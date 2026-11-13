import { HttpError } from "./http";

/** Header names a hosted Apotheke sends (docs/api-v1.md, "Per-installation budget"). */
export const INSTALLATION_HEADER = "x-apotheke-installation";
export const LIMIT_HEADER = "x-apotheke-autofill-limit";
export const SOFT_HEADER = "x-apotheke-autofill-soft";

export const LIMIT_MESSAGE = "Autofill limit reached for this month";
export const WARNING_MESSAGE = "Autofill is close to this month's limit";

export interface InstallationBudget {
  id: string;
  hard: number | null;
  soft: number;
}

function count(raw: string | null, name: string): number | null {
  if (raw === null) return null;
  const value = raw.trim();
  if (!/^\d{1,15}$/.test(value)) throw new HttpError(400, "bad_request", `invalid ${name}`);
  return Number(value);
}

/** The caller's installation and its monthly limit, or null without an installation id (today's behaviour). */
export function installationBudget(request: Request): InstallationBudget | null {
  const id = request.headers.get(INSTALLATION_HEADER);
  if (id === null) return null;
  if (id.length > 63 || !/^[a-z0-9]+(-[a-z0-9]+)*$/.test(id)) {
    throw new HttpError(400, "bad_request", `invalid ${INSTALLATION_HEADER}`);
  }
  const hard = count(request.headers.get(LIMIT_HEADER), LIMIT_HEADER);
  const soft = count(request.headers.get(SOFT_HEADER), SOFT_HEADER);
  return { id, hard, soft: soft ?? (hard === null ? Infinity : hard * 0.8) };
}
