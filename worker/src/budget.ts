import { DurableObject } from "cloudflare:workers";

import { resetsAtIso, secondsUntilUtcMidnight, utcDay } from "./time";

interface BudgetRow {
  day: string;
  used: number;
  reserved: number;
  requests: number;
  requests_reserved: number;
  exhausted: number;
}

export interface ReserveInput {
  nowMs: number;
  neuronEstimate: number;
  neuronBudget: number;
  /** 0 means no request cap. */
  requestCap: number;
  countRequest: boolean;
}

export interface ReserveResult {
  ok: boolean;
  retryAfter: number;
  resetsAt: string;
}

export interface SettleInput {
  nowMs: number;
  neuronEstimate: number;
  neuronActual: number;
}

export interface BudgetSnapshot {
  day: string;
  used: number;
  reserved: number;
  requests: number;
  requestsReserved: number;
  exhausted: boolean;
  resetsAt: string;
  retryAfter: number;
}

/** One SQLite Durable Object for the whole Worker (`idFromName("global")`).
 * Requests are serialized on this instance, so reserve cannot overshoot.
 */
export class Budget extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      this.ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS budget (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          day TEXT NOT NULL,
          used REAL NOT NULL,
          reserved REAL NOT NULL,
          requests INTEGER NOT NULL,
          requests_reserved INTEGER NOT NULL,
          exhausted INTEGER NOT NULL
        )
      `);
    });
  }

  async reserve(input: ReserveInput): Promise<ReserveResult> {
    const row = this.ensure(input.nowMs);
    const denied: ReserveResult = {
      ok: false,
      retryAfter: secondsUntilUtcMidnight(input.nowMs),
      resetsAt: resetsAtIso(input.nowMs),
    };
    if (row.exhausted) return denied;
    if (input.requestCap > 0 && input.countRequest && row.requests + 1 > input.requestCap) return denied;
    if (row.used + row.reserved + input.neuronEstimate > input.neuronBudget + 1e-6) return denied;
    // The request is counted at admission. Neuron estimate stays in `reserved`
    // until settle, so a crash still holds the cap.
    this.ctx.storage.sql.exec(
      "UPDATE budget SET reserved = ?, requests = ? WHERE id = 1",
      row.reserved + input.neuronEstimate,
      row.requests + (input.countRequest ? 1 : 0),
    );
    return { ok: true, retryAfter: 0, resetsAt: resetsAtIso(input.nowMs) };
  }

  async settle(input: SettleInput): Promise<{ ok: true }> {
    const row = this.ensure(input.nowMs);
    this.ctx.storage.sql.exec(
      "UPDATE budget SET used = ?, reserved = ? WHERE id = 1",
      row.used + input.neuronActual,
      Math.max(0, row.reserved - input.neuronEstimate),
    );
    return { ok: true };
  }

  async markExhausted(nowMs: number): Promise<{ ok: true }> {
    this.ensure(nowMs);
    this.ctx.storage.sql.exec("UPDATE budget SET exhausted = 1 WHERE id = 1");
    return { ok: true };
  }

  async snapshot(nowMs: number): Promise<BudgetSnapshot> {
    const row = this.ensure(nowMs);
    return {
      day: row.day,
      used: row.used,
      reserved: row.reserved,
      requests: row.requests,
      requestsReserved: row.requests_reserved,
      exhausted: row.exhausted !== 0,
      resetsAt: resetsAtIso(nowMs),
      retryAfter: secondsUntilUtcMidnight(nowMs),
    };
  }

  /** Test helper. Not an HTTP route. */
  async reset(): Promise<{ ok: true }> {
    this.ctx.storage.sql.exec("DELETE FROM budget");
    return { ok: true };
  }

  private ensure(nowMs: number): BudgetRow {
    const day = utcDay(nowMs);
    const existing = this.ctx.storage.sql
      .exec(
        "SELECT day, used, reserved, requests, requests_reserved, exhausted FROM budget WHERE id = 1",
      )
      .toArray() as unknown as BudgetRow[];
    if (existing.length === 0) {
      this.ctx.storage.sql.exec(
        "INSERT INTO budget (id, day, used, reserved, requests, requests_reserved, exhausted) VALUES (1, ?, 0, 0, 0, 0, 0)",
        day,
      );
      return { day, used: 0, reserved: 0, requests: 0, requests_reserved: 0, exhausted: 0 };
    }
    const row = existing[0];
    // Roll forward only. A clock step backwards (stale RTC before NTP) keeps
    // the stored counter so the day does not start over at zero.
    if (row.day < day) {
      this.ctx.storage.sql.exec(
        "UPDATE budget SET day = ?, used = 0, reserved = 0, requests = 0, requests_reserved = 0, exhausted = 0 WHERE id = 1",
        day,
      );
      return { day, used: 0, reserved: 0, requests: 0, requests_reserved: 0, exhausted: 0 };
    }
    return row;
  }
}
