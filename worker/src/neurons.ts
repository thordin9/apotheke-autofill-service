/** Gemma 4 26B A4B rates from the Workers AI pricing page, neurons per million tokens. */
export const INPUT_NEURONS_PER_MILLION = 9091;
export const OUTPUT_NEURONS_PER_MILLION = 27273;

/** Reserved before each AI call, and charged when the reply has no usage. */
export const NEURON_ESTIMATE = 30;

export const MODEL = "@cf/google/gemma-4-26b-a4b-it";

export type NeuronSource = "usage" | "rates" | "estimate";

export interface NeuronCharge {
  neurons: number;
  source: NeuronSource;
}

function nonNegative(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function neuronsFromUsage(usage: unknown): NeuronCharge {
  if (usage && typeof usage === "object") {
    const row = usage as Record<string, unknown>;
    const direct = nonNegative(row.neurons);
    if (direct !== null) return { neurons: direct, source: "usage" };
    const prompt = nonNegative(row.prompt_tokens);
    const completion = nonNegative(row.completion_tokens);
    if (prompt !== null && completion !== null) {
      const neurons =
        (prompt * INPUT_NEURONS_PER_MILLION + completion * OUTPUT_NEURONS_PER_MILLION) / 1_000_000;
      return { neurons, source: "rates" };
    }
    const total = nonNegative(row.total_tokens);
    if (total !== null) {
      // No input/output split: use the higher (output) rate so the charge is not low.
      return { neurons: (total * OUTPUT_NEURONS_PER_MILLION) / 1_000_000, source: "rates" };
    }
  }
  return { neurons: NEURON_ESTIMATE, source: "estimate" };
}

export const DEFAULT_NEURON_BUDGET = 6000;
export const DEFAULT_MAX_UPLOAD_BYTES = 3 * 1024 * 1024;

export function parseNeuronBudget(raw: string | undefined): number | null {
  if (raw === undefined || raw.trim() === "") return DEFAULT_NEURON_BUDGET;
  if (!/^\d+$/.test(raw.trim())) return null;
  const value = Number(raw.trim());
  if (!Number.isSafeInteger(value)) return null;
  return value;
}

/** Empty or 0: no request cap. A non-integer is misconfigured. */
export function parseRequestCap(raw: string | undefined): number | null | "bad" {
  if (raw === undefined || raw.trim() === "" || raw.trim() === "0") return null;
  if (!/^\d+$/.test(raw.trim())) return "bad";
  const value = Number(raw.trim());
  if (!Number.isSafeInteger(value)) return "bad";
  return value;
}

export function parseMaxUpload(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === "") return DEFAULT_MAX_UPLOAD_BYTES;
  if (!/^\d+$/.test(raw.trim())) return DEFAULT_MAX_UPLOAD_BYTES;
  const value = Number(raw.trim());
  if (!Number.isSafeInteger(value) || value <= 0) return DEFAULT_MAX_UPLOAD_BYTES;
  return value;
}
