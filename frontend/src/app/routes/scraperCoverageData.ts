import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { PrivilegedSessionExpired } from "./scraperMonitoring";
import { apiFetch, csrfToken } from "../api";

export type CoverageStatus = "covered" | "partial" | "uncovered";
export type CellStatus = "posted" | "reserved" | "failed" | "uncovered";

export interface StateCoverageCard {
  state: string;
  businesses: number;
  posted_cells: number;
  total_cells: number;
  active_keywords: number;
  coverage: number;
  status: CoverageStatus;
}

export interface StateKeywordActivity {
  keyword: string;
  businesses: number;
  posted_cells: number;
  total_cells: number;
  coverage: number;
  empty_rate: number;
  last_enqueued: string | null;
}

export interface StateGridCell {
  index: number;
  cell: string;
  status: CellStatus;
}

export interface StateGridCoverage {
  cells: StateGridCell[];
  posted: number;
  reserved: number;
  failed: number;
  uncovered: number;
}

export interface CoverageFeed<T> {
  state: "ok" | "unavailable";
  refresh_seconds: number;
  fetched_at: string | null;
  data: T | null;
}

export interface StateCoverageSnapshot {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  states: CoverageFeed<StateCoverageCard[]>;
}

export interface StateCoverageDetail {
  state: string;
  service_state: "connected" | "degraded" | "unavailable";
  last_successful_at: string | null;
  keywords: CoverageFeed<StateKeywordActivity[]>;
  cells: CoverageFeed<StateGridCoverage>;
}

const BASE = "/api/admin/scraper/coverage";

async function read<T>(path: string): Promise<T> {
  const response = await apiFetch(path, {
    headers: { "X-CSRF-Token": csrfToken() },
  });
  if (response.status === 401 || response.status === 403) {
    throw new PrivilegedSessionExpired();
  }
  if (!response.ok) {
    throw new Error("Scraper state coverage could not be refreshed.");
  }
  return response.json() as Promise<T>;
}

export async function stateCoverageLoader(): Promise<StateCoverageSnapshot> {
  try {
    return await read<StateCoverageSnapshot>(BASE);
  } catch (caught) {
    if (caught instanceof PrivilegedSessionExpired) {
      throw redirect("/admin/acquisition/scraper");
    }
    throw new Response("Scraper state coverage could not be loaded.", {
      status: 502,
    });
  }
}

export async function stateCoverageDetailLoader({
  params,
}: LoaderFunctionArgs): Promise<StateCoverageDetail> {
  const state = params.state?.toUpperCase() ?? "";
  try {
    return await read<StateCoverageDetail>(
      `${BASE}/${encodeURIComponent(state)}`,
    );
  } catch (caught) {
    if (caught instanceof PrivilegedSessionExpired) {
      throw redirect("/admin/acquisition/scraper");
    }
    throw new Response("Scraper state coverage could not be loaded.", {
      status: 502,
    });
  }
}

export function fetchStateCards(): Promise<
  StateCoverageSnapshot
> {
  return read<StateCoverageSnapshot>(BASE);
}

export function fetchStateKeywords(
  state: string,
): Promise<CoverageFeed<StateKeywordActivity[]>> {
  return read<CoverageFeed<StateKeywordActivity[]>>(
    `${BASE}/${encodeURIComponent(state)}/keywords`,
  );
}

export function fetchStateCells(
  state: string,
): Promise<CoverageFeed<StateGridCoverage>> {
  return read<CoverageFeed<StateGridCoverage>>(
    `${BASE}/${encodeURIComponent(state)}/cells`,
  );
}
