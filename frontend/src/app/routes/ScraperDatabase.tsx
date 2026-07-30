import { useMemo, useState } from "react";
import {
  Form,
  Link,
  useLoaderData,
  useNavigate,
  useRevalidator,
} from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { EmptyState, ErrorState } from "../../design-system/primitives/feedback";
import {
  Field,
  Fieldset,
  Input,
  Select,
} from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { Text } from "../../design-system/primitives/typography";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  multiStateExportHref,
  regenerateStoredExports,
  stateExportHref,
  storedExportHref,
} from "./scraperDatabaseData";
import type {
  DatabaseBrowsePage,
  DatabaseBusiness,
  DatabaseNiche,
  DatabaseStateDetail,
  DatabaseStateSummary,
  DatabaseWorkspace,
  StoredExport,
} from "./scraperDatabaseData";

import {
  WORKSPACE_ROOT,
  WORKSPACE_SECTIONS,
  workspaceRail,
} from "./scraperWorkspaceNav";
import "./ScraperDatabase.css";

const WORKSPACE = "/app/admin/acquisition/scraper/workspace";
const DATABASE = `${WORKSPACE}/database`;


function count(value: number): string {
  return value.toLocaleString();
}

function lastSuccess(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "never";
}

function queryHref(
  browse: DatabaseBrowsePage,
  page: number,
): string {
  const query = new URLSearchParams();
  if (browse.search) query.set("search", browse.search);
  if (browse.state) query.set("state", browse.state);
  query.set("page", String(page));
  return `${DATABASE.replace(/^\/app/, "")}?${query.toString()}`;
}

function download(url: string) {
  window.location.assign(url);
}

function StateCard({
  state,
  checked,
  onChange,
}: {
  state: DatabaseStateSummary;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const inputId = `database-state-${state.state}`;
  return (
    <Card as="article" className="database-state" padding={4}>
      <div className="database-state__select">
        <input
          id={inputId}
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.currentTarget.checked)}
        />
        <label htmlFor={inputId}>Select {state.state}</label>
      </div>
      <Link
        className="database-state__link"
        to={`/admin/acquisition/scraper/workspace/database/states/${state.state}`}
      >
        <span className="database-state__code">{state.state}</span>
        <span className="database-state__metric">
          <strong>{count(state.businesses)}</strong>
          <small>businesses</small>
        </span>
        <span className="database-state__facts">
          <span>{count(state.unique_phones)} unique phones</span>
          <span>{count(state.niches)} Niches</span>
        </span>
        <span className="database-state__browse">Browse state →</span>
      </Link>
    </Card>
  );
}

function BusinessRows({ records }: { records: DatabaseBusiness[] }) {
  if (!records.length) {
    return (
      <EmptyState
        title="No matching businesses"
        description="Try a different business name, phone number, website, or state."
      />
    );
  }
  return (
    <div className="database-table-wrap">
      <table className="database-table">
        <thead>
          <tr>
            <th>Business</th>
            <th>Phone</th>
            <th>State</th>
            <th>Niche</th>
            <th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record, index) => (
            <tr
              key={`${record.title}-${record.phone ?? "none"}-${record.last_seen}-${index}`}
            >
              <td data-label="Business">
                <strong>{record.title}</strong>
                {record.website ? (
                  <a
                    href={record.website}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {record.website}
                  </a>
                ) : null}
              </td>
              <td data-label="Phone">{record.phone ?? "—"}</td>
              <td data-label="State">{record.state ?? "—"}</td>
              <td data-label="Niche">{record.niche ?? "—"}</td>
              <td data-label="Last seen">{record.last_seen}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StoredExports({
  exports,
  states,
  onRegenerated,
}: {
  exports: StoredExport[];
  states: DatabaseStateSummary[];
  onRegenerated: (exports: StoredExport[]) => void;
}) {
  const navigate = useNavigate();
  const [state, setState] = useState(states[0]?.state ?? "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [outcome, setOutcome] = useState("");

  async function regenerate() {
    setConfirming(false);
    setBusy(true);
    setFailure("");
    setOutcome("");
    try {
      const result = await regenerateStoredExports(state);
      onRegenerated(result.stored_exports);
      setOutcome(`${result.generated} regenerated. Stored downloads are current.`);
    } catch (caught) {
      const status = (caught as { status?: number }).status;
      if (status === 401 || status === 403) {
        void navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setFailure("Stored exports could not be regenerated. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      id="stored-exports"
      title="Stored exports"
      description="Regeneration preserves the existing STATE.csv files and their two-column phone,title format."
    >
      <Stack gap={4}>
        <Cluster align="end">
          <Field
            label="Reference state"
            description="The Scraper validates this state, then refreshes the stored state export set."
          >
            <Select
              value={state}
              onChange={(event) => setState(event.currentTarget.value)}
            >
              {states.map((item) => (
                <option key={item.state} value={item.state}>
                  {item.state}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            busy={busy}
            busyLabel="Regenerating exports…"
            disabled={!state}
            onClick={() => setConfirming(true)}
          >
            Regenerate stored exports
          </Button>
        </Cluster>
        {failure ? <p className="database-message database-message--error" role="alert">{failure}</p> : null}
        {outcome ? <p className="database-message database-message--success" role="status">{outcome}</p> : null}
        {exports.length ? (
          <Grid minColumnWidth="14rem" gap={3}>
            {exports.map((item) => (
              <Card as="article" key={item.filename} padding={4}>
                <Stack gap={2}>
                  <Text weight="bold">{item.filename}</Text>
                  <Text size="sm" tone="muted">{item.size_label}</Text>
                  <ActionLink href={storedExportHref(item.filename)}>
                    Download stored export
                  </ActionLink>
                </Stack>
              </Card>
            ))}
          </Grid>
        ) : (
          <EmptyState
            title="No stored exports reported"
            description="Regenerate the current state files to make their stored downloads available here."
          />
        )}
      </Stack>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void regenerate()}
        title="Regenerate stored exports?"
        consequence="The Scraper will replace its stored STATE.csv files with the current available phone pool. Current database records are not changed."
        confirmLabel="Regenerate exports"
        cancelLabel="Keep current files"
        busy={busy}
      />
    </Section>
  );
}

export function ScraperDatabaseRoute() {
  const data = useLoaderData<DatabaseWorkspace>();
  const revalidator = useRevalidator();
  const [selected, setSelected] = useState<string[]>([]);
  const [storedExports, setStoredExports] = useState(data.stored_exports);
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle("Scraper Database");

  if (
    data.service_state === "unavailable"
    || !data.totals
    || !data.browse
  ) {
    return (
      <Page
        title="Scraper Database"
        description="Browse acquired Scraper records and create CSV exports."
      >
        <TerminalWorkspace
          status="OFFLINE / DATABASE UNAVAILABLE"
          tone="offline"
          destinations={workspaceRail(`${WORKSPACE_ROOT}/database`)}
        >
          <ErrorState
            title="Scraper database unavailable"
            description={`The private record store did not respond. No totals, records, or export controls are available. Last successful connection: ${lastSuccess(data.last_successful_at)}.`}
            onRetry={() => void revalidator.revalidate()}
          />
        </TerminalWorkspace>
      </Page>
    );
  }

  const browse = data.browse;
  const selectedSet = new Set(selected);
  return (
    <Page
      title="Scraper Database"
      description="Search the acquired record store and download current or stored CSV exports."
    >
      <TerminalWorkspace
        status="ONLINE / DATABASE"
        destinations={workspaceRail(`${WORKSPACE_ROOT}/database`, {
          sections: WORKSPACE_SECTIONS.database,
        })}
      >
        <Stack gap={6}>
          <Grid minColumnWidth="13rem" gap={3}>
            <Card as="section" aria-label="All businesses" padding={4}>
              <Text size="sm" tone="muted">All businesses</Text>
              <Text size="lg" weight="bold">{count(data.totals.businesses)}</Text>
            </Card>
            <Card as="section" aria-label="Exportable phones" padding={4}>
              <Text size="sm" tone="muted">Exportable phones</Text>
              <Text size="lg" weight="bold">{count(data.totals.unique_phones)}</Text>
            </Card>
            <Card as="section" aria-label="States with records" padding={4}>
              <Text size="sm" tone="muted">States with records</Text>
              <Text size="lg" weight="bold">{count(data.states.length)}</Text>
            </Card>
          </Grid>

          <Section
            id="database-states"
            title="State databases"
            description="Select one or more states for a combined current CSV, or open a state for Niche-level context."
          >
            <Stack gap={4}>
              <Fieldset legend="States to download">
                <Cluster>
                  <Button
                    variant="ghost"
                    onClick={() => setSelected(data.states.map((item) => item.state))}
                  >
                    Select all
                  </Button>
                  <Button
                    variant="ghost"
                    disabled={!selected.length}
                    onClick={() => setSelected([])}
                  >
                    Clear selection
                  </Button>
                  <Text size="sm" tone="muted" aria-live="polite">
                    {selected.length} selected
                  </Text>
                  <Button
                    variant="primary"
                    disabled={!selected.length}
                    onClick={() => download(multiStateExportHref(selected))}
                  >
                    Download selected states
                  </Button>
                </Cluster>
                <Grid minColumnWidth="15rem" gap={3}>
                  {data.states.map((item) => (
                    <StateCard
                      key={item.state}
                      state={item}
                      checked={selectedSet.has(item.state)}
                      onChange={(checked) =>
                        setSelected((current) =>
                          checked
                            ? [...current, item.state]
                            : current.filter((value) => value !== item.state),
                        )
                      }
                    />
                  ))}
                </Grid>
              </Fieldset>
            </Stack>
          </Section>

          <Section
            id="database-browse"
            title="Browse records"
            description="Search the currently supported business name, phone, and website fields, with an optional state filter."
          >
            <Stack gap={4}>
              <Form className="database-filters" method="get">
                <Field label="Search records">
                  <Input
                    type="search"
                    name="search"
                    defaultValue={browse.search}
                    placeholder="Business name, phone, or website"
                  />
                </Field>
                <Field label="State">
                  <Select name="state" defaultValue={browse.state}>
                    <option value="">All states</option>
                    {data.states.map((item) => (
                      <option key={item.state} value={item.state}>
                        {item.state}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Button type="submit" variant="primary">Search</Button>
                {(browse.search || browse.state) ? (
                  <ActionLink href={DATABASE}>Clear filters</ActionLink>
                ) : null}
              </Form>
              <div className="database-results__head">
                <Text weight="bold">{count(browse.total)} matching businesses</Text>
                <StatusBadge tone="info">
                  Page {browse.page} of {browse.pages}
                </StatusBadge>
              </div>
              <BusinessRows records={browse.records} />
              <nav className="database-pagination" aria-label="Business results pages">
                {browse.has_previous ? (
                  <Link to={queryHref(browse, browse.page - 1)}>← Previous</Link>
                ) : (
                  <span aria-disabled="true">← Previous</span>
                )}
                <span>Page {browse.page}</span>
                {browse.has_next ? (
                  <Link to={queryHref(browse, browse.page + 1)}>Next →</Link>
                ) : (
                  <span aria-disabled="true">Next →</span>
                )}
              </nav>
            </Stack>
          </Section>

          <StoredExports
            exports={storedExports}
            states={data.states}
            onRegenerated={setStoredExports}
          />
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}

function NicheTable({
  state,
  niches,
  selected,
  onChange,
}: {
  state: string;
  niches: DatabaseNiche[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  if (!niches.length) {
    return (
      <EmptyState
        title="No Niches found"
        description="This state has no Niche groupings available for export."
      />
    );
  }
  return (
    <div className="database-table-wrap">
      <table className="database-table database-table--niches">
        <thead>
          <tr>
            <th><span className="database-sr-only">Select</span></th>
            <th>Niche</th>
            <th>Businesses</th>
            <th>Unique phones</th>
            <th>Download</th>
          </tr>
        </thead>
        <tbody>
          {niches.map((niche) => {
            const inputId = `database-niche-${state}-${niche.key}`;
            return (
              <tr key={niche.key}>
                <td data-label="Select">
                  <input
                    id={inputId}
                    type="checkbox"
                    checked={selectedSet.has(niche.key)}
                    onChange={(event) =>
                      onChange(
                        event.currentTarget.checked
                          ? [...selected, niche.key]
                          : selected.filter((value) => value !== niche.key),
                      )
                    }
                  />
                  <label className="database-sr-only" htmlFor={inputId}>
                    Select {niche.label}
                  </label>
                </td>
                <td data-label="Niche"><strong>{niche.label}</strong></td>
                <td data-label="Businesses">{count(niche.businesses)}</td>
                <td data-label="Unique phones">{count(niche.unique_phones)}</td>
                <td data-label="Download">
                  <ActionLink href={stateExportHref(state, [niche.key])}>
                    Download {niche.label}
                  </ActionLink>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ScraperDatabaseStateRoute() {
  const data = useLoaderData<DatabaseStateDetail>();
  const revalidator = useRevalidator();
  const [selected, setSelected] = useState<string[]>([]);
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle(`${data.state} Scraper Database`);
  const detailPath = `${WORKSPACE_ROOT}/database/states/${data.state}`;
  const destinations = workspaceRail(detailPath, {
    pageLabel: `${data.state} database`,
    ...(data.service_state === "connected" && data.totals
      ? { sections: WORKSPACE_SECTIONS.databaseState }
      : {}),
  });

  if (data.service_state === "unavailable" || !data.totals) {
    return (
      <Page title={`${data.state} Scraper Database`}>
        <TerminalWorkspace
          status="OFFLINE / STATE DATABASE UNAVAILABLE"
          tone="offline"
          destinations={destinations}
        >
          <ErrorState
            title={`${data.state} database unavailable`}
            description={`The state database did not respond, so no Niche totals or downloads are available. Last successful connection: ${lastSuccess(data.last_successful_at)}.`}
            onRetry={() => void revalidator.revalidate()}
          />
        </TerminalWorkspace>
      </Page>
    );
  }

  return (
    <Page
      title={`${data.state} Scraper Database`}
      description="State totals and current phone-bearing businesses grouped by Niche."
      actions={
        <>
          <ActionLink href={DATABASE}>All state databases</ActionLink>
          <ActionLink
            href={stateExportHref(data.state)}
            variant="primary"
          >
            Download entire state
          </ActionLink>
        </>
      }
    >
      <TerminalWorkspace
        status={`ONLINE / ${data.state} DATABASE`}
        destinations={destinations}
      >
        <Stack gap={6}>
          <Grid id="state-database" minColumnWidth="13rem" gap={3}>
            <Card as="section" aria-label="State businesses" padding={4}>
              <Text size="sm" tone="muted">Businesses</Text>
              <Text size="lg" weight="bold">{count(data.totals.businesses)}</Text>
            </Card>
            <Card as="section" aria-label="State unique phones" padding={4}>
              <Text size="sm" tone="muted">Unique phones</Text>
              <Text size="lg" weight="bold">{count(data.totals.unique_phones)}</Text>
            </Card>
            <Card as="section" aria-label="State Niches" padding={4}>
              <Text size="sm" tone="muted">Niches</Text>
              <Text size="lg" weight="bold">{count(data.totals.niches)}</Text>
            </Card>
          </Grid>
          <Section
            id="state-niches"
            title="Niches"
            description="Download one Niche, a selected set, or the full state. Every current CSV keeps business_name, phone_number, state."
          >
            <Stack gap={4}>
              <Cluster>
                <Button
                  variant="ghost"
                  onClick={() => setSelected(data.niches.map((item) => item.key))}
                >
                  Select all
                </Button>
                <Button
                  variant="ghost"
                  disabled={!selected.length}
                  onClick={() => setSelected([])}
                >
                  Clear selection
                </Button>
                <Text size="sm" tone="muted" aria-live="polite">
                  {selected.length} selected
                </Text>
                <Button
                  variant="primary"
                  disabled={!selected.length}
                  onClick={() => download(stateExportHref(data.state, selected))}
                >
                  Download selected Niches
                </Button>
              </Cluster>
              <NicheTable
                state={data.state}
                niches={data.niches}
                selected={selected}
                onChange={setSelected}
              />
            </Stack>
          </Section>
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}
