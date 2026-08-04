import { useEffect, useRef, useState } from "react";
import {
  useLoaderData,
  useNavigate,
  useSearchParams,
} from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { EmptyState, ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Page, Stack } from "../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { VisuallyHidden } from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  ScraperRuntimeRequestError,
  fetchCampaignHistory,
} from "./scraperRuntimeApi";
import type {
  CampaignHistory,
  HistorySort,
  SortDirection,
} from "./scraperRuntimeApi";

import { WORKSPACE_ROOT, workspaceRail } from "./scraperWorkspaceNav";
import "./ScraperCampaignHistory.css";


const SORT_LABELS: Array<[HistorySort, string]> = [
  ["last_enqueued", "Campaign date"],
  ["keyword", "Keyword"],
  ["state", "State"],
  ["cells_posted", "Cells posted"],
];

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Campaign history could not be loaded.";
}

function lastSuccess(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "never";
}

export function ScraperCampaignHistoryRoute() {
  const initial = useLoaderData<CampaignHistory>();
  const navigate = useNavigate();
  const [, setSearchParams] = useSearchParams();
  const [history, setHistory] = useState(initial);
  const [search, setSearch] = useState(initial.search);
  const [state, setState] = useState(initial.state);
  const [sort, setSort] = useState<HistorySort>(initial.sort);
  const [direction, setDirection] = useState<SortDirection>(
    initial.direction,
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const first = useRef(true);
  const requestNumber = useRef(0);

  useDocumentTitle("Scraper Campaign History");

  async function load() {
    const number = ++requestNumber.current;
    setBusy(true);
    setFailure("");
    try {
      const next = await fetchCampaignHistory({
        search,
        state,
        sort,
        direction,
      });
      if (number !== requestNumber.current) return;
      setHistory(next);
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (state) params.set("state", state);
      if (sort !== "last_enqueued") params.set("sort", sort);
      if (direction !== "desc") params.set("direction", direction);
      setSearchParams(params, { replace: true });
    } catch (error) {
      if (number !== requestNumber.current) return;
      if (
        error instanceof ScraperRuntimeRequestError &&
        error.status === 401
      ) {
        void navigate("/admin/acquisition/scraper");
        return;
      }
      setFailure(message(error));
    } finally {
      if (number === requestNumber.current) setBusy(false);
    }
  }

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const timer = window.setTimeout(() => void load(), 350);
    return () => window.clearTimeout(timer);
    // load is deliberately represented by the serializable filter values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, state, sort, direction]);

  if (history.service_state === "unavailable") {
    return (
      <Page
        title="Scraper Campaign History"
        description="Search and inspect the Scraper campaign ledger without exposing the private service."
      >
        <TerminalWorkspace
          status="OFFLINE / HISTORY UNAVAILABLE"
          tone="offline"
          destinations={workspaceRail(`${WORKSPACE_ROOT}/history`)}
        >
          <ErrorState
            title="Campaign history unavailable"
            description={`The private service did not return campaign history, so filtering and results are unavailable. Last successful connection: ${lastSuccess(history.last_successful_at)}.`}
            onRetry={() => window.location.reload()}
          />
        </TerminalWorkspace>
      </Page>
    );
  }

  return (
    <Page
      title="Scraper Campaign History"
      description="Search and inspect the Scraper campaign ledger without exposing the private service."
    >
      <TerminalWorkspace
        status={busy ? "HISTORY / QUERYING" : "HISTORY / PRIVILEGED"}
        tone={failure ? "warning" : "online"}
        destinations={workspaceRail(`${WORKSPACE_ROOT}/history`)}
      >
        <Stack gap={4}>
          <form
            className="campaign-filters"
            aria-label="Campaign history filters"
            onSubmit={(event) => {
              event.preventDefault();
              void load();
            }}
          >
            <Field label="Search keywords">
              <Input
                type="search"
                value={search}
                placeholder="Search campaign keywords"
                onChange={(event) => setSearch(event.currentTarget.value)}
              />
            </Field>
            <Field label="State">
              <Select
                value={state}
                onChange={(event) => setState(event.currentTarget.value)}
              >
                <option value="">All states</option>
                {history.all_states.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Sort by">
              <Select
                value={sort}
                onChange={(event) =>
                  setSort(event.currentTarget.value as HistorySort)
                }
              >
                {SORT_LABELS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Direction">
              <Select
                value={direction}
                onChange={(event) =>
                  setDirection(event.currentTarget.value as SortDirection)
                }
              >
                <option value="desc">Descending</option>
                <option value="asc">Ascending</option>
              </Select>
            </Field>
            <Button type="submit" busy={busy} busyLabel="Loading…">
              Apply filters
            </Button>
          </form>

          {failure ? (
            <ErrorState
              title="Campaign history unavailable"
              description={failure}
              retryLabel="Retry this query"
              onRetry={() => void load()}
            />
          ) : null}

          <Cluster justify="space-between">
            <p className="campaign-result-count" role="status">
              {history.rows.length.toLocaleString()} campaign
              {history.rows.length === 1 ? "" : "s"} shown
            </p>
            {busy ? <span className="campaign-refreshing">Refreshing…</span> : null}
          </Cluster>

          {history.rows.length ? (
            <div className="campaign-table-wrap">
              <table className="campaign-table">
                <caption>
                  <VisuallyHidden>
                    Campaign history matching the current filters
                  </VisuallyHidden>
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Keyword</th>
                    <th scope="col">State</th>
                    <th scope="col">Cells posted</th>
                    <th scope="col">First enqueue</th>
                    <th scope="col">Latest enqueue</th>
                    <th scope="col">Campaign date</th>
                  </tr>
                </thead>
                <tbody>
                  {history.rows.map((row) => (
                    <tr
                      key={`${row.keyword}-${row.state}-${row.campaign_date}`}
                    >
                      <th scope="row" data-label="Keyword">
                        {row.keyword}
                      </th>
                      <td data-label="State">
                        <span className="campaign-state">{row.state}</span>
                      </td>
                      <td data-label="Cells posted">
                        {row.cells_posted.toLocaleString()}
                      </td>
                      <td data-label="First enqueue">
                        {row.first_enqueued ?? "—"}
                      </td>
                      <td data-label="Latest enqueue">
                        {row.latest_enqueued ?? "—"}
                      </td>
                      <td data-label="Campaign date">{row.campaign_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState
              title="No campaign history"
              description="No campaign rows match the current search and state filters."
              action={
                <Button
                  onClick={() => {
                    setSearch("");
                    setState("");
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          )}
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}
