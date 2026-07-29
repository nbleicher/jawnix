import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";

export interface DatabaseTotals {
  businesses: number;
  unique_phones: number;
}

export interface DatabaseStateSummary extends DatabaseTotals {
  state: string;
  niches: number;
}

export interface DatabaseBusiness {
  title: string;
  phone: string | null;
  website: string | null;
  state: string | null;
  niche: string | null;
  last_seen: string;
}

export interface DatabaseBrowsePage {
  records: DatabaseBusiness[];
  search: string;
  state: string;
  page: number;
  page_size: number;
  total: number;
  pages: number;
  has_previous: boolean;
  has_next: boolean;
}

export interface StoredExport {
  filename: string;
  size_label: string;
}

export interface DatabaseWorkspace {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  totals: DatabaseTotals | null;
  states: DatabaseStateSummary[];
  browse: DatabaseBrowsePage | null;
  stored_exports: StoredExport[];
}

export interface DatabaseNiche extends DatabaseTotals {
  key: string;
  label: string;
}

export interface DatabaseStateDetail {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  state: string;
  totals: DatabaseStateSummary | null;
  niches: DatabaseNiche[];
}

export interface ExportRegeneration {
  generated: string;
  stored_exports: StoredExport[];
}

const BASE = "/api/admin/scraper/database";

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

async function read<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf() },
  });
  if (response.status === 401 || response.status === 403) {
    throw redirect("/admin/acquisition/scraper");
  }
  if (!response.ok) {
    throw new Response("Scraper database could not be loaded.", {
      status: response.status,
    });
  }
  return response.json() as Promise<T>;
}

export async function scraperDatabaseLoader({
  request,
}: LoaderFunctionArgs): Promise<DatabaseWorkspace> {
  const source = new URL(request.url).searchParams;
  const query = new URLSearchParams();
  const search = source.get("search")?.trim() ?? "";
  const state = source.get("state")?.trim().toUpperCase() ?? "";
  const page = source.get("page") ?? "1";
  if (search) query.set("search", search);
  if (state) query.set("state", state);
  query.set("page", page);
  return read<DatabaseWorkspace>(`${BASE}?${query.toString()}`);
}

export async function scraperDatabaseStateLoader({
  params,
}: LoaderFunctionArgs): Promise<DatabaseStateDetail> {
  return read<DatabaseStateDetail>(
    `${BASE}/states/${encodeURIComponent(params.state?.toUpperCase() ?? "")}`,
  );
}

export function regenerateStoredExports(
  state: string,
): Promise<ExportRegeneration> {
  return api<ExportRegeneration>(
    `${BASE}/exports/${encodeURIComponent(state)}/regenerate`,
    { method: "POST" },
  );
}

export function stateExportHref(
  state: string,
  niches: string[] | null = null,
): string {
  const query = new URLSearchParams();
  query.set("scope", niches === null ? "all" : "selected");
  for (const niche of niches ?? []) query.append("niche", niche);
  return `${BASE}/exports/state/${encodeURIComponent(state)}?${query.toString()}`;
}

export function multiStateExportHref(states: string[]): string {
  const query = new URLSearchParams();
  for (const state of states) query.append("state", state);
  return `${BASE}/exports/states?${query.toString()}`;
}

export function storedExportHref(filename: string): string {
  return `${BASE}/exports/stored/${encodeURIComponent(filename)}`;
}
