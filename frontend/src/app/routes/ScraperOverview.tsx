import { useCallback, useEffect, useRef, useState } from "react";
import { useLoaderData, useNavigate } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { cx } from "../../design-system/primitives/cx";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import { Cluster, Page, Stack } from "../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { Text, VisuallyHidden } from "../../design-system/primitives/typography";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  PrivilegedSessionExpired,
  controlPipeline,
  fetchRegion,
} from "./scraperMonitoring";
import type {
  MonitoringRegion,
  MonitoringSnapshot,
  RegionData,
} from "./scraperMonitoring";
import { useOperatorPresence } from "./scraperPresence";

import "./ScraperOverview.css";

const RAIL = [
  {
    label: "Overview",
    href: "/app/admin/acquisition/scraper/workspace",
    current: true,
  },
  {
    label: "States",
    href: "/app/admin/acquisition/scraper/workspace/states",
  },
  { label: "Status", href: "#scraper-status" },
  { label: "Pipeline", href: "#scraper-pipeline" },
  { label: "Throughput", href: "#scraper-throughput" },
  { label: "Fleet", href: "#scraper-fleet" },
  {
    label: "Keywords",
    href: "/app/admin/acquisition/scraper/workspace/keywords",
  },
  {
    label: "Database",
    href: "/app/admin/acquisition/scraper/workspace/database",
  },
  { label: "Exit to Acquisition", href: "/app/admin/acquisition" },
];

function count(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString() : "—";
}

function ratio(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function clock(value: string | null): string {
  if (!value) return "never";
  return new Intl.DateTimeFormat(undefined, {
    timeStyle: "medium",
  }).format(new Date(value));
}

// --- Freshness --------------------------------------------------------------

interface Feed {
  region: MonitoringRegion;
  /** Whether the most recent refresh attempt succeeded. */
  live: boolean;
}

type Feeds = Record<string, Feed>;

function seedFeeds(regions: MonitoringRegion[]): Feeds {
  return Object.fromEntries(
    regions.map((region) => [
      region.region,
      { region, live: region.state === "ok" },
    ]),
  );
}

/**
 * One independent poll per region, at that region's own cadence.
 *
 * A region that fails keeps its last good payload and is marked not-live, so
 * the panel can say how old its numbers are instead of going blank.
 */
function useRegionFeeds(
  initial: MonitoringRegion[],
  watching: boolean,
  onExpired: () => void,
): [Feeds, (region: MonitoringRegion) => void] {
  const [feeds, setFeeds] = useState<Feeds>(() => seedFeeds(initial));
  const expired = useRef(onExpired);
  expired.current = onExpired;

  // The plan itself is read through a ref so the effect depends only on a
  // primitive: re-running it would restart all nine timers, which would skew
  // every cadence on any unrelated re-render.
  const plan = useRef(initial);
  plan.current = initial;
  const planKey = initial
    .map((region) => `${region.region}:${region.refresh_seconds}`)
    .join(",");

  useEffect(() => {
    if (!watching) return;
    const timers = plan.current.map((entry) =>
      setInterval(() => {
        const key = entry.region;
        void fetchRegion(key)
          .then((next) => {
            setFeeds((current) => {
              if (next.state === "ok") {
                return { ...current, [key]: { region: next, live: true } };
              }
              // The upstream region is down. Keep what we had; only the
              // freshness claim changes.
              const previous = current[key];
              return {
                ...current,
                [key]: previous
                  ? { ...previous, live: false }
                  : { region: next, live: false },
              };
            });
          })
          .catch((caught: unknown) => {
            if (caught instanceof PrivilegedSessionExpired) {
              expired.current();
              return;
            }
            setFeeds((current) => {
              const previous = current[key];
              return previous
                ? { ...current, [key]: { ...previous, live: false } }
                : current;
            });
          });
      }, entry.refresh_seconds * 1000),
    );
    return () => timers.forEach(clearInterval);
  }, [planKey, watching]);

  const patch = useCallback((region: MonitoringRegion) => {
    setFeeds((current) => ({
      ...current,
      [region.region]: { region, live: region.state === "ok" },
    }));
  }, []);

  return [feeds, patch];
}

// --- Panel frame ------------------------------------------------------------

function Panel({
  id,
  title,
  cadence,
  feed,
  children,
  live = false,
}: {
  id?: string;
  title: string;
  cadence: string;
  feed: Feed | undefined;
  children: (data: RegionData) => React.ReactNode;
  /** Marks a panel whose numbers change constantly, as upstream does. */
  live?: boolean;
}) {
  const data = feed?.region.data ?? null;
  const stale = feed ? !feed.live : true;
  return (
    <section
      className="ops-panel"
      aria-label={title}
      {...(id ? { id } : {})}
    >
      <div className="ops-panel__head">
        <h3 className="ops-panel__title">{title}</h3>
        <span className="ops-panel__cadence">{cadence}</span>
      </div>
      {stale ? (
        <p className="ops-panel__stale" role="status">
          {data
            ? `Not refreshing. Showing the last reading from ${clock(
                feed?.region.fetched_at ?? null,
              )}.`
            : "No reading yet."}
        </p>
      ) : null}
      <div
        className="ops-panel__body"
        {...(live ? { "aria-live": "polite" as const } : {})}
      >
        {data ? children(data) : null}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ops-metric">
      <span className="ops-metric__label">{label}</span>
      <span className="ops-metric__value">{value}</span>
    </div>
  );
}

// --- Pipeline controls ------------------------------------------------------

function PipelineControls({
  data,
  onChanged,
  onExpired,
}: {
  data: RegionData;
  onChanged: (region: MonitoringRegion) => void;
  onExpired: () => void;
}) {
  const [pausing, setPausing] = useState(false);
  const [clearQueue, setClearQueue] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [outcome, setOutcome] = useState("");
  const state = data.pipeline_state;
  const paused = state?.key === "paused" || state?.key === "pausing";

  async function send(command: {
    action: "pause" | "resume";
    clear_queue?: boolean;
  }) {
    if (busy) return;
    setBusy(true);
    setFailure("");
    setOutcome("");
    try {
      const result = await controlPipeline({ ...command, reason: reason.trim() });
      onChanged(result.region);
      setOutcome(
        command.action === "resume"
          ? "Pipeline resumed."
          : result.cancelled_jobs
            ? `Pipeline paused; ${result.cancelled_jobs.toLocaleString()} queued jobs cancelled.`
            : "Pipeline paused; the queue was kept.",
      );
      setPausing(false);
      setReason("");
    } catch (caught) {
      if (caught instanceof PrivilegedSessionExpired) {
        onExpired();
        return;
      }
      setPausing(false);
      setFailure(
        caught instanceof Error
          ? caught.message
          : "The pipeline action could not be completed.",
      );
    } finally {
      setBusy(false);
    }
  }

  function start(action: "pause" | "resume", clear = false) {
    if (!reason.trim()) {
      setReasonError("Record why you are changing the pipeline.");
      return;
    }
    setReasonError("");
    if (action === "resume") {
      void send({ action: "resume" });
      return;
    }
    setClearQueue(clear);
    setPausing(true);
  }

  return (
    <Stack gap={3}>
      {state ? (
        <div className={cx("ops-pipeline__state", `ops-pipeline__state--${state.key}`)}>
          <Text as="span" weight="bold">{state.label}</Text>
          <Text as="span" size="sm">{state.detail}</Text>
        </div>
      ) : null}

      {outcome ? (
        <p className="ops-note ops-note--ok" role="status">{outcome}</p>
      ) : null}
      {failure ? (
        <p className="ops-note ops-note--bad" role="alert">{failure}</p>
      ) : null}

      <Field
        label="Reason"
        description="Recorded in Jawnix Activity with this action."
        required
        {...(reasonError ? { error: reasonError } : {})}
      >
        <Input
          value={reason}
          onChange={(event) => {
            setReason(event.currentTarget.value);
            setReasonError("");
          }}
          maxLength={2000}
        />
      </Field>

      <Cluster gap={2}>
        {paused ? (
          <Button variant="primary" busy={busy} busyLabel="Resuming…" onClick={() => start("resume")}>
            Resume pipeline
          </Button>
        ) : (
          <>
            <Button onClick={() => start("pause", false)}>Pause, keep queue</Button>
            <Button variant="danger" onClick={() => start("pause", true)}>
              Pause and clear queue
            </Button>
          </>
        )}
      </Cluster>

      <ConfirmDialog
        open={pausing}
        onClose={() => setPausing(false)}
        onConfirm={() => void send({ action: "pause", clear_queue: clearQueue })}
        title={clearQueue ? "Pause and clear the queue?" : "Pause the pipeline?"}
        consequence={
          clearQueue
            ? "Every queued scrape job is cancelled and no new work is queued. Running jobs still finish. Cancelled jobs cannot be restored — they must be enqueued again."
            : "No new scrape jobs are queued. Work already queued is kept and running jobs finish."
        }
        confirmLabel={clearQueue ? "Pause and clear queue" : "Pause, keep queue"}
        cancelLabel="Keep running"
        destructive={clearQueue}
        busy={busy}
      />
    </Stack>
  );
}

// --- The route --------------------------------------------------------------

export function ScraperOverviewRoute() {
  const snapshot = useLoaderData<MonitoringSnapshot>();
  const navigate = useNavigate();
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle("Scraper Operations");

  const expire = useCallback(() => {
    void navigate("/admin/acquisition/scraper", { replace: true });
  }, [navigate]);

  const { watching, resume } = useOperatorPresence(
    snapshot.idle_expires_in * 1000,
  );
  const [feeds, patch] = useRegionFeeds(snapshot.regions, watching, expire);

  const overall = feeds["overall"]?.region.data?.stack_status ?? null;
  const offline = snapshot.service_state === "unavailable";
  const tone = offline
    ? "offline"
    : overall?.key === "attention" || overall?.key === "stale"
      ? "warning"
      : "online";
  const status = offline
    ? "OFFLINE / FAIL-CLOSED"
    : `${(overall?.label ?? "MONITORING").toUpperCase()} / PRIVILEGED`;

  return (
    <Page
      title="Scraper Operations"
      description="Live acquisition monitoring and pipeline control, mediated and audited by Jawnix."
    >
      <TerminalWorkspace status={status} tone={tone} destinations={RAIL}>
        {offline ? (
          <Stack gap={4}>
            <ErrorState
              title="Scraper Operations unavailable"
              description={`The private service did not respond, so no monitoring or controls are available. Last successful connection: ${
                snapshot.last_successful_at
                  ? new Date(snapshot.last_successful_at).toLocaleString()
                  : "never"
              }.`}
              onRetry={() => window.location.reload()}
            />
            <div>
              <ActionLink href="/app/admin/acquisition">
                Back to Acquisition
              </ActionLink>
            </div>
          </Stack>
        ) : (
          <Stack gap={4}>
            {!watching ? (
              <div className="ops-idle" role="status">
                <Text as="span" size="sm">
                  Monitoring paused so the privileged session can expire.
                </Text>
                <Button onClick={resume}>Resume monitoring</Button>
              </div>
            ) : null}

            <section id="scraper-status" className="ops-overall" aria-label="Overall status">
              <div className={cx("ops-overall__pill", `ops-overall__pill--${overall?.key ?? "stale"}`)}>
                <Text as="span" weight="bold">{overall?.label ?? "Telemetry unavailable"}</Text>
              </div>
              <Text size="sm">{overall?.detail ?? "No host sample received."}</Text>
              {overall?.reasons.length ? (
                <ul className="ops-overall__reasons">
                  {overall.reasons.slice(0, 3).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </section>

            <div className="ops-grid">
              <Panel title="Host and stack" cadence="15s" feed={feeds["stack"]}>
                {(data) => (
                  <Stack gap={3}>
                    <div className="ops-metrics">
                      <Metric label="CPU" value={data.sample?.cpu_percent != null ? `${data.sample.cpu_percent}%` : "—"} />
                      <Metric label="Memory" value={data.sample?.memory_percent != null ? `${data.sample.memory_percent}%` : "—"} />
                      <Metric label="Disk" value={data.sample?.disk_percent != null ? `${data.sample.disk_percent}%` : "—"} />
                      <Metric label="Uptime" value={data.sample?.uptime_label ?? "—"} />
                      <Metric label="Spool" value={`${count(data.sample?.spool_pending_files)} · ${data.sample?.spool_age_label ?? "—"}`} />
                    </div>
                    <ul className="ops-services" aria-label="Host services">
                      {(data.services ?? []).map((service) => (
                        <li key={service.key} className={cx("ops-service", `ops-service--${service.state}`)}>
                          <span className="ops-service__label">{service.label}</span>
                          <span className="ops-service__detail">{service.detail}</span>
                        </li>
                      ))}
                    </ul>
                  </Stack>
                )}
              </Panel>

              <Panel id="scraper-pipeline" title="Pipeline activity" cadence="3s" feed={feeds["activity"]} live>
                {(data) => (
                  <Stack gap={3}>
                    <div className="ops-metrics">
                      <Metric label="Queued" value={count(data.activity?.queue_depth)} />
                      <Metric label="Running" value={count(data.activity?.running_jobs)} />
                      <Metric label="Retryable" value={count(data.activity?.retryable_jobs)} />
                      <Metric label="Jobs / min" value={count(data.activity?.jobs_last_minute)} />
                      <Metric label="Rows / min" value={count(data.activity?.results_last_minute)} />
                      <Metric label="Last write" value={data.activity?.write_age ?? "never"} />
                    </div>
                    <Text size="sm" tone="muted">
                      {`Latest: ${data.activity?.latest_keyword ?? "—"} · ${data.activity?.latest_state ?? "—"} · ${count(data.activity?.latest_result_count)} rows`}
                    </Text>
                    <PipelineControls data={data} onChanged={patch} onExpired={expire} />
                  </Stack>
                )}
              </Panel>

              <Panel id="scraper-throughput" title="Headline totals" cadence="10s" feed={feeds["stats"]}>
                {(data) => (
                  <div className="ops-metrics">
                    <Metric label="Businesses" value={count(data.stats?.businesses)} />
                    <Metric label="Phone leads" value={count(data.stats?.phone_businesses)} />
                    <Metric label="Unique phones" value={count(data.stats?.unique_phones)} />
                    <Metric label="Added / hour" value={count(data.stats?.added_last_hour)} />
                    <Metric label="Projected / day" value={count((data.stats?.added_last_hour ?? 0) * 24)} />
                    <Metric label="Empty rate" value={ratio(data.stats?.empty_rate)} />
                  </div>
                )}
              </Panel>

              <Panel title="Database activity" cadence="2s" feed={feeds["log"]} live>
                {(data) => (
                  <ol className="ops-log" role="list" aria-label="Committed jobs">
                    {(data.pipeline_events ?? []).map((event) => (
                      <li key={event.job_id}>
                        <span className="ops-log__time">{clock(event.created_at)}</span>
                        <span className="ops-log__state">{event.state}</span>
                        <span className="ops-log__keyword">{event.keyword}</span>
                        <span className="ops-log__counts">
                          {`${count(event.result_count)} rows · ${count(event.phone_count)} phones`}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </Panel>

              <Panel title="Performance trends" cadence="60s" feed={feeds["trends"]}>
                {(data) => (
                  <Stack gap={3}>
                    {(
                      [
                        ["Businesses", "businesses", "businesses_height"],
                        ["Jobs", "jobs", "jobs_height"],
                        ["Queue depth", "queue", "queue_height"],
                      ] as const
                    ).map(([label, valueKey, heightKey]) => (
                      <div key={label} className="ops-trend">
                        <span className="ops-trend__label">{label}</span>
                        <ol className="ops-trend__bars" role="list" aria-label={`${label}, last 24 hours`}>
                          {(data.trends ?? []).map((bucket) => (
                            <li key={bucket.label} style={{ ["--ops-bar" as string]: `${bucket[heightKey]}%` }}>
                              <VisuallyHidden>
                                {`${bucket.label} ${count(bucket[valueKey])}`}
                              </VisuallyHidden>
                            </li>
                          ))}
                        </ol>
                      </div>
                    ))}
                  </Stack>
                )}
              </Panel>

              <Panel id="scraper-fleet" title="Workers" cadence="15s" feed={feeds["workers"]}>
                {(data) => (
                  <Stack gap={2}>
                    <Text size="sm" tone="muted">
                      {`${(data.workers ?? []).length}/${count(data.expected_workers)} reporting`}
                    </Text>
                    <ul className="ops-workers" aria-label="Worker fleet">
                      {(data.workers ?? []).map((worker) => (
                        <li key={`${worker.box_id}-${worker.container_name}`} className={cx("ops-worker", worker.is_healthy ? "ops-worker--ok" : "ops-worker--bad")}>
                          <span className="ops-worker__name">{worker.container_name}</span>
                          <span className="ops-worker__status">
                            {worker.is_healthy ? "healthy" : "stale"} · {worker.heartbeat_age}
                          </span>
                          <span className="ops-worker__work">
                            {worker.current_keyword
                              ? `${worker.current_state ?? "—"} · ${worker.current_keyword}`
                              : "idle"}
                          </span>
                          <span className="ops-worker__rate">
                            {`${count(worker.jobs_processed)} jobs · ${worker.results_per_min ?? 0}/min`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </Stack>
                )}
              </Panel>

              <Panel title="Pipeline alerts" cadence="60s" feed={feeds["incidents"]}>
                {(data) =>
                  (data.incidents ?? []).length ? (
                    <ul className="ops-incidents" aria-label="Pipeline alerts">
                      {(data.incidents ?? []).map((incident) => (
                        <li key={incident.checked_at} className={cx("ops-incident", incident.status === "ok" ? "ops-incident--ok" : "ops-incident--bad")}>
                          <span className="ops-incident__time">{clock(incident.checked_at)}</span>
                          <span className="ops-incident__messages">
                            {incident.messages.length ? incident.messages.join("; ") : "All checks passed"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <Text size="sm" tone="muted">No alerts in the last 24 hours.</Text>
                  )
                }
              </Panel>

              <Panel title="Top states" cadence="30s" feed={feeds["top-states"]}>
                {(data) => {
                  const peak = data.top_states?.[0]?.businesses || 1;
                  return (
                    <ul className="ops-states" aria-label="Top states by businesses">
                      {(data.top_states ?? []).map((entry) => (
                        <li key={entry.state}>
                          <span className="ops-states__code">{entry.state}</span>
                          <span
                            className="ops-states__bar"
                            style={{ ["--ops-bar" as string]: `${(entry.businesses * 100) / peak}%` }}
                            aria-hidden="true"
                          />
                          <span className="ops-states__value">{count(entry.businesses)}</span>
                        </li>
                      ))}
                    </ul>
                  );
                }}
              </Panel>
            </div>
          </Stack>
        )}
      </TerminalWorkspace>
    </Page>
  );
}
