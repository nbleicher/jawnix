import { Link } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { Panel } from "../../design-system/primitives/panel";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Text } from "../../design-system/primitives/typography";
import { routePath } from "../routePath";
import {
  formatCount,
  formatDatabase,
  formatDateTime,
  formatDay,
  formatRate,
  formatStates,
  plural,
  stageLabel,
} from "../operator/format";
import type { CustomerNeed } from "./AdminCustomers";
import type { ActivityPage } from "./AdminActivity";
import { formatUsdCents } from "./AdminCustomerControls";
import { formatAsOf } from "./OperatorNumbers";
import { customerTabHref } from "./customerDetailsData";
import type { CustomerDetailsData, CustomerSummaryView } from "./customerDetailsData";

/**
 * The Customer page's presentational blocks: the numbers row, the Needs list,
 * the recent-activity panel, and the one-line description under the name.
 */

/** The backend counts dependencies with its own storage keys, one of which is
 *  camelCase. Operators read record names, not storage keys. */
const DEPENDENCY_LABELS: Record<string, string> = {
  requests: "Batch Requests",
  distributions: "Distributions",
  outcomes: "Lead Outcomes",
  reports: "Lead Reports",
  dispositions: "Dispositions",
  profiles: "Profiles",
  userAccounts: "User Accounts",
  invitations: "Invitations",
  agencyMemberships: "Agency memberships",
  exclusionLists: "Exclusion Lists",
  creditLedgerEntries: "Credit Ledger entries",
  creditPurchases: "Credit Purchases",
  batchHolds: "Batch Holds",
  accountMigrations: "User Account migration records",
};

function dependencyLabel(key: string): string {
  return DEPENDENCY_LABELS[key] ?? key;
}

export function Dependencies({ counts }: { counts: Record<string, number> }) {
  const blocking = Object.entries(counts).filter(([, count]) => count > 0);
  if (!blocking.length) return null;
  return (
    <ul className="admin-customer-details__dependencies">
      {blocking.map(([key, count]) => (
        <li key={key}>
          {dependencyLabel(key)}: {count.toLocaleString("en-US")}
        </li>
      ))}
    </ul>
  );
}

function heldMeta(summary: CustomerSummaryView): string {
  const { balance } = summary;
  if (!balance.billed) return "Billing off";
  if (!balance.heldCents) return `Nothing held · ${formatRate(balance.rateCentsPerThousand)}`;
  const batches = balance.heldFor.map((number) => `#${number}`).join(", ");
  return `${formatUsdCents(balance.heldCents)} held for ${batches}`;
}

export function CustomerNumbers({ summary }: { summary: CustomerSummaryView }) {
  const now = new Date();
  const first = summary.openBatches.first;
  const need = summary.needs[0];
  return (
    <KpiRow label="Customer numbers" columns={6}>
      <Kpi
        label="Balance"
        value={summary.balance.billed ? formatUsdCents(summary.balance.availableCents) : "Not billed"}
        meta={heldMeta(summary)}
      />
      <Kpi
        label="Available now"
        value={summary.available ? formatCount(summary.available.count) : "Not counted"}
        meta={
          summary.available?.asOf
            ? `${formatDatabase(summary.database)} · as of ${formatAsOf(summary.available.asOf, now)}`
            : `${formatDatabase(summary.database)} · refresh on Summary`
        }
      />
      <Kpi
        label="Open batches"
        value={formatCount(summary.openBatches.count)}
        meta={first ? `#${first.number} ${stageLabel(first.stage).toLowerCase()}` : "None open"}
      />
      <Kpi
        label={`Delivered · ${summary.delivered.windowDays} days`}
        value={formatCount(summary.delivered.leads)}
        meta={plural(summary.delivered.batches, "batch", "batches")}
      />
      <Kpi
        label="Lead reports"
        value={formatCount(summary.leadReports.open)}
        meta={
          summary.leadReports.open
            ? `${formatCount(summary.leadReports.open)} open of ${formatCount(summary.leadReports.total)}`
            : summary.leadReports.total
              ? `None open · ${formatCount(summary.leadReports.total)} in total`
              : "None open"
        }
      />
      <Kpi
        label="Needs"
        value={formatCount(summary.needs.length)}
        meta={need ? need.label : "Nothing waiting"}
        alert={summary.needs.length > 0}
      />
    </KpiRow>
  );
}

function actionWords(action: string): string {
  const spaced = action.replaceAll("_", " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : "Change";
}

/** The latest few changes, so Summary says what happened lately; the
 *  Activity tab has the whole trail. */
export function RecentActivity({ activity, customerId }: { activity: ActivityPage; customerId: number }) {
  const entries = activity.entries.slice(0, 3);
  return (
    <Panel title="Recent activity">
      {entries.length ? (
        <ul className="admin-customer-details__recent">
          {entries.map((entry) => (
            <li key={entry.id}>
              <Text size="sm">{actionWords(entry.action)}</Text>
              <Text size="xs" tone="muted">
                {entry.reason ? `${entry.reason} · ` : ""}
                {formatDateTime(entry.recordedAt)}
              </Text>
            </li>
          ))}
        </ul>
      ) : (
        <Text size="sm" tone="muted">
          No administrator has changed this customer yet.
        </Text>
      )}
      <ActionLink href={customerTabHref(customerId, "activity")}>All activity</ActionLink>
    </Panel>
  );
}

export function agencyName(customer: CustomerDetailsData["customer"]): string {
  return customer.agency_id === null || !customer.agency ? "No agency" : customer.agency;
}

export function describe(data: CustomerDetailsData): string {
  const { customer, summary } = data;
  const parts = [
    agencyName(customer),
    formatStates(customer.licensed_states),
    formatDatabase(summary?.database ?? customer.lead_database),
  ];
  if (summary?.customerSince) parts.push(`customer since ${formatDay(summary.customerSince)}`);
  if (!customer.active) parts.push("deactivated");
  return parts.join(" · ");
}

export function NeedsList({ needs }: { needs: CustomerNeed[] }) {
  if (!needs.length) {
    return (
      <Text size="sm" tone="muted">
        Nothing is waiting on this customer.
      </Text>
    );
  }
  return (
    <ul className="admin-customer-details__needs" aria-label="Needs">
      {needs.map((need) => (
        <li key={`${need.kind}-${need.href}-${need.label}`}>
          <Link to={routePath(need.href)}>{need.label}</Link>
        </li>
      ))}
    </ul>
  );
}
