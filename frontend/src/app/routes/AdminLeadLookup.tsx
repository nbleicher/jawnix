import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Page, Section, Stack } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { ControlActionBar, leadControlRequest } from "../operator/actions";
import type { ControlAction } from "../operator/actions";
import { formatDatabase, formatDay, formatPhone } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import type { LeadEvidence } from "./AdminFulfillment";
import { BackToInventory, LeadLookupForm } from "./AdminInventory";
import { OverrideFields, validateOverride } from "./AdminLeadReport";
import { exclusionTypeLabel } from "./exclusionLists";
import "./AdminInventory.css";

/**
 * Lead lookup (P5): everything about one phone number — where it lives,
 * whether it is usable and why not, every delivery and listing, the controls
 * on it, and who could receive it next. Correct and suppress act here, each
 * behind a reason.
 */

interface RecordLink {
  id: number;
  name: string;
  href: string;
}

export interface LookupDelivery {
  id: number;
  deliveredAt: string;
  customer: RecordLink | null;
  customerName: string;
  agencyName: string;
  batch: { number: number; href: string } | null;
  database: string;
  title: string;
  state: string;
  disposition: string | null;
}

export interface LookupListing {
  database: "db1" | "db2";
  observedAt: string | null;
  source: string;
  title: string;
  state: string;
  valid: boolean;
  current?: boolean;
}

export interface LookupReport {
  id: string;
  href: string;
  reasonLabel: string;
  status: string;
  createdAt: string;
  customerName: string;
}

export interface LookupHold {
  id: string;
  reasonLabel: string;
  active: boolean;
  createdAt: string;
  releasedAt: string | null;
  reportHref: string;
}

export interface LookupCorrection {
  id: string;
  action: string;
  title: string;
  state: string;
  reason: string;
  createdAt: string;
  basedOnLabel: string;
  active: boolean;
}

export interface LookupExclusion {
  id: string;
  filename: string;
  type: string;
  status: string;
  global: boolean;
  customerName: string;
  createdAt: string;
}

export interface NextRecipient {
  key: string;
  label: string;
  href?: string;
  status: "never" | "blocked" | "later" | "now";
  reason: string;
  at?: string;
  database?: string;
  count: number;
  customers: string[];
}

export interface LeadLookupResult {
  query: string;
  phone: string | null;
  valid: boolean;
  exclusions: LookupExclusion[];
  lead: {
    id: number;
    phone: string;
    title: string;
    state: string;
    niche: string;
    firstSeenAt: string | null;
    lastDeliveredAt: string | null;
  } | null;
  usable?: { usable: boolean; reasons: { kind: string; label: string; detail: string }[] };
  databases?: {
    db1: { member: boolean; source: string };
    db2: {
      member: boolean;
      state: string;
      upload: { id: string; filename: string; uploadedBy: string; at: string | null } | null;
    };
  };
  deliveries?: LookupDelivery[];
  listings?: LookupListing[];
  reports?: LookupReport[];
  holds?: LookupHold[];
  suppression?: { suppressed: boolean; reason: string; restoreNotice: string };
  corrections?: LookupCorrection[];
  nextRecipients?: NextRecipient[];
  evidence?: LeadEvidence;
  actions?: ControlAction[];
}

export async function adminLeadLookupLoader({
  request,
}: LoaderFunctionArgs): Promise<LeadLookupResult | null> {
  const url = new URL(request.url);
  const phone = url.searchParams.get("phone")?.trim();
  const lead = url.searchParams.get("lead")?.trim();
  if (phone) return api<LeadLookupResult>(`/api/admin/leads/lookup?phone=${encodeURIComponent(phone)}`);
  if (lead && /^\d+$/.test(lead)) return api<LeadLookupResult>(`/api/admin/leads/lookup?lead=${lead}`);
  return null;
}

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

function recipientValue(row: NextRecipient): string {
  switch (row.reason) {
    case "delivered":
      return `Never · delivered ${formatDay(row.at)}`;
    case "shared_history":
      return "Never · shared history";
    case "cooldown":
      return `From ${formatDay(row.at)}`;
    case "eligible":
      return "Now";
    case "not_usable":
      return "Not while it is not usable";
    case "not_in_database":
      return `Not in ${formatDatabase(row.database)}`;
    case "not_appointed":
      return "Not appointed";
    case "customer_exclusion":
      return "On their exclusion list";
    case "niche_policy":
      return "Niche policy";
    default:
      return row.reason.replaceAll("_", " ");
  }
}

function recipientFacts(rows: NextRecipient[]): Fact[] {
  return rows.map((row) => {
    // A group says how many Customers it covers; one Customer is named.
    const single = row.key.startsWith("customer:");
    const label = single ? row.label : `${row.label} (${row.count})`;
    // Lines break only between the " · " parts, never inside a date.
    const value = recipientValue(row)
      .split(" · ")
      .map((part) => part.replaceAll(" ", "\u00a0"))
      .join(" · ");
    return { term: label, description: value };
  });
}

const REPORT_TONES: Record<string, "danger" | "success" | "neutral"> = {
  open: "danger",
  corrected: "success",
  suppressed: "success",
  dismissed: "neutral",
};

function EvidenceSection({ result }: { result: LeadLookupResult }) {
  const reports = result.reports ?? [];
  const holds = result.holds ?? [];
  const corrections = result.corrections ?? [];
  const exclusions = result.exclusions;
  const suppression = result.suppression;
  const empty =
    !reports.length && !holds.length && !corrections.length && !exclusions.length && !suppression?.suppressed;

  const reportColumns: DataColumn<LookupReport>[] = [
    { key: "reason", header: "Report", cell: (row) => <Link to={routePath(row.href)}>{row.reasonLabel}</Link> },
    { key: "customer", header: "Customer", cell: (row) => row.customerName },
    { key: "when", header: "Filed", cell: (row) => formatDay(row.createdAt) },
    {
      key: "status",
      header: "Status",
      cell: (row) => (
        <StatusBadge tone={REPORT_TONES[row.status] ?? "neutral"}>{row.status.replaceAll("_", " ")}</StatusBadge>
      ),
    },
  ];
  const holdColumns: DataColumn<LookupHold>[] = [
    { key: "reason", header: "Hold", cell: (row) => <Link to={routePath(row.reportHref)}>{row.reasonLabel}</Link> },
    { key: "placed", header: "Placed", cell: (row) => formatDay(row.createdAt) },
    { key: "released", header: "Released", cell: (row) => (row.releasedAt ? formatDay(row.releasedAt) : "—") },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge tone={row.active ? "danger" : "neutral"}>{row.active ? "Active" : "Released"}</StatusBadge>,
    },
  ];
  const correctionColumns: DataColumn<LookupCorrection>[] = [
    {
      key: "change",
      header: "Correction",
      cell: (row) =>
        row.action === "removed"
          ? "Removed"
          : [row.title, row.state].filter(Boolean).join(" · ") || "Applied",
    },
    { key: "over", header: "Over", cell: (row) => row.basedOnLabel },
    { key: "reason", header: "Reason", cell: (row) => row.reason },
    { key: "when", header: "When", cell: (row) => formatDay(row.createdAt) },
    {
      key: "status",
      header: "Status",
      cell: (row) => <StatusBadge tone={row.active ? "info" : "neutral"}>{row.active ? "In force" : "Superseded"}</StatusBadge>,
    },
  ];
  const exclusionColumns: DataColumn<LookupExclusion>[] = [
    { key: "file", header: "Exclusion list", cell: (row) => row.filename },
    { key: "by", header: "Uploaded by", cell: (row) => row.customerName },
    { key: "type", header: "Type", cell: (row) => exclusionTypeLabel(row.type) },
    {
      key: "scope",
      header: "Scope",
      cell: (row) => (
        <StatusBadge tone={row.global ? "info" : "neutral"}>{row.global ? "Every customer" : "Uploader only"}</StatusBadge>
      ),
    },
  ];

  return (
    <Section title="Reports, holds, and suppression">
      {empty ? (
        <Text size="sm" tone="muted">
          No reports, holds, suppressions, corrections, or exclusion lists.
        </Text>
      ) : (
        <Stack gap={4}>
          {suppression?.suppressed ? (
            <FactList
              label="Suppression"
              items={[
                { term: "Suppressed", description: suppression.reason || "No reason recorded" },
                { term: "Restoring", description: suppression.restoreNotice },
              ]}
            />
          ) : null}
          {reports.length ? (
            <DataTable label="Lead reports" columns={reportColumns} rows={reports} rowKey={(row) => row.id} />
          ) : null}
          {holds.length ? (
            <DataTable label="Eligibility holds" columns={holdColumns} rows={holds} rowKey={(row) => row.id} />
          ) : null}
          {corrections.length ? (
            <DataTable
              label="Lead corrections"
              columns={correctionColumns}
              rows={corrections}
              rowKey={(row) => row.id}
            />
          ) : null}
          {exclusions.length ? (
            <DataTable
              label="Exclusion lists with this phone"
              columns={exclusionColumns}
              rows={exclusions}
              rowKey={(row) => row.id}
            />
          ) : null}
        </Stack>
      )}
    </Section>
  );
}

function NotFound({ result }: { result: LeadLookupResult }) {
  if (!result.valid) {
    return (
      <Text role="status">
        “{result.query}” is not a phone number. Enter ten digits, with or without punctuation.
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      <Text role="status">
        No lead in DB1 or DB2 has the number {result.phone ? formatPhone(result.phone) : result.query}.
      </Text>
      {result.exclusions.length ? <EvidenceSection result={result} /> : null}
    </Stack>
  );
}

function LookupResult({ result }: { result: LeadLookupResult }) {
  const lead = result.lead!;
  const usable = result.usable ?? { usable: true, reasons: [] };
  const databases = result.databases!;
  const deliveries = result.deliveries ?? [];
  const listings = result.listings ?? [];
  const latest = deliveries[0];
  const both = databases.db1.member && databases.db2.member;
  const databaseValue = both ? "DB1 + DB2" : databases.db2.member ? "DB2" : databases.db1.member ? "DB1" : "Neither";
  const databaseMeta = [
    databases.db1.member ? databases.db1.source : "",
    databases.db2.member && databases.db2.upload ? `CSV upload ${databases.db2.upload.filename}` : "",
  ]
    .filter(Boolean)
    .join(" and ");

  const deliveryColumns: DataColumn<LookupDelivery>[] = [
    { key: "when", header: "When", cell: (row) => formatDay(row.deliveredAt) },
    {
      key: "customer",
      header: "Customer",
      cell: (row) => (row.customer ? <Link to={routePath(row.customer.href)}>{row.customer.name}</Link> : row.customerName || "—"),
    },
    {
      key: "batch",
      header: "Batch",
      cell: (row) => (row.batch ? <Link to={routePath(row.batch.href)}>#{row.batch.number}</Link> : "—"),
    },
    { key: "db", header: "DB", cell: (row) => formatDatabase(row.database) },
    {
      key: "as",
      header: "Delivered as",
      cell: (row) => [row.title, row.state].filter(Boolean).join(" · ") || "—",
    },
    {
      key: "disposition",
      header: "Disposition",
      cell: (row) =>
        row.disposition ?? (
          <Text as="span" size="sm" tone="muted">
            None yet
          </Text>
        ),
    },
  ];
  const listingColumns: DataColumn<LookupListing>[] = [
    { key: "observed", header: "Observed", cell: (row) => formatDay(row.observedAt) },
    { key: "source", header: "Source", cell: (row) => row.source },
    { key: "title", header: "Title", cell: (row) => row.title || "—" },
    { key: "state", header: "State", cell: (row) => row.state || "—" },
    { key: "valid", header: "Valid", cell: (row) => (row.valid ? "Yes" : "No") },
  ];

  const actions = result.actions ?? [];
  const evidence = result.evidence;

  return (
    <>
      <KpiRow label="Lead summary">
        <Kpi
          label="Lead"
          value={<span className="jx-lookup-phone">{formatPhone(lead.phone)}</span>}
          meta={[lead.title || "No title", lead.state, lead.niche].filter(Boolean).join(" · ")}
        />
        <Kpi
          label="Usable"
          value={usable.usable ? "Yes" : "No"}
          alert={!usable.usable}
          meta={
            usable.usable
              ? "Not suppressed · no hold · not excluded"
              : usable.reasons.map((reason) => reason.label).join(" · ")
          }
        />
        <Kpi label="Databases" value={databaseValue} meta={databaseMeta || "Not in either database"} />
        <Kpi
          label="Deliveries"
          value={deliveries.length.toLocaleString("en-US")}
          meta={latest ? `Last ${formatDay(latest.deliveredAt)} to ${latest.customer?.name ?? latest.customerName}` : "Never delivered"}
        />
      </KpiRow>
      <RecordColumns
        main={
          <>
            <Section title="Deliveries">
              <DataTable
                label="Deliveries"
                columns={deliveryColumns}
                rows={deliveries}
                rowKey={(row) => String(row.id)}
                empty="Never delivered to any Customer."
              />
            </Section>
            <Section title="Listing history">
              <DataTable
                label="Listing history"
                columns={listingColumns}
                rows={listings}
                rowKey={(row) => `${row.database}:${row.source}:${row.observedAt ?? ""}`}
                empty="No listing observations recorded."
              />
            </Section>
            <EvidenceSection result={result} />
          </>
        }
        aside={
          <>
            <Panel title="Who can receive it next">
              {result.nextRecipients?.length ? (
                <FactList
                  label="Who can receive it next"
                  className="jx-lookup-recipients"
                  items={recipientFacts(result.nextRecipients)}
                />
              ) : (
                <Text size="sm" tone="muted">
                  No active Customers.
                </Text>
              )}
            </Panel>
            <Panel title="Actions">
              <ControlActionBar
                actions={actions}
                resolve={leadControlRequest(lead.id)}
                settled="No action is available for this lead."
                validate={validateOverride}
                extras={(action, { values, set }) =>
                  action.requiresOverride && evidence ? (
                    <OverrideFields evidence={evidence} values={values} set={set} />
                  ) : null
                }
              />
              <Text size="xs" tone="muted">
                Each takes a reason and is recorded in Activity.
              </Text>
            </Panel>
          </>
        }
      />
    </>
  );
}

export function AdminLeadLookupRoute() {
  const result = useLoaderData<LeadLookupResult | null>();
  useDocumentTitle(result?.lead ? `Lead ${formatPhone(result.lead.phone)}` : "Lead lookup");
  const initial = result?.lead ? formatPhone(result.lead.phone) : (result?.query ?? "");
  return (
    <Page
      title="Lead lookup"
      width="data"
      density="data"
      description="Everything about one phone number: where it lives, whether it is usable, and every delivery."
    >
      <LeadLookupForm initial={initial} />
      {result === null ? (
        <Text size="sm" tone="muted">
          Enter a phone number to see its databases, deliveries, listings, and who can receive it next.
        </Text>
      ) : result.lead ? (
        <LookupResult result={result} />
      ) : (
        <NotFound result={result} />
      )}
      <BackToInventory />
    </Page>
  );
}
