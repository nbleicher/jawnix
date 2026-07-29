import { redirect } from "react-router";

export type HistorySort =
  | "keyword"
  | "state"
  | "last_enqueued"
  | "cells_posted"
  | "latest_enqueued";
export type SortDirection = "asc" | "desc";

export interface CampaignHistoryRow {
  keyword: string;
  state: string;
  cells_posted: number;
  first_enqueued: string | null;
  latest_enqueued: string | null;
  campaign_date: string;
}

export interface CampaignHistory {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  search: string;
  state: string;
  sort: HistorySort;
  direction: SortDirection;
  all_states: string[];
  rows: CampaignHistoryRow[];
}

export interface RuntimeSettings {
  zoom: number;
  radius: number;
  depth: number;
  lang: string;
  fast_mode: boolean;
  timeout: number;
}

export interface QueueSettings {
  target_depth: number;
  target_per_worker: number;
  min_target_depth: number;
  max_target_depth: number;
  batch_size: number;
  poll_secs: number;
  skip_recent_days: number;
}

export interface StateOverride {
  cell_size_km?: number;
  zoom?: number;
}

export interface RuntimeConfiguration {
  states: string[];
  settings: RuntimeSettings;
  queue: QueueSettings;
  overrides: Record<string, StateOverride>;
}

export interface FieldBounds {
  minimum: number;
  maximum: number;
  step: number;
}

export interface RuntimeBounds {
  runtime: Record<
    Exclude<keyof RuntimeSettings, "lang" | "fast_mode">,
    FieldBounds
  >;
  queue: Record<keyof QueueSettings, FieldBounds>;
  override: {
    cell_size_km: FieldBounds;
    zoom: FieldBounds;
  };
  language_max_length: number;
}

export interface StateCellEffect {
  state: string;
  cells: number;
}

export interface RuntimeWorkspace {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  current: RuntimeConfiguration;
  version: string;
  all_states: string[];
  cells: StateCellEffect[];
  total_cells: number;
  bounds: RuntimeBounds;
}

export interface RuntimeEffects {
  cells: StateCellEffect[];
  current_total_cells: number;
  proposed_total_cells: number;
  total_cell_delta: number;
  states_added: string[];
  states_removed: string[];
  runtime_changes: string[];
  queue_changes: string[];
  override_changes: string[];
}

export interface RuntimePreview {
  configuration: RuntimeConfiguration;
  expected_version: string;
  proposed_version: string;
  review_token: string;
  effects: RuntimeEffects;
}

export interface RuntimeSaveResult {
  revision_id: string;
  version: string;
  configuration: RuntimeConfiguration;
  effects: RuntimeEffects;
  enqueued: boolean;
}

export class ScraperRuntimeRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.method && init.method !== "GET"
        ? { "X-CSRF-Token": csrf() }
        : {}),
      ...init.headers,
    },
  });
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string | Array<{ msg?: string }>;
  };
  if (!response.ok) {
    const validation = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).filter(Boolean).join(" ")
      : body.detail;
    throw new ScraperRuntimeRequestError(
      validation || "The Scraper request could not be completed.",
      response.status,
    );
  }
  return body as T;
}

export function fetchCampaignHistory(
  filters: Partial<{
    search: string;
    state: string;
    sort: HistorySort;
    direction: SortDirection;
  }> = {},
): Promise<CampaignHistory> {
  const params = new URLSearchParams();
  if (filters.search) params.set("search", filters.search);
  if (filters.state) params.set("state", filters.state);
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.direction) params.set("direction", filters.direction);
  const query = params.toString();
  return request<CampaignHistory>(
    `/api/admin/scraper/history${query ? `?${query}` : ""}`,
  );
}

export async function scraperCampaignHistoryLoader({
  request: loaderRequest,
}: {
  request: Request;
}): Promise<CampaignHistory> {
  const query = new URL(loaderRequest.url).searchParams;
  try {
    return await fetchCampaignHistory({
      search: query.get("search") ?? "",
      state: query.get("state") ?? "",
      sort: (query.get("sort") as HistorySort | null) ?? "last_enqueued",
      direction:
        (query.get("direction") as SortDirection | null) ?? "desc",
    });
  } catch (error) {
    if (
      error instanceof ScraperRuntimeRequestError &&
      error.status === 401
    ) {
      throw redirect("/admin/acquisition/scraper");
    }
    throw error;
  }
}

export function fetchRuntimeWorkspace(): Promise<RuntimeWorkspace> {
  return request<RuntimeWorkspace>("/api/admin/scraper/runtime");
}

export async function scraperRuntimeLoader(): Promise<RuntimeWorkspace> {
  try {
    return await fetchRuntimeWorkspace();
  } catch (error) {
    if (
      error instanceof ScraperRuntimeRequestError &&
      error.status === 401
    ) {
      throw redirect("/admin/acquisition/scraper");
    }
    throw error;
  }
}

export function previewRuntimeConfiguration(
  configuration: RuntimeConfiguration,
): Promise<RuntimePreview> {
  return request<RuntimePreview>("/api/admin/scraper/runtime/preview", {
    method: "POST",
    body: JSON.stringify({ configuration }),
  });
}

export function saveRuntimeConfiguration(input: {
  configuration: RuntimeConfiguration;
  expected_version: string;
  review_token: string;
  enqueue: boolean;
  reason: string;
}): Promise<RuntimeSaveResult> {
  return request<RuntimeSaveResult>("/api/admin/scraper/runtime/save", {
    method: "POST",
    body: JSON.stringify(input),
  });
}
