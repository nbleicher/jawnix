import { Link } from "react-router";

import { Cluster, Section } from "../../design-system/primitives/layout";
import { FactList } from "../../design-system/primitives/facts";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { routePath } from "../routePath";
import { formatDateTime } from "../operator/format";
import {
  ConfigureBillingAction,
  PostAdjustmentAction,
  formatLeadRate,
  formatUsdCents,
} from "./AdminCustomerControls";
import type { ControlProps, CreditLedgerEntryView, CreditPurchaseView, CustomerBillingView } from "./AdminCustomerControls";

import "./AdminCustomerDetails.css";

// -------------------------------------------------------------- Billing tab

function ledgerKindLabel(kind: string): string {
  switch (kind) {
    case "purchase":
      return "Credit purchase";
    case "batch_charge":
      return "Batch charge";
    case "admin_adjustment":
      return "Adjustment";
    default:
      return kind.replaceAll("_", " ");
  }
}

function purchaseTone(status: string) {
  if (status === "failed") return "danger" as const;
  if (status === "completed" || status === "succeeded") return "success" as const;
  if (status === "processing" || status === "pending") return "info" as const;
  return "neutral" as const;
}

const LEDGER_COLUMNS: DataColumn<CreditLedgerEntryView>[] = [
  { key: "when", header: "When", cell: (entry) => formatDateTime(entry.createdAt) },
  { key: "kind", header: "Kind", cell: (entry) => ledgerKindLabel(entry.kind) },
  {
    key: "amount",
    header: "Amount",
    numeric: true,
    cell: (entry) => formatUsdCents(entry.amountCents),
  },
  { key: "who", header: "Who", cell: (entry) => entry.actorLabel ?? "—" },
  {
    key: "batch",
    header: "Batch",
    cell: (entry) =>
      entry.batchNumber ? (
        <Link to={routePath(`/app/admin/batches/${entry.batchNumber}`)}>
          #{entry.batchNumber}
        </Link>
      ) : (
        "—"
      ),
  },
  { key: "reason", header: "Reason", cell: (entry) => entry.reason || "—" },
];

const PURCHASE_COLUMNS: DataColumn<CreditPurchaseView>[] = [
  { key: "when", header: "Started", cell: (item) => formatDateTime(item.createdAt) },
  {
    key: "amount",
    header: "Amount",
    numeric: true,
    cell: (item) => formatUsdCents(item.amountCents),
  },
  {
    key: "status",
    header: "Status",
    cell: (item) => (
      <StatusBadge tone={purchaseTone(item.status)}>
        {item.status.replaceAll("_", " ")}
      </StatusBadge>
    ),
  },
  {
    key: "completed",
    header: "Completed",
    cell: (item) => (item.completedAt ? formatDateTime(item.completedAt) : "—"),
  },
];

/**
 * The Billing tab: the wallet and its actions in the right column, the
 * purchases and the Credit Ledger (with who posted each entry and the batch
 * it belongs to) in the main column.
 */
export function CustomerBillingSection({
  billing,
  customerId,
  onChanged,
}: ControlProps & { billing: CustomerBillingView }) {
  return (
    <RecordColumns
      main={
        <>
          <Section title="Credit ledger" description="Every credit and debit, newest first.">
            <DataTable
              label="Credit ledger"
              columns={LEDGER_COLUMNS}
              rows={billing.ledger}
              rowKey={(entry) => entry.id}
              empty="No ledger entries yet. Purchases, batch charges, and adjustments appear here."
            />
          </Section>
          <Section title="Purchases" description="Card purchases through Stripe.">
            <DataTable
              label="Purchases"
              columns={PURCHASE_COLUMNS}
              rows={billing.purchases}
              rowKey={(item) => item.id}
              empty="No card purchases yet."
            />
          </Section>
        </>
      }
      aside={
        <Panel
          title="Credit wallet"
          action={
            <StatusBadge tone={billing.billingEnabled ? "success" : "neutral"}>
              {billing.billingEnabled ? "Billing on" : "Billing off"}
            </StatusBadge>
          }
        >
          <FactList
            label="Credit wallet"
            items={[
              { term: "Rate", description: formatLeadRate(billing.leadRateCentsPerThousand) },
              { term: "Balance", description: formatUsdCents(billing.balanceCents) },
              { term: "Held for open batches", description: formatUsdCents(billing.activeHoldsCents) },
              { term: "Available", description: formatUsdCents(billing.availableBalanceCents) },
            ]}
          />
          <Cluster gap={3}>
            <ConfigureBillingAction billing={billing} customerId={customerId} onChanged={onChanged} />
            <PostAdjustmentAction customerId={customerId} onChanged={onChanged} />
          </Cluster>
        </Panel>
      }
    />
  );
}
