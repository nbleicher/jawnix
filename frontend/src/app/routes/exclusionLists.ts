/** Client for typed Exclusion List uploads (#153).
 *
 * Uploader-scoped protection is permanent once ingestion completes; an
 * administrator decision controls only the additional global effect. The
 * customer-facing vocabulary here therefore never promises or revokes
 * global scope — a denied list still protects its uploader.
 */

export interface ExclusionListStatus {
  id: string;
  type: string;
  filename: string;
  status: string;
  totalRows: number;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  poolImpact: number;
  global: boolean;
  error: string;
  createdAt: string;
  ingestedAt: string | null;
  decidedAt: string | null;
}

export const EXCLUSION_TYPES = [
  // Mixed leads: real customer files hold landline, DNC, and TCPA phones in
  // one CSV, so the blended type is the default and the exclusive types are
  // for customers who do keep separated lists.
  { value: "mixed", label: "Mixed (landline, DNC, TCPA litigator)" },
  { value: "landline", label: "Landline" },
  { value: "dnc", label: "DNC registry" },
  { value: "tcpa_litigator", label: "TCPA litigator" },
] as const;

export function exclusionTypeLabel(type: string): string {
  return (
    EXCLUSION_TYPES.find((item) => item.value === type)?.label ??
    type.replaceAll("_", " ")
  );
}

export const INGESTING_STATUSES = ["queued", "ingesting"];

export class ExclusionListRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

interface ErrorBody {
  detail?: string | { msg?: string }[];
}

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

function refusal(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  const first = Array.isArray(body.detail) ? body.detail[0]?.msg : undefined;
  return first ?? fallback;
}

async function parse<T>(response: Response, fallback: string): Promise<T> {
  const body = (await response.json().catch(() => ({}))) as ErrorBody;
  if (!response.ok) {
    throw new ExclusionListRequestError(
      refusal(body, fallback),
      response.status,
    );
  }
  return body as T;
}

export async function listMyExclusionLists(): Promise<ExclusionListStatus[]> {
  const response = await fetch("/api/me/exclusion-lists", {
    credentials: "same-origin",
  });
  const body = await parse<unknown>(
    response,
    "Your Exclusion Lists could not be loaded.",
  );
  return Array.isArray(body) ? (body as ExclusionListStatus[]) : [];
}

export async function uploadMyExclusionList(
  file: File,
  type: string,
): Promise<ExclusionListStatus> {
  const body = new FormData();
  body.append("file", file);
  body.append("type", type);
  const response = await fetch("/api/me/exclusion-lists", {
    method: "POST",
    headers: { "X-CSRF-Token": csrf() },
    credentials: "same-origin",
    body,
  });
  return parse(response, "The Exclusion List could not be uploaded.");
}
