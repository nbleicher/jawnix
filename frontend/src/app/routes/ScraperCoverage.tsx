import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useLoaderData, useNavigate } from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { cx } from "../../design-system/primitives/cx";
import { EmptyState, ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Page, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { Text, VisuallyHidden } from "../../design-system/primitives/typography";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  fetchStateCards,
  fetchStateCells,
  fetchStateKeywords,
} from "./scraperCoverageData";
import type {
  CellStatus,
  CoverageFeed,
  CoverageStatus,
  StateCoverageCard,
  StateCoverageDetail,
  StateCoverageSnapshot,
  StateGridCell,
  StateGridCoverage,
  StateKeywordActivity,
} from "./scraperCoverageData";
import { PrivilegedSessionExpired } from "./scraperMonitoring";
import { useOperatorPresence } from "./scraperPresence";

import {
  WORKSPACE_ROOT,
  WORKSPACE_SECTIONS,
  workspaceRail,
} from "./scraperWorkspaceNav";
import "./ScraperCoverage.css";

const OVERVIEW_PATH = "/app/admin/acquisition/scraper/workspace";
const STATES_PATH = `${OVERVIEW_PATH}/states`;


const STATUS_LABELS: Record<CoverageStatus, string> = {
  covered: "Covered",
  partial: "In progress",
  uncovered: "Uncovered",
};

const CELL_LABELS: Record<CellStatus, string> = {
  posted: "Posted",
  reserved: "Reserved",
  failed: "Failed",
  uncovered: "Uncovered",
};

function count(value: number): string {
  return value.toLocaleString();
}

function percent(value: number): string {
  return `${value}%`;
}

function rate(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function clock(value: string | null): string {
  if (!value) return "never";
  return new Intl.DateTimeFormat(undefined, {
    timeStyle: "medium",
  }).format(new Date(value));
}

interface RetainedFeed<T> {
  feed: CoverageFeed<T>;
  live: boolean;
}

/**
 * Refresh one region without replacing readable data with an outage.
 *
 * Stable keys in the rendered cards, rows, and cells mean these state updates
 * do not replace the focused node. A failed attempt changes only the freshness
 * claim and leaves the last successful payload in place.
 */
function useRetainedFeed<T>(
  initial: CoverageFeed<T>,
  watching: boolean,
  refresh: () => Promise<CoverageFeed<T>>,
  onExpired: () => void,
): RetainedFeed<T> {
  const [retained, setRetained] = useState<RetainedFeed<T>>({
    feed: initial,
    live: initial.state === "ok",
  });
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  const expiredRef = useRef(onExpired);
  expiredRef.current = onExpired;

  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(() => {
      void refreshRef
        .current()
        .then((next) => {
          setRetained((current) =>
            next.state === "ok"
              ? { feed: next, live: true }
              : { ...current, live: false },
          );
        })
        .catch((caught: unknown) => {
          if (caught instanceof PrivilegedSessionExpired) {
            expiredRef.current();
            return;
          }
          setRetained((current) => ({ ...current, live: false }));
        });
    }, initial.refresh_seconds * 1000);
    return () => clearInterval(timer);
  }, [initial.refresh_seconds, watching]);

  return retained;
}

function IdleNotice({
  watching,
  onResume,
}: {
  watching: boolean;
  onResume: () => void;
}) {
  return !watching ? (
    <div className="coverage-idle" role="status">
      <Text as="span" size="sm">
        Live coverage refresh paused so the privileged session can expire.
      </Text>
      <Button onClick={onResume}>Resume live refresh</Button>
    </div>
  ) : null;
}

function Freshness({
  live,
  feed,
}: {
  live: boolean;
  feed: CoverageFeed<unknown>;
}) {
  return !live ? (
    <p className="coverage-stale" role="status">
      {feed.data
        ? `Not refreshing. Showing the last reading from ${clock(feed.fetched_at)}.`
        : "No reading is available yet."}
    </p>
  ) : null;
}

function coverageTone(status: CoverageStatus) {
  if (status === "covered") return "success" as const;
  if (status === "partial") return "warning" as const;
  return "neutral" as const;
}

function StateCard({ item }: { item: StateCoverageCard }) {
  return (
    <Link
      className="coverage-state-card"
      to={`/admin/acquisition/scraper/workspace/states/${item.state}`}
      aria-label={`${item.state}: ${STATUS_LABELS[item.status]}, ${percent(
        item.coverage,
      )} coverage, ${count(item.businesses)} businesses`}
    >
      <div className="coverage-state-card__head">
        <span className="coverage-state-card__code">{item.state}</span>
        <StatusBadge tone={coverageTone(item.status)}>
          {STATUS_LABELS[item.status]}
        </StatusBadge>
      </div>
      <strong className="coverage-state-card__businesses">
        {count(item.businesses)}
      </strong>
      <span className="coverage-state-card__caption">businesses</span>
      <div className="coverage-state-card__coverage">
        <span>Coverage</span>
        <b>{percent(item.coverage)}</b>
      </div>
      <div
        className="coverage-progress"
        role="progressbar"
        aria-label={`${item.state} grid coverage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={item.coverage}
      >
        <span style={{ width: percent(item.coverage) }} />
      </div>
      <div className="coverage-state-card__foot">
        <span>
          {count(item.posted_cells)}/{count(item.total_cells)} cells
        </span>
        <span>{count(item.active_keywords)} keywords</span>
      </div>
    </Link>
  );
}

export function ScraperStateCoverageRoute() {
  const snapshot = useLoaderData<StateCoverageSnapshot>();
  const navigate = useNavigate();
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle("States · Scraper Operations");

  const expire = useCallback(() => {
    void navigate("/admin/acquisition/scraper", { replace: true });
  }, [navigate]);
  const { watching, resume } = useOperatorPresence(
    snapshot.idle_expires_in * 1000,
  );
  const states = useRetainedFeed(
    snapshot.states,
    watching,
    async () => (await fetchStateCards()).states,
    expire,
  );
  const offline =
    snapshot.service_state === "unavailable" && !states.feed.data;

  return (
    <Page
      title="States"
      description="Per-state Scraper coverage, business counts, active keywords, and today’s grid progress."
      actions={
        <ActionLink href="/app/admin/acquisition#scraper-configuration-versions">
          Configuration versions
        </ActionLink>
      }
    >
      <TerminalWorkspace
        status={offline ? "OFFLINE / LAST STATE UNAVAILABLE" : "ONLINE / COVERAGE"}
        tone={offline ? "offline" : states.live ? "online" : "warning"}
        destinations={workspaceRail(`${WORKSPACE_ROOT}/states`)}
      >
        <Stack gap={4}>
          <IdleNotice watching={watching} onResume={resume} />
          {offline ? (
            <ErrorState
              title="State coverage unavailable"
              description={`The private service did not return state coverage. Last successful connection: ${
                snapshot.last_successful_at
                  ? new Date(snapshot.last_successful_at).toLocaleString()
                  : "never"
              }.`}
              onRetry={() => window.location.reload()}
            />
          ) : (
            <section aria-label="State coverage">
              <Stack gap={3}>
                <div className="coverage-section-head">
                  <div>
                    <span className="coverage-eyebrow">Coverage</span>
                    <h2>Active states</h2>
                  </div>
                  <span className="coverage-cadence">
                    {states.feed.refresh_seconds}s refresh
                  </span>
                </div>
                <Freshness live={states.live} feed={states.feed} />
                {states.feed.data?.length ? (
                  <div className="coverage-state-grid">
                    {states.feed.data.map((item) => (
                      <StateCard item={item} key={item.state} />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No active states"
                    description="Add an active state through the current Scraper configuration before coverage can begin."
                    action={
                      <ActionLink href="/app/admin/acquisition#scraper-configuration-versions">
                        Open configuration versions
                      </ActionLink>
                    }
                  />
                )}
              </Stack>
            </section>
          )}
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}

function StateSection<T>({
  id,
  eyebrow,
  title,
  retained,
  children,
}: {
  id: string;
  eyebrow: string;
  title: string;
  retained: RetainedFeed<T>;
  children: (data: T) => React.ReactNode;
}) {
  return (
    <section id={id} className="coverage-section" aria-label={title}>
      <div className="coverage-section-head">
        <div>
          <span className="coverage-eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        <span className="coverage-cadence">
          {retained.feed.refresh_seconds}s refresh
        </span>
      </div>
      <Freshness live={retained.live} feed={retained.feed} />
      {retained.feed.data ? children(retained.feed.data) : (
        <EmptyState
          title={`${title} unavailable`}
          description="This region has not returned a readable result yet. Other state coverage remains available."
        />
      )}
    </section>
  );
}

function KeywordActivity({ rows }: { rows: StateKeywordActivity[] }) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized
      ? rows.filter((row) =>
          row.keyword.toLocaleLowerCase().includes(normalized),
        )
      : rows;
  }, [query, rows]);

  return (
    <Stack gap={3}>
      <div className="coverage-filter">
        <Field
          label="Find keyword activity"
          description={`${count(filtered.length)} of ${count(rows.length)} keywords shown.`}
        >
          <Input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            placeholder="Filter by keyword"
          />
        </Field>
      </div>
      {filtered.length ? (
        <div className="coverage-keywords">
          <table>
            <caption>
              <VisuallyHidden>
                Per-state keyword activity and coverage
              </VisuallyHidden>
            </caption>
            <thead>
              <tr>
                <th scope="col">Keyword</th>
                <th scope="col">Businesses</th>
                <th scope="col">Cells</th>
                <th scope="col">Coverage</th>
                <th scope="col">Empty rate</th>
                <th scope="col">Last enqueue</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.keyword}>
                  <th scope="row" data-label="Keyword">
                    {row.keyword}
                  </th>
                  <td data-label="Businesses">{count(row.businesses)}</td>
                  <td data-label="Cells">
                    {count(row.posted_cells)}/{count(row.total_cells)}
                  </td>
                  <td data-label="Coverage">
                    <span className="coverage-table-progress">
                      <span style={{ width: percent(row.coverage) }} />
                    </span>
                    <span>{percent(row.coverage)}</span>
                  </td>
                  <td data-label="Empty rate">
                    <span
                      className={cx(
                        "coverage-empty-rate",
                        row.empty_rate > 0.5
                          ? "coverage-empty-rate--bad"
                          : null,
                      )}
                    >
                      {rate(row.empty_rate)}
                    </span>
                  </td>
                  <td data-label="Last enqueue">
                    {row.last_enqueued ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title="No matching keyword activity"
          description="Clear the filter to restore every keyword in this state."
          action={<Button onClick={() => setQuery("")}>Clear filter</Button>}
        />
      )}
    </Stack>
  );
}

function GridCoverage({ data }: { data: StateGridCoverage }) {
  const [filter, setFilter] = useState<"all" | CellStatus>("all");
  const [selectedCell, setSelectedCell] = useState(
    data.cells[0]?.cell ?? "",
  );
  const filtered = useMemo(
    () =>
      filter === "all"
        ? data.cells
        : data.cells.filter((cell) => cell.status === filter),
    [data.cells, filter],
  );
  const selected =
    filtered.find((cell) => cell.cell === selectedCell) ??
    filtered[0] ??
    null;
  const selectedIndex = selected
    ? filtered.findIndex((cell) => cell.cell === selected.cell)
    : -1;

  function move(offset: number) {
    if (!filtered.length) return;
    const index =
      (selectedIndex + offset + filtered.length) % filtered.length;
    setSelectedCell(filtered[index]?.cell ?? "");
  }

  return (
    <Stack gap={3}>
      <ul className="coverage-legend" aria-label="Grid status totals">
        {(Object.keys(CELL_LABELS) as CellStatus[]).map((status) => (
          <li key={status}>
            <span
              className={cx(
                "coverage-legend__swatch",
                `coverage-legend__swatch--${status}`,
              )}
              aria-hidden="true"
            />
            <span>{CELL_LABELS[status]}</span>
            <strong>{count(data[status])}</strong>
          </li>
        ))}
      </ul>

      <div className="coverage-grid-filter">
        <Field
          label="Grid status"
          description={`${count(filtered.length)} of ${count(data.cells.length)} cells shown.`}
        >
          <Select
            value={filter}
            onChange={(event) => {
              const next = event.currentTarget.value as "all" | CellStatus;
              setFilter(next);
              const first =
                next === "all"
                  ? data.cells[0]
                  : data.cells.find((cell) => cell.status === next);
              if (first) setSelectedCell(first.cell);
            }}
          >
            <option value="all">All statuses</option>
            {(Object.keys(CELL_LABELS) as CellStatus[]).map((status) => (
              <option value={status} key={status}>
                {CELL_LABELS[status]}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {selected ? (
        <div className="coverage-grid-layout">
          <div
            className="coverage-cell-detail"
            aria-label="Selected grid cell"
            aria-live="polite"
          >
            <span className="coverage-eyebrow">Selected cell</span>
            <strong>
              Cell {selected.index} of {data.cells.length}
            </strong>
            <code>{selected.cell}</code>
            <StatusBadge
              tone={
                selected.status === "posted"
                  ? "success"
                  : selected.status === "reserved"
                    ? "warning"
                    : selected.status === "failed"
                      ? "danger"
                      : "neutral"
              }
            >
              {CELL_LABELS[selected.status]}
            </StatusBadge>
            <Cluster gap={2}>
              <Button onClick={() => move(-1)}>Previous cell</Button>
              <Button onClick={() => move(1)}>Next cell</Button>
            </Cluster>
          </div>

          <ol className="coverage-cell-grid" aria-label="Grid cells">
            {filtered.map((cell) => (
              <li key={cell.cell}>
                <CellButton
                  cell={cell}
                  selected={cell.cell === selected.cell}
                  onSelect={() => setSelectedCell(cell.cell)}
                />
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <EmptyState
          title="No cells match this status"
          description="Choose another grid status to inspect the remaining cells."
        />
      )}
    </Stack>
  );
}

function CellButton({
  cell,
  selected,
  onSelect,
}: {
  cell: StateGridCell;
  selected: boolean;
  onSelect: () => void;
}) {
  const label = `Cell ${cell.index}: ${cell.cell} — ${CELL_LABELS[cell.status]}`;
  return (
    <button
      type="button"
      className={cx(
        "coverage-cell",
        `coverage-cell--${cell.status}`,
      )}
      aria-label={label}
      aria-pressed={selected}
      title={label}
      onClick={onSelect}
    >
      <span className="coverage-cell__index">#{cell.index}</span>
      <span className="coverage-cell__coordinate">{cell.cell}</span>
      <span className="coverage-cell__status">
        {CELL_LABELS[cell.status]}
      </span>
    </button>
  );
}

export function ScraperStateDetailRoute() {
  const snapshot = useLoaderData<StateCoverageDetail>();
  const navigate = useNavigate();
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle(`${snapshot.state} coverage · Scraper Operations`);

  const expire = useCallback(() => {
    void navigate("/admin/acquisition/scraper", { replace: true });
  }, [navigate]);
  const { watching, resume } = useOperatorPresence(
    snapshot.idle_expires_in * 1000,
  );
  const keywords = useRetainedFeed(
    snapshot.keywords,
    watching,
    () => fetchStateKeywords(snapshot.state),
    expire,
  );
  const cells = useRetainedFeed(
    snapshot.cells,
    watching,
    () => fetchStateCells(snapshot.state),
    expire,
  );
  const unavailable = !keywords.feed.data && !cells.feed.data;
  const detailPath = `${WORKSPACE_ROOT}/states/${snapshot.state}`;

  return (
    <Page
      title={`${snapshot.state} coverage`}
      description="Per-keyword activity and today’s complete Scraper grid-cell status."
      actions={
        <>
          <ActionLink href={STATES_PATH}>All states</ActionLink>
          <ActionLink href="/app/admin/acquisition#scraper-configuration-versions">
            Configuration versions
          </ActionLink>
        </>
      }
    >
      <TerminalWorkspace
        status={
          unavailable
            ? "OFFLINE / COVERAGE UNAVAILABLE"
            : !keywords.live || !cells.live
              ? "DEGRADED / LAST READINGS"
              : "ONLINE / COVERAGE"
        }
        tone={
          unavailable
            ? "offline"
            : !keywords.live || !cells.live
              ? "warning"
              : "online"
        }
        destinations={workspaceRail(detailPath, {
          pageLabel: `${snapshot.state} coverage`,
          sections: WORKSPACE_SECTIONS.stateCoverage,
        })}
      >
        <Stack gap={6}>
          <IdleNotice watching={watching} onResume={resume} />
          <StateSection
            id="state-keywords"
            eyebrow="Campaigns"
            title="Keywords"
            retained={keywords}
          >
            {(rows) => <KeywordActivity rows={rows} />}
          </StateSection>
          <StateSection
            id="state-grid"
            eyebrow="Today"
            title="Grid cells"
            retained={cells}
          >
            {(data) => <GridCoverage data={data} />}
          </StateSection>
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}
