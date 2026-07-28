import { redirect } from "react-router";

/**
 * The native Scraper Operations monitoring contract (#63).
 *
 * Regions are addressed individually on purpose: each refreshes at its own
 * cadence and fails on its own, which is what keeps one dark panel from
 * blanking the other eight.
 */

export type RegionKey =
  | "overall"
  | "stack"
  | "stats"
  | "activity"
  | "log"
  | "workers"
  | "trends"
  | "incidents"
  | "top-states";

export interface StackStatus {
  key: "operational" | "idle" | "attention" | "stale";
  label: string;
  detail: string;
  reasons: string[];
  age_seconds: number | null;
}

export interface ServiceRow {
  key: string;
  label: string;
  state: "ok" | "neutral" | "bad";
  detail: string;
}

export interface StackSample {
  captured_at: string;
  cpu_percent: number | null;
  load_1: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  memory_percent: number | null;
  disk_used_bytes: number | null;
  disk_total_bytes: number | null;
  disk_percent: number | null;
  host_uptime_seconds: number | null;
  uptime_label: string | null;
  spool_pending_files: number | null;
  spool_oldest_seconds: number | null;
  spool_age_label: string | null;
  worker_restarts: number | null;
  expected_workers: number | null;
  running_workers: number | null;
  unhealthy_workers: number | null;
  database_ok: boolean | null;
  dashboard_ok: boolean | null;
  queue_api_ok: boolean | null;
  required_services_ok: boolean | null;
  queue_depth: number | null;
  running_jobs: number | null;
  retryable_jobs: number | null;
  oldest_queue_seconds: number | null;
  businesses_total: number | null;
  completed_jobs_total: number | null;
  empty_rate_1h: number | null;
}

export interface DashboardStats {
  businesses: number;
  phone_businesses: number;
  unique_phones: number;
  leads: number;
  available_leads: number;
  queue_depth: number;
  running_jobs: number;
  retryable_jobs: number;
  oldest_queue_secs: number;
  added_last_hour: number;
  empty_rate: number;
}

export interface PipelineActivity {
  queue_depth: number;
  running_jobs: number;
  retryable_jobs: number;
  jobs_last_minute: number;
  jobs_last_five_minutes: number;
  results_last_minute: number;
  latest_result_at: string | null;
  businesses_last_minute: number;
  businesses_last_five_minutes: number;
  businesses_total: number;
  latest_business_at: string | null;
  latest_keyword: string | null;
  latest_state: string | null;
  latest_result_count: number | null;
  latest_job_at: string | null;
  healthy_workers: number;
  latest_write_at: string | null;
  write_age: string;
  write_is_fresh: boolean;
}

export interface PipelineState {
  key: "pausing" | "paused" | "running" | "stopped";
  label: string;
  detail: string;
}

export interface PauseInfo {
  mode: string;
  cancelled_jobs: number;
}

export interface PipelineEvent {
  job_id: number;
  created_at: string;
  keyword: string;
  state: string;
  result_count: number | null;
  phone_count: number | null;
}

export interface Worker {
  box_id: string;
  container_name: string;
  reported_at: string;
  heartbeat_age: string;
  is_healthy: boolean;
  status: string;
  active_jobs: number | null;
  jobs_processed: number | null;
  results_per_min: number | null;
  current_state: string | null;
  current_keyword: string | null;
  current_job_id: number | null;
}

export interface TrendBucket {
  label: string;
  businesses: number;
  jobs: number;
  queue: number;
  cpu: number;
  memory: number;
  businesses_height: number;
  jobs_height: number;
  queue_height: number;
}

export interface Incident {
  checked_at: string;
  status: string;
  messages: string[];
}

export interface TopState {
  state: string;
  businesses: number;
}

export interface RegionData {
  stack_status?: StackStatus | null;
  sample?: StackSample | null;
  services?: ServiceRow[] | null;
  stats?: DashboardStats | null;
  activity?: PipelineActivity | null;
  pipeline_state?: PipelineState | null;
  pause_info?: PauseInfo | null;
  pipeline_events?: PipelineEvent[] | null;
  workers?: Worker[] | null;
  expected_workers?: number | null;
  trends?: TrendBucket[] | null;
  incidents?: Incident[] | null;
  top_states?: TopState[] | null;
}

export interface MonitoringRegion {
  region: RegionKey;
  state: "ok" | "unavailable";
  refresh_seconds: number;
  fetched_at: string | null;
  data: RegionData | null;
}

export interface MonitoringSnapshot {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  regions: MonitoringRegion[];
}

export interface PipelineResult {
  ok: boolean;
  pipeline_state: string;
  cancelled_jobs: number;
  region: MonitoringRegion;
}

export interface PipelineCommand {
  action: "pause" | "resume";
  clear_queue?: boolean;
  reason: string;
}

const BASE = "/api/admin/scraper/monitoring";

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

/** The privileged session is gone; the step-up gate owns what happens next. */
export class PrivilegedSessionExpired extends Error {
  constructor() {
    super("The Scraper privileged session has expired.");
    this.name = "PrivilegedSessionExpired";
  }
}

export async function scraperOverviewLoader(): Promise<MonitoringSnapshot> {
  const response = await fetch(BASE, {
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf() },
  });
  if (response.status === 401 || response.status === 403) {
    throw redirect("/admin/acquisition/scraper");
  }
  if (!response.ok) {
    throw new Response("Scraper Operations could not be loaded.", {
      status: response.status,
    });
  }
  return response.json() as Promise<MonitoringSnapshot>;
}

/**
 * Refresh one region.
 *
 * An upstream region being down is not an error here — it comes back as a
 * well-formed region reporting `unavailable`, so the caller keeps whatever it
 * was already showing. Only losing the privileged session throws.
 */
export async function fetchRegion(
  region: RegionKey,
): Promise<MonitoringRegion> {
  const response = await fetch(`${BASE}/${region}`, {
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf() },
  });
  if (response.status === 401 || response.status === 403) {
    throw new PrivilegedSessionExpired();
  }
  if (!response.ok) {
    throw new Error("The region could not be refreshed.");
  }
  return response.json() as Promise<MonitoringRegion>;
}

export async function controlPipeline(
  command: PipelineCommand,
): Promise<PipelineResult> {
  const response = await fetch("/api/admin/scraper/pipeline", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    body: JSON.stringify(command),
  });
  if (response.status === 401 || response.status === 403) {
    throw new PrivilegedSessionExpired();
  }
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string | { msg?: string }[];
  };
  if (!response.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : Array.isArray(body.detail)
          ? body.detail[0]?.msg
          : undefined;
    throw new Error(detail || "The pipeline action could not be completed.");
  }
  return body as unknown as PipelineResult;
}
