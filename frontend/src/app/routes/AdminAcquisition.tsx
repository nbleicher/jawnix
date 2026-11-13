import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLoaderData, useRevalidator, useSearchParams } from "react-router";

import { ActionLink, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Page, Section } from "../../design-system/primitives/layout";
import { PanelLayout, SidePanel } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { ControlActionBar } from "../operator/actions";
import type { ControlAction, ControlResolver } from "../operator/actions";
import { NICHE_CONFIRM, NICHE_DENY } from "../operator/decisionActions";
import { formatCount, formatDay, plural } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./AdminAcquisition.css";

/**
 * Acquisition's remaining pieces, under Setup (A5): Scraper Configuration
 * versions with the dormant Source Recommendations that schedule them, and
 * every Niche mapping.
 *
 * Its daily decisions — held Scrape Anomalies, Niche proposals, Exclusion
 * Lists — are on Today; the Nightly Review history is on Insights. Both
 * pages read the one Acquisition contract and post every decision back to
 * the endpoint that owns it, so neither becomes a second way to change
 * acquisition. A Source Recommendation decision sends the evidence checksum
 * it displayed: approving numbers that moved after they were read is refused
 * server-side rather than applied to numbers nobody saw.
 */

export interface ScrapeAnomalyRow {
  id: string;
  status: string;
  scraperRunId: number;
  decidable: boolean;
}

export interface RecommendationRow {
  id: string;
  niche: string;
  segment: string;
  action: string;
  status: string;
  evidence: Record<string, unknown>;
  evidenceChecksum: string;
  configurationVersion: number | null;
  decisionBy: string;
  decisionReason: string;
  decidedAt: string | null;
  createdAt: string;
  resultingConfigurationId: string | null;
  decidable: boolean;
}

export interface NicheRow {
  segment: string;
  state: string;
  keyword: string;
  niche: string;
  confirmed: boolean;
  proposalSource: string;
  evidence: Record<string, unknown>;
  confirmedBy: string;
  confirmedAt: string | null;
  denied?: boolean;
  deniedBy?: string;
  deniedAt?: string | null;
}

export interface ConfigurationRow {
  id: string;
  version: number;
  checksum: string;
  status: string;
  reason: string;
  createdAt: string;
  scheduledAt: string | null;
  activatedAt: string | null;
  basedOnConfigurationId: string | null;
  segmentCount: number;
  anomalyThresholds: Record<string, unknown>;
}

/** The parts of `GET /api/admin/acquisition` these pages read. */
export interface AcquisitionData {
  scrapeAnomalies: ScrapeAnomalyRow[];
  sourceRecommendations: RecommendationRow[];
  nicheMappings: NicheRow[];
  scraperConfigurations: ConfigurationRow[];
}

async function acquisitionLoader(): Promise<AcquisitionData> {
  return api<AcquisitionData>("/api/admin/acquisition");
}

export const configurationsLoader = acquisitionLoader;
export const nicheMappingsLoader = acquisitionLoader;

function words(value: string): string {
  const text = value.replaceAll("_", " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "Unknown";
}

/** Who proposed a Niche, in words. */
const PROPOSAL_SOURCES: Record<string, string> = {
  ai_openrouter: "AI proposal",
  scraper_configuration: "Scraper configuration",
  historical_provenance: "Past deliveries",
};

function proposedBy(source: string): string {
  return PROPOSAL_SOURCES[source] ?? words(source || "unknown");
}

// ------------------------------------------------------ Configuration versions

// Black is in force or about to be; grey is history (V4).
const CONFIGURATION_TONES: Record<string, StatusTone> = {
  active: "success",
  scheduled: "info",
  restored: "info",
};

function configurationColumns(now: Date): DataColumn<ConfigurationRow>[] {
  return [
    {
      key: "version",
      header: "Version",
      cell: (item) => (
        <Link to={`/admin/acquisition/configurations/${item.id}`}>v{item.version}</Link>
      ),
    },
    {
      key: "status",
      header: "Status",
      cell: (item) => (
        <StatusBadge tone={CONFIGURATION_TONES[item.status] ?? "neutral"}>
          {words(item.status)}
        </StatusBadge>
      ),
    },
    { key: "segments", header: "Segments", numeric: true, cell: (item) => formatCount(item.segmentCount) },
    {
      key: "reason",
      header: "Reason",
      cell: (item) =>
        item.basedOnConfigurationId ? `${item.reason} (rolled forward from an earlier version)` : item.reason,
    },
    { key: "created", header: "Created", cell: (item) => formatDay(item.createdAt, now) },
    {
      key: "activated",
      header: "Activated",
      cell: (item) =>
        item.activatedAt
          ? formatDay(item.activatedAt, now)
          : item.scheduledAt
            ? `Scheduled ${formatDay(item.scheduledAt, now)}`
            : "—",
    },
  ];
}

const RECOMMENDATION_APPROVE: ControlAction = {
  name: "approve",
  label: "Approve",
  consequence:
    "Records the approval. Where applying is enabled it schedules a new Scraper Configuration version carrying this change; in shadow mode it records the decision only. Either way no existing version is rewritten and no Scrape Run starts.",
  destructive: false,
  requiresReason: true,
};

const RECOMMENDATION_DENY: ControlAction = {
  name: "deny",
  label: "Deny",
  consequence:
    "Records the refusal. Acquisition is unchanged, and this recommendation stays quiet until materially new evidence accrues.",
  destructive: true,
  requiresReason: true,
};

function recommendationRequest(item: RecommendationRow): ControlResolver {
  return (action, reason) => ({
    url: `/api/admin/source-recommendations/${item.id}/${action.name}`,
    method: "POST",
    body: { reason, evidenceChecksum: item.evidenceChecksum },
  });
}

function evidenceOf(item: RecommendationRow) {
  const counts = (item.evidence?.["counts"] ?? {}) as Record<string, number>;
  const analysis = (item.evidence?.["analysis"] ?? {}) as Record<string, unknown>;
  return { counts, analysis };
}

function RecommendationPanel({
  item,
  onClose,
  onChanged,
  autoFocus,
  returnFocus,
}: {
  item: RecommendationRow;
  onClose: () => void;
  onChanged: () => void;
  autoFocus: boolean;
  returnFocus: () => HTMLElement | null;
}) {
  const { counts, analysis } = evidenceOf(item);
  return (
    <SidePanel
      eyebrow={`Source recommendation · ${item.decidable ? "Waiting" : words(item.status)}`}
      title={`${words(item.action)} · ${segmentName(item.segment)}`}
      onClose={onClose}
      autoFocus={autoFocus}
      returnFocus={returnFocus}
      actions={
        item.decidable ? (
          <ControlActionBar
            actions={[RECOMMENDATION_APPROVE, RECOMMENDATION_DENY]}
            resolve={recommendationRequest(item)}
            settled=""
            primaryAction="approve"
            onConfirmed={onChanged}
            refresh={onChanged}
          />
        ) : undefined
      }
    >
      {/* The evidence must be readable before the decision, not after. */}
      <FactList
        label="Recommendation evidence"
        items={[
          { term: "Niche", description: item.niche || "—" },
          { term: "Worked leads", description: formatCount(counts["worked"] ?? 0) },
          { term: "Quality ratings", description: formatCount(counts["rated"] ?? 0) },
          { term: "Eligibility", description: words(String(analysis["eligibility"] ?? "unknown")) },
          {
            term: "Same-niche peers",
            description:
              analysis["peerSegmentCount"] !== undefined ? String(analysis["peerSegmentCount"]) : "—",
          },
          {
            term: "Evidence from",
            description: item.configurationVersion !== null ? `Configuration v${item.configurationVersion}` : "—",
          },
        ]}
      />
      {!item.decidable ? (
        <Text size="sm" tone="muted">
          Decided {formatDay(item.decidedAt)}
          {item.decisionReason ? `: ${item.decisionReason}` : "."}
        </Text>
      ) : null}
    </SidePanel>
  );
}

export function AdminConfigurationsRoute() {
  const data = useLoaderData<AcquisitionData>();
  const revalidator = useRevalidator();
  const selection = useSelectedRow("recommendation");
  const now = new Date();
  useDocumentTitle("Configuration versions");

  const pending = data.sourceRecommendations.filter((item) => item.decidable);
  const held = data.scrapeAnomalies.filter((item) => item.decidable).length;
  const open = data.sourceRecommendations.find((item) => item.id === selection.selected) ?? null;

  const recommendationColumns: DataColumn<RecommendationRow>[] = [
    {
      key: "segment",
      header: "Segment",
      cell: (item) => (
        <Link
          to={`?recommendation=${item.id}`}
          data-panel-trigger={item.id}
          onClick={selection.openFromLink(item.id)}
        >
          {segmentName(item.segment)}
        </Link>
      ),
    },
    { key: "action", header: "Proposal", cell: (item) => words(item.action) },
    { key: "niche", header: "Niche", cell: (item) => item.niche || "—" },
    {
      key: "worked",
      header: "Worked",
      numeric: true,
      cell: (item) => formatCount(evidenceOf(item).counts["worked"] ?? 0),
    },
    { key: "created", header: "Proposed", cell: (item) => formatDay(item.createdAt, now) },
  ];

  return (
    <Page
      title="Configuration versions"
      width="data"
      density="data"
      description="Every Scraper Configuration, newest first. Scheduling or rolling back never rewrites a version."
      actions={<ActionLink href="/app/admin/setup">Back to Setup</ActionLink>}
    >
      {held ? (
        <Text size="sm" tone="danger" role="status">
          {plural(held, "held Scrape Run")} {held === 1 ? "is" : "are"} waiting for a decision on{" "}
          <Link to="/admin/today">Today</Link>.
        </Text>
      ) : null}
      <PanelLayout
        panel={
          open ? (
            <RecommendationPanel
              key={open.id}
              item={open}
              onClose={selection.close}
              onChanged={() => void revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(open.id)}
            />
          ) : null
        }
      >
        <Section title="Versions">
          <DataTable
            label="Scraper Configuration versions"
            columns={configurationColumns(now)}
            rows={data.scraperConfigurations}
            rowKey={(item) => item.id}
            empty="The first version is imported from the Scraper's Source Segment contract."
          />
        </Section>
        <Section
          title="Source recommendations"
          description="Legacy proposals stay reviewable; new ones are no longer generated."
        >
          {pending.length ? (
            <DataTable
              label="Source recommendations"
              columns={recommendationColumns}
              rows={pending}
              rowKey={(item) => item.id}
              selectedKey={open?.id ?? null}
              onRowClick={(item) => selection.select(item.id)}
            />
          ) : (
            <Text size="sm" tone="muted">
              No source recommendation is waiting.
            </Text>
          )}
        </Section>
      </PanelLayout>
    </Page>
  );
}

// ---------------------------------------------------------------- Niche mappings

/** "roofing contractor · TX" for the stored "TX::roofing contractor". */
export function segmentName(segment: string): string {
  const [state, keyword] = segment.split("::");
  return keyword ? `${keyword} · ${state}` : segment;
}

type NicheStatus = "pending" | "confirmed" | "denied";

function nicheStatus(item: NicheRow): NicheStatus {
  if (item.confirmed) return "confirmed";
  if (item.denied) return "denied";
  return "pending";
}

const NICHE_STATUS: Record<NicheStatus, { label: string; tone: StatusTone }> = {
  pending: { label: "Awaiting review", tone: "danger" },
  confirmed: { label: "Confirmed", tone: "success" },
  denied: { label: "Denied", tone: "neutral" },
};

const NICHE_CORRECT: ControlAction = {
  ...NICHE_CONFIRM,
  label: "Correct Niche",
  consequence:
    "Replaces this Source Segment's confirmed Niche. Leads it delivers from now on carry the corrected Niche; history is unchanged.",
};

function nicheRequest(segment: string): ControlResolver {
  return (action, reason, values) => ({
    url: `/api/admin/source-niches/${encodeURIComponent(segment)}/${action.name}`,
    method: "POST",
    body: action.name === "confirm" ? { niche: (values.niche ?? "").trim(), reason } : { reason },
  });
}

const PAGE_SIZE = 50;

function NichePanel({
  item,
  onClose,
  onChanged,
  autoFocus,
  returnFocus,
}: {
  item: NicheRow;
  onClose: () => void;
  onChanged: () => void;
  autoFocus: boolean;
  returnFocus: () => HTMLElement | null;
}) {
  const status = nicheStatus(item);
  const actions =
    status === "pending" ? [NICHE_CONFIRM, NICHE_DENY] : status === "confirmed" ? [NICHE_CORRECT] : [NICHE_CONFIRM];
  return (
    <SidePanel
      eyebrow={`Niche mapping · ${NICHE_STATUS[status].label}`}
      title={segmentName(item.segment)}
      onClose={onClose}
      autoFocus={autoFocus}
      returnFocus={returnFocus}
      actions={
        <ControlActionBar
          actions={actions}
          resolve={nicheRequest(item.segment)}
          settled=""
          primaryAction="confirm"
          initialValues={(action) => (action.name === "confirm" ? { niche: item.niche } : {})}
          validate={(action, values) =>
            action.name === "confirm" && !(values.niche ?? "").trim() ? "A Niche is required." : ""
          }
          extras={(action, { values, set }) =>
            action.name === "confirm" ? (
              <Field label="Niche" required>
                <Input name="niche" value={values.niche ?? ""} onChange={(event) => set("niche", event.target.value)} />
              </Field>
            ) : null
          }
          onConfirmed={onChanged}
          refresh={onChanged}
        />
      }
    >
      <FactList
        label="Niche mapping"
        items={[
          { term: "Keyword", description: item.keyword },
          { term: "State", description: item.state },
          { term: status === "confirmed" ? "Niche" : "Proposed niche", description: item.niche || "None" },
          { term: "Proposed by", description: proposedBy(item.proposalSource) },
          ...(item.confirmedAt ? [{ term: "Confirmed", description: formatDay(item.confirmedAt) }] : []),
          ...(item.deniedAt ? [{ term: "Denied", description: formatDay(item.deniedAt) }] : []),
        ]}
      />
    </SidePanel>
  );
}

export function AdminNicheMappingsRoute() {
  const data = useLoaderData<AcquisitionData>();
  const revalidator = useRevalidator();
  const [params, setParams] = useSearchParams();
  const selection = useSelectedRow("segment");
  const now = new Date();
  useDocumentTitle("Niche mappings");

  const query = params.get("q") ?? "";
  const status = (params.get("status") ?? "") as NicheStatus | "";
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  function setFilter(key: string, value: string | null) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== "page") next.delete("page");
        return next;
      },
      { replace: true, preventScrollReset: true },
    );
  }

  // Typing filters after a pause, so each keystroke is not a navigation.
  const filter = useRef(setFilter);
  filter.current = setFilter;
  useEffect(() => {
    if (draft === query) return;
    const timer = window.setTimeout(() => filter.current("q", draft.trim() || null), 300);
    return () => window.clearTimeout(timer);
  }, [draft, query]);

  const counts = useMemo(() => {
    const result: Record<NicheStatus, number> = { pending: 0, confirmed: 0, denied: 0 };
    for (const item of data.nicheMappings) result[nicheStatus(item)] += 1;
    return result;
  }, [data.nicheMappings]);

  // Awaiting review first, then by state and keyword.
  const rows = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const order: Record<NicheStatus, number> = { pending: 0, confirmed: 1, denied: 2 };
    return data.nicheMappings
      .filter((item) => !status || nicheStatus(item) === status)
      .filter(
        (item) =>
          !needle ||
          [item.segment, item.state, item.keyword, item.niche].some((value) =>
            value.toLocaleLowerCase().includes(needle),
          ),
      )
      .sort(
        (a, b) =>
          order[nicheStatus(a)] - order[nicheStatus(b)] ||
          a.state.localeCompare(b.state) ||
          a.keyword.localeCompare(b.keyword),
      );
  }, [data.nicheMappings, query, status]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const shown = rows.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  const open = data.nicheMappings.find((item) => item.segment === selection.selected) ?? null;

  function pageHref(target: number) {
    const next = new URLSearchParams(params);
    next.delete("segment");
    if (target <= 1) next.delete("page");
    else next.set("page", String(target));
    const search = next.toString();
    return `/app/admin/setup/niche-mappings${search ? `?${search}` : ""}`;
  }

  const columns: DataColumn<NicheRow>[] = [
    {
      key: "segment",
      header: "Segment",
      cell: (item) => (
        <Link
          to={`?segment=${encodeURIComponent(item.segment)}`}
          data-panel-trigger={item.segment}
          onClick={selection.openFromLink(item.segment)}
        >
          {segmentName(item.segment)}
        </Link>
      ),
    },
    { key: "niche", header: "Niche", cell: (item) => item.niche || "None proposed" },
    {
      key: "status",
      header: "Status",
      cell: (item) => {
        const value = NICHE_STATUS[nicheStatus(item)];
        return <StatusBadge tone={value.tone}>{value.label}</StatusBadge>;
      },
    },
    { key: "source", header: "Proposed by", cell: (item) => proposedBy(item.proposalSource) },
    {
      key: "changed",
      header: "Decided",
      cell: (item) => formatDay(item.confirmedAt ?? item.deniedAt ?? null, now),
    },
  ];

  return (
    <Page
      title="Niche mappings"
      width="data"
      density="data"
      description="The Niche each Source Segment's leads carry. Proposals wait here and on Today until reviewed."
      actions={<ActionLink href="/app/admin/setup">Back to Setup</ActionLink>}
    >
      <div className="jx-niche-filters">
        <Field label="Search">
          <Input
            type="search"
            placeholder="State, keyword, or niche"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </Field>
        <Field label="Status">
          <Select value={status} onChange={(event) => setFilter("status", event.target.value || null)}>
            <option value="">All ({formatCount(data.nicheMappings.length)})</option>
            <option value="pending">Awaiting review ({formatCount(counts.pending)})</option>
            <option value="confirmed">Confirmed ({formatCount(counts.confirmed)})</option>
            <option value="denied">Denied ({formatCount(counts.denied)})</option>
          </Select>
        </Field>
      </div>
      <PanelLayout
        panel={
          open ? (
            <NichePanel
              key={`${open.segment}:${nicheStatus(open)}`}
              item={open}
              onClose={selection.close}
              onChanged={() => void revalidator.revalidate()}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus(open.segment)}
            />
          ) : null
        }
      >
        <DataTable
          label="Niche mappings"
          columns={columns}
          rows={shown}
          rowKey={(item) => item.segment}
          selectedKey={open?.segment ?? null}
          onRowClick={(item) => selection.select(item.segment)}
          empty="No Niche mapping matches these filters."
        />
        <Cluster gap={3} justify="space-between">
          <Text size="sm" tone="muted" role="status">
            {rows.length
              ? `Showing ${formatCount((current - 1) * PAGE_SIZE + 1)}–${formatCount(
                  Math.min(rows.length, current * PAGE_SIZE),
                )} of ${formatCount(rows.length)}`
              : "No mappings"}
          </Text>
          {pages > 1 ? (
            <nav aria-label="Pages" className="jx-niche-pages">
              {current > 1 ? (
                <ButtonLink variant="secondary" href={pageHref(current - 1)}>
                  Previous
                </ButtonLink>
              ) : null}
              <Text as="span" size="sm">
                Page {current} of {pages}
              </Text>
              {current < pages ? (
                <ButtonLink variant="secondary" href={pageHref(current + 1)}>
                  Next
                </ButtonLink>
              ) : null}
            </nav>
          ) : null}
        </Cluster>
      </PanelLayout>
    </Page>
  );
}
