export function utcDay(nowMs: number): string {
  return new Date(nowMs).toISOString().slice(0, 10);
}

export function nextUtcMidnight(nowMs: number): number {
  const date = new Date(nowMs);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + 1);
}

export function secondsUntilUtcMidnight(nowMs: number): number {
  return Math.max(1, Math.ceil((nextUtcMidnight(nowMs) - nowMs) / 1000));
}

export function resetsAtIso(nowMs: number): string {
  return new Date(nextUtcMidnight(nowMs)).toISOString();
}
