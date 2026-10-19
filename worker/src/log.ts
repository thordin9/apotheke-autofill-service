/** One JSON line per request. Wall-clock timings, not Workers CPU time.
 * Callers must pass only these fields: never the token, image, or field values.
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
    total_ms: round(performance.now() - started),
    neurons: timing.neurons,
    model: timing.model,
    ai_calls: timing.aiCalls,
  };
  console.log(JSON.stringify(line));
}
