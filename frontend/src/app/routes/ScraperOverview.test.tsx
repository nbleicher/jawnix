import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { ScraperOverviewRoute } from "./ScraperOverview";
import type {
  MonitoringRegion,
  MonitoringSnapshot,
  RegionData,
  RegionKey,
  StackSample,
} from "./scraperMonitoring";

const CADENCE: Record<RegionKey, number> = {
  log: 2,
  activity: 3,
  stats: 10,
  overall: 15,
  stack: 15,
  workers: 15,
  "top-states": 30,
  trends: 60,
  incidents: 60,
};

/** Every host field, so a fixture can override only the ones it cares about. */
function emptySample(): StackSample {
  return {
    captured_at: "2026-07-28T11:59:30Z",
    cpu_percent: null,
    load_1: null,
    memory_used_bytes: null,
    memory_total_bytes: null,
    memory_percent: null,
    disk_used_bytes: null,
    disk_total_bytes: null,
    disk_percent: null,
    host_uptime_seconds: null,
    uptime_label: null,
    spool_pending_files: null,
    spool_oldest_seconds: null,
    spool_age_label: null,
    worker_restarts: null,
    expected_workers: null,
    running_workers: null,
    unhealthy_workers: null,
    database_ok: null,
    dashboard_ok: null,
    queue_api_ok: null,
    required_services_ok: null,
    queue_depth: null,
    running_jobs: null,
    retryable_jobs: null,
    oldest_queue_seconds: null,
    businesses_total: null,
    completed_jobs_total: null,
    empty_rate_1h: null,
  };
}

const DATA: Record<RegionKey, RegionData> = {
  overall: {
    stack_status: {
      key: "attention",
      label: "Attention needed",
      detail: "Only 6 of 8 workers are running",
      reasons: ["Only 6 of 8 workers are running", "Queue depth exceeds its warning threshold"],
      age_seconds: 30,
    },
  },
  stack: {
    sample: {
      ...emptySample(),
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
      { key: "gms-enqueue.service", label: "Enqueuer", state: "bad", detail: "failed" },
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
    pipeline_state: { key: "running", label: "Running", detail: "Workers are processing the queue" },
    pause_info: { mode: "", cancelled_jobs: 0 },
  },
  log: {
    pipeline_events: [
      { job_id: 5001, created_at: "2026-07-28T11:59:40Z", keyword: "dentist", state: "PA", result_count: 20, phone_count: 17 },
    ],
  },
  workers: {
    expected_workers: 8,
    workers: [
      { box_id: "box-1", container_name: "gms-worker-1", reported_at: "2026-07-28T11:59:50Z", heartbeat_age: "10s", is_healthy: true, status: "alive", active_jobs: 1, jobs_processed: 4210, results_per_min: 18.5, current_state: "PA", current_keyword: "dentist", current_job_id: 5001 },
      { box_id: "box-2", container_name: "gms-worker-2", reported_at: "2026-07-28T11:55:00Z", heartbeat_age: "5m", is_healthy: false, status: "stale", active_jobs: 0, jobs_processed: 3900, results_per_min: 0, current_state: null, current_keyword: null, current_job_id: null },
    ],
  },
  trends: {
    trends: Array.from({ length: 24 }, (_, hour) => ({
      label: `${String(hour).padStart(2, "0")}:00`,
      businesses: 100, jobs: 20, queue: 400, cpu: 40, memory: 70,
      businesses_height: 80, jobs_height: 60, queue_height: 50,
    })),
  },
  incidents: {
    incidents: [
      { checked_at: "2026-07-28T11:30:00Z", status: "error", messages: ["Queue depth exceeds its warning threshold"] },
    ],
  },
  "top-states": { top_states: [{ state: "TX", businesses: 1204002 }, { state: "PA", businesses: 980411 }] },
};

function region(key: RegionKey, overrides: Partial<MonitoringRegion> = {}): MonitoringRegion {
  return {
    region: key,
    state: "ok",
    refresh_seconds: CADENCE[key],
    fetched_at: "2026-07-28T12:00:00Z",
    data: DATA[key],
    ...overrides,
  };
}

function snapshot(overrides: Partial<MonitoringSnapshot> = {}): MonitoringSnapshot {
  return {
    service_state: "connected",
    last_successful_at: "2026-07-28T12:00:00Z",
    idle_expires_in: 900,
    regions: (Object.keys(CADENCE) as RegionKey[]).map((key) => region(key)),
    ...overrides,
  };
}

function renderOverview(data: MonitoringSnapshot) {
  const router = createMemoryRouter(
    [
      {
        id: "scraper",
        path: "/admin/acquisition/scraper/workspace",
        loader: () => data,
        element: <ScraperOverviewRoute />,
      },
    ],
    {
      initialEntries: ["/admin/acquisition/scraper/workspace"],
      hydrationData: { loaderData: { scraper: data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

function mockPipeline(response: unknown, ok = true) {
  const calls: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: string, init?: RequestInit) => {
      calls.push(init?.body ? JSON.parse(String(init.body)) : undefined);
      return Promise.resolve({
        ok,
        status: ok ? 200 : 422,
        json: () => Promise.resolve(response),
      } as Response);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("every monitoring read survives the rebuild", () => {
  it("renders all nine regions", () => {
    renderOverview(snapshot());

    for (const name of [
      "Host and stack",
      "Pipeline activity",
      "Headline totals",
      "Database activity",
      "Performance trends",
      "Workers",
      "Pipeline alerts",
      "Top states",
    ]) {
      expect(screen.getByRole("group", { name })).toBeVisible();
    }
    expect(screen.getByRole("region", { name: "Overall status" })).toBeVisible();
  });

  it("keeps the overall status and the reasons behind it", () => {
    renderOverview(snapshot());

    const overall = screen.getByRole("region", { name: "Overall status" });
    expect(overall).toHaveTextContent("Attention needed");
    expect(overall).toHaveTextContent("Only 6 of 8 workers are running");
    expect(overall).toHaveTextContent("Queue depth exceeds its warning threshold");
  });

  it("shows Workers and the live log without requiring the operator to expand panels", () => {
    // #156 collapsed every monitoring panel by default. Attention named the
    // problem ("1 worker is unhealthy") while the Workers list and Database
    // activity log that identify it stayed hidden behind closed <details>.
    renderOverview(snapshot());

    const workers = screen.getByRole("group", { name: "Workers" });
    expect(workers).toHaveProperty("open", true);
    expect(workers).toHaveTextContent("gms-worker-2");
    expect(workers).toHaveTextContent("stale");

    const log = screen.getByRole("group", { name: "Database activity" });
    expect(log).toHaveProperty("open", true);
    expect(log).toHaveTextContent("dentist");
  });

  it("keeps host telemetry, services, totals, activity, fleet, alerts and states", () => {
    renderOverview(snapshot());

    const stack = screen.getByRole("group", { name: "Host and stack" });
    expect(stack).toHaveTextContent("42.5%");
    expect(stack).toHaveTextContent("11d 0h");
    expect(within(stack).getByRole("list", { name: "Host services" })).toHaveTextContent("Enqueuer");

    expect(screen.getByRole("group", { name: "Headline totals" })).toHaveTextContent("9,244,326");
    // Projected/day is derived from the hourly rate, as upstream does.
    expect(screen.getByRole("group", { name: "Headline totals" })).toHaveTextContent("101,160");

    const activity = screen.getByRole("group", { name: "Pipeline activity" });
    expect(activity).toHaveTextContent("812");
    expect(activity).toHaveTextContent("dentist");

    expect(screen.getByRole("group", { name: "Workers" })).toHaveTextContent("2/8 reporting");
    expect(screen.getByRole("group", { name: "Workers" })).toHaveTextContent("gms-worker-2");
    expect(screen.getByRole("group", { name: "Pipeline alerts" })).toHaveTextContent(
      "Queue depth exceeds its warning threshold",
    );
    expect(screen.getByRole("group", { name: "Top states" })).toHaveTextContent("TX");
  });

  it("labels every trend bar so the charts are not colour-only", () => {
    renderOverview(snapshot());

    const chart = screen.getByRole("list", { name: "Businesses, last 24 hours" });
    expect(within(chart).getAllByRole("listitem")).toHaveLength(24);
    expect(chart).toHaveTextContent("00:00 100");
  });

  it("states each region's refresh cadence", () => {
    renderOverview(snapshot());

    expect(screen.getByRole("group", { name: "Database activity" })).toHaveTextContent("2s");
    expect(screen.getByRole("group", { name: "Performance trends" })).toHaveTextContent("60s");
  });
});

describe("partial failure, staleness and outage", () => {
  it("keeps a failed region's last reading and says it is not refreshing", () => {
    renderOverview(
      snapshot({
        regions: (Object.keys(CADENCE) as RegionKey[]).map((key) =>
          key === "workers" ? region(key, { state: "unavailable" }) : region(key),
        ),
      }),
    );

    const workers = screen.getByRole("group", { name: "Workers" });
    expect(workers).toHaveTextContent("Not refreshing");
    // The last safe context is still on screen.
    expect(workers).toHaveTextContent("gms-worker-1");
    // And no other panel is affected.
    expect(screen.getByRole("group", { name: "Top states" })).not.toHaveTextContent(
      "Not refreshing",
    );
  });

  it("says so plainly when a region has never been read", () => {
    renderOverview(
      snapshot({
        regions: (Object.keys(CADENCE) as RegionKey[]).map((key) =>
          key === "trends"
            ? region(key, { state: "unavailable", data: null, fetched_at: null })
            : region(key),
        ),
      }),
    );

    expect(screen.getByRole("group", { name: "Performance trends" })).toHaveTextContent(
      "No reading yet.",
    );
  });

  it("fails closed on a full outage while keeping the last connection", () => {
    renderOverview(
      snapshot({ service_state: "unavailable", last_successful_at: "2026-07-28T11:00:00Z" }),
    );

    expect(
      screen.getByRole("heading", { name: "Scraper Operations unavailable" }),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Last successful connection");
    expect(screen.queryByRole("button", { name: /Pause/ })).not.toBeInTheDocument();
  });

  it("never renders upstream internals", () => {
    renderOverview(snapshot());

    expect(document.body).not.toHaveTextContent("river_job");
    expect(document.body).not.toHaveTextContent("docker.service");
  });
});

describe("pipeline controls", () => {
  const paused = {
    ok: true,
    pipeline_state: "paused",
    cancelled_jobs: 0,
    region: region("activity", {
      data: {
        ...DATA.activity,
        pipeline_state: { key: "paused", label: "Paused", detail: "No new scrape jobs will be queued" },
        pause_info: { mode: "drain", cancelled_jobs: 0 },
      },
    }),
  };

  it("requires a reason before any pipeline change", async () => {
    const user = userEvent.setup();
    const calls = mockPipeline(paused);
    renderOverview(snapshot());

    await user.click(screen.getByRole("button", { name: "Pause, keep queue" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Record why you are changing the pipeline.",
    );
    expect(calls).toHaveLength(0);
  });

  it("pauses without clearing the queue by default", async () => {
    const user = userEvent.setup();
    const calls = mockPipeline(paused);
    renderOverview(snapshot());

    await user.type(screen.getByRole("textbox", { name: /Reason/ }), "Source quality");
    await user.click(screen.getByRole("button", { name: "Pause, keep queue" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Work already queued is kept");
    await user.click(within(dialog).getByRole("button", { name: "Pause, keep queue" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      action: "pause",
      clear_queue: false,
      reason: "Source quality",
    });
  });

  it("warns that clearing the queue cannot be undone, and reports what it cancelled", async () => {
    const user = userEvent.setup();
    const calls = mockPipeline({
      ...paused,
      cancelled_jobs: 812,
      region: region("activity", {
        data: {
          ...DATA.activity,
          pipeline_state: { key: "paused", label: "Paused", detail: "Queue cleared (812 cancelled)" },
          pause_info: { mode: "clear", cancelled_jobs: 812 },
        },
      }),
    });
    renderOverview(snapshot());

    await user.type(screen.getByRole("textbox", { name: /Reason/ }), "Bad keyword set");
    await user.click(screen.getByRole("button", { name: "Pause and clear queue" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("cannot be restored");
    await user.click(within(dialog).getByRole("button", { name: "Pause and clear queue" }));

    await waitFor(() =>
      expect(screen.getByText(/812 queued jobs cancelled/)).toBeVisible(),
    );
    expect(calls[0]).toMatchObject({ action: "pause", clear_queue: true });
  });

  it("offers resume instead of pause once the pipeline is paused", async () => {
    const user = userEvent.setup();
    const calls = mockPipeline({
      ok: true,
      pipeline_state: "running",
      cancelled_jobs: 0,
      region: region("activity"),
    });
    renderOverview(
      snapshot({
        regions: (Object.keys(CADENCE) as RegionKey[]).map((key) =>
          key === "activity"
            ? region(key, {
                data: {
                  ...DATA.activity,
                  pipeline_state: { key: "paused", label: "Paused", detail: "No new scrape jobs will be queued" },
                },
              })
            : region(key),
        ),
      }),
    );

    expect(screen.queryByRole("button", { name: "Pause, keep queue" })).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: /Reason/ }), "Inventory recovered");
    await user.click(screen.getByRole("button", { name: "Resume pipeline" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ action: "resume", reason: "Inventory recovered" });
    await waitFor(() => expect(screen.getByText("Pipeline resumed.")).toBeVisible());
  });

  it("surfaces a refused action without claiming it happened", async () => {
    const user = userEvent.setup();
    mockPipeline({ detail: "Only a pause can clear the queue." }, false);
    renderOverview(snapshot());

    await user.type(screen.getByRole("textbox", { name: /Reason/ }), "Testing");
    await user.click(screen.getByRole("button", { name: "Pause, keep queue" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Pause, keep queue" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Only a pause can clear the queue.",
      ),
    );
  });
});
