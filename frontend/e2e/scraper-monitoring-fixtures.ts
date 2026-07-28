/**
 * Monitoring payloads shaped exactly like the native contract, so a browser
 * test exercises the real region envelope rather than a convenient stand-in.
 */

const CADENCE = {
  log: 2,
  activity: 3,
  stats: 10,
  overall: 15,
  stack: 15,
  workers: 15,
  "top-states": 30,
  trends: 60,
  incidents: 60,
} as const;

export type RegionKey = keyof typeof CADENCE;

const DATA: Record<RegionKey, unknown> = {
  overall: {
    stack_status: {
      key: "attention",
      label: "Attention needed",
      detail: "Only 6 of 8 workers are running",
      reasons: [
        "Only 6 of 8 workers are running",
        "Queue depth exceeds its warning threshold",
      ],
      age_seconds: 30,
    },
  },
  stack: {
    sample: {
      captured_at: "2026-07-28T11:59:30Z",
      cpu_percent: 42.5,
      memory_percent: 75,
      disk_percent: 80,
      uptime_label: "11d 0h",
      spool_pending_files: 12,
      spool_age_label: "1m",
    },
    services: [
      { key: "postgres", label: "PostgreSQL", state: "ok", detail: "healthy" },
      {
        key: "gms-enqueue.service",
        label: "Enqueuer",
        state: "bad",
        detail: "failed",
      },
    ],
  },
  stats: {
    stats: {
      businesses: 9244326,
      phone_businesses: 4588286,
      unique_phones: 2305025,
      leads: 0,
      available_leads: 0,
      queue_depth: 812,
      running_jobs: 6,
      retryable_jobs: 4,
      oldest_queue_secs: 1200,
      added_last_hour: 4215,
      empty_rate: 0.18,
    },
  },
  activity: {
    activity: {
      queue_depth: 812,
      running_jobs: 6,
      retryable_jobs: 4,
      jobs_last_minute: 22,
      jobs_last_five_minutes: 118,
      results_last_minute: 640,
      latest_result_at: null,
      businesses_last_minute: 310,
      businesses_last_five_minutes: 1602,
      businesses_total: 9244326,
      latest_business_at: null,
      latest_keyword: "dentist",
      latest_state: "PA",
      latest_result_count: 20,
      latest_job_at: null,
      healthy_workers: 6,
      latest_write_at: null,
      write_age: "2s",
      write_is_fresh: true,
    },
    pipeline_state: {
      key: "running",
      label: "Running",
      detail: "Workers are processing the queue",
    },
    pause_info: { mode: "", cancelled_jobs: 0 },
  },
  log: {
    pipeline_events: [
      {
        job_id: 5001,
        created_at: "2026-07-28T11:59:40Z",
        keyword: "dentist",
        state: "PA",
        result_count: 20,
        phone_count: 17,
      },
    ],
  },
  workers: {
    expected_workers: 8,
    workers: [
      {
        box_id: "box-1",
        container_name: "gms-worker-1",
        reported_at: "2026-07-28T11:59:50Z",
        heartbeat_age: "10s",
        is_healthy: true,
        status: "alive",
        active_jobs: 1,
        jobs_processed: 4210,
        results_per_min: 18.5,
        current_state: "PA",
        current_keyword: "dentist",
        current_job_id: 5001,
      },
    ],
  },
  trends: {
    trends: Array.from({ length: 24 }, (_, hour) => ({
      label: `${String(hour).padStart(2, "0")}:00`,
      businesses: 100,
      jobs: 20,
      queue: 400,
      cpu: 40,
      memory: 70,
      businesses_height: 80,
      jobs_height: 60,
      queue_height: 50,
    })),
  },
  incidents: {
    incidents: [
      {
        checked_at: "2026-07-28T11:30:00Z",
        status: "error",
        messages: ["Queue depth exceeds its warning threshold"],
      },
    ],
  },
  "top-states": {
    top_states: [
      { state: "TX", businesses: 1204002 },
      { state: "PA", businesses: 980411 },
    ],
  },
};

export function monitoringRegion(
  region: RegionKey,
  overrides: Record<string, unknown> = {},
) {
  return {
    region,
    state: "ok",
    refresh_seconds: CADENCE[region],
    fetched_at: "2026-07-28T12:00:00Z",
    data: DATA[region],
    ...overrides,
  };
}

export function monitoringSnapshot(
  overrides: Record<string, unknown> = {},
  unavailable: RegionKey[] = [],
) {
  return {
    service_state: "connected",
    last_successful_at: "2026-07-28T12:00:00Z",
    idle_expires_in: 900,
    regions: (Object.keys(CADENCE) as RegionKey[]).map((region) =>
      unavailable.includes(region)
        ? monitoringRegion(region, { state: "unavailable" })
        : monitoringRegion(region),
    ),
    ...overrides,
  };
}

export function pausedPipelineResult(cancelled = 0) {
  return {
    ok: true,
    pipeline_state: "paused",
    cancelled_jobs: cancelled,
    region: monitoringRegion("activity", {
      data: {
        ...(DATA.activity as Record<string, unknown>),
        pipeline_state: {
          key: "paused",
          label: "Paused",
          detail: cancelled
            ? `Queue cleared (${cancelled} cancelled)`
            : "No new scrape jobs will be queued",
        },
        pause_info: {
          mode: cancelled ? "clear" : "drain",
          cancelled_jobs: cancelled,
        },
      },
    }),
  };
}
