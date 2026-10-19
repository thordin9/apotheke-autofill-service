/** One JSON line per request. *_ms values are Date.now() wall-clock deltas.
 * Workers freezes that clock during CPU, so a CPU-only slice can stay 0.
 * A delta that crosses an await, including total_ms, still moves. Workers
 * Logs `cpuTime` (and `wallTime`) is the CPU measurement. Callers must pass
 * only these fields: never the token, image, or field values.
 */

export interface RequestTiming {
  base64Ms: number;
  parseMs: number;
  validateMs: number;
  neurons: number;
  model: string;
  aiCalls: number;
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function logRequest(status: number, started: number, timing: RequestTiming): void {
  const line = {
    level: "info",
    status,
    base64_ms: round(timing.base64Ms),
    parse_ms: round(timing.parseMs),
    validate_ms: round(timing.validateMs),
    total_ms: round(Date.now() - started),
    neurons: timing.neurons,
    model: timing.model,
    ai_calls: timing.aiCalls,
  };
  console.log(JSON.stringify(line));
}
