import { redirect } from "react-router";

import { apiFetch } from "../api";

export interface KeywordRollover {
  enabled: boolean;
  state: "off" | "working" | "draining" | "ready";
  label: string;
  detail: string;
  percent_complete: number;
  posted_jobs: number | null;
  expected_jobs: number | null;
  last_status: "generated" | "error" | null;
  last_event: string | null;
}

export interface KeywordWinner {
  new_phone_count?: number;
  new_phones_per_minute?: number;
  rank: number;
  keyword: string;
  phone_businesses: number;
  businesses: number;
  posted_cells: number;
  phones_per_cell: number;
  phone_rate: number;
  last_used: string;
}

export interface KeywordOutcomePerformance {
  keyword: string;
  delivered: number;
  positive: number;
  positives_per_delivered: number | null;
}

export interface KeywordWorkspace {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  current: string[];
  version: string;
  ai_enabled: boolean;
  rollover: KeywordRollover;
  winners: KeywordWinner[];
  performance?: KeywordOutcomePerformance[];
  prescriptive_mode?: "dormant_worked_leads";
}

export interface KeywordDiff {
  proposed: string[];
  added: string[];
  removed: string[];
  unchanged: string[];
  expected_version: string;
  review_token: string;
}

export interface KeywordSaveResult {
  saved: true;
  enqueued: boolean;
  current: string[];
  version: string;
  diff: KeywordDiff;
}

export interface KeywordGenerationDraft {
  generation_id: string;
  mode: "broad" | "adjacent";
  seed_keyword: string | null;
  keywords: string[];
  excluded_count: number;
  notice: string;
}

export class KeywordRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(path, init);
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string;
  };
  if (!response.ok) {
    throw new KeywordRequestError(
      body.detail || "The keyword request could not be completed.",
      response.status,
    );
  }
  return body as T;
}

export function fetchKeywordWorkspace(): Promise<KeywordWorkspace> {
  return request<KeywordWorkspace>("/api/admin/scraper/keywords");
}

export async function scraperKeywordsLoader(): Promise<KeywordWorkspace> {
  try {
    return await fetchKeywordWorkspace();
  } catch (error) {
    if (error instanceof KeywordRequestError && error.status === 401) {
      throw redirect("/admin/acquisition/scraper");
    }
    throw error;
  }
}

export function previewKeywords(text: string): Promise<KeywordDiff> {
  return request<KeywordDiff>("/api/admin/scraper/keywords/preview", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function saveKeywords(input: {
  text: string;
  expected_version: string;
  review_token: string;
  enqueue: boolean;
  generation_id?: string;
}): Promise<KeywordSaveResult> {
  return request<KeywordSaveResult>("/api/admin/scraper/keywords/save", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function generateKeywords(
  mode: "broad" | "adjacent",
  seedKeyword?: string,
): Promise<KeywordGenerationDraft> {
  return request<KeywordGenerationDraft>(
    "/api/admin/scraper/keywords/generate",
    {
      method: "POST",
      body: JSON.stringify({
        mode,
        seed_keyword: seedKeyword ?? null,
      }),
    },
  );
}

export function setKeywordRollover(
  action: "enable" | "disable",
): Promise<KeywordRollover> {
  return request<KeywordRollover>(
    "/api/admin/scraper/keywords/rollover",
    {
      method: "POST",
      body: JSON.stringify({ action }),
    },
  );
}
