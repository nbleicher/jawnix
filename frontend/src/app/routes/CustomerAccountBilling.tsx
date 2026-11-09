import { useEffect, useState } from "react";

import { ApplicationLink } from "../../design-system/primitives/ApplicationLink";
import { Button } from "../../design-system/primitives/Button";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Section } from "../../design-system/primitives/layout";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { useBilledWallet } from "../billing/CreditWalletContext";
import { PurchaseDialog } from "../billing/CreditWalletWidget";
import { formatCents } from "../billing/money";
import { batchLabel } from "../customer/batchLabel";
import { loadCustomerBilling } from "../customer/billing";
import type {
  CustomerBilling,
  CustomerBillingBatch,
  CustomerBillingEntry,
} from "../customer/billing";
import { formatClock, formatDay, plural } from "../customer/format";

/** "Nov 9 09:41", local time, with the year only when it differs (V5). */
function formatWhen(value: string, now: Date): string {
  return `${formatDay(value, now)} ${formatClock(value)}`;
}

/** "+$500.00" or "−$125.00": the sign carries the direction. */
function formatSigned(cents: number): string {
  return `${cents >= 0 ? "+" : "−"}${formatCents(Math.abs(cents))}`;
}

function BatchLink({ batch }: { batch: CustomerBillingBatch }) {
  return (
    <ApplicationLink href={batch.href}>batch {batchLabel(batch)}</ApplicationLink>
  );
}

/** What a history row was, in the Customer's words. */
function What({ entry }: { entry: CustomerBillingEntry }) {
  switch (entry.kind) {
    case "purchase":
      return <>Credits bought</>;
    case "hold":
      return entry.batch ? <>Held for <BatchLink batch={entry.batch} /></> : <>Held for a batch</>;
    case "charge":
      return entry.batch ? <>Charged for <BatchLink batch={entry.batch} /></> : <>Charged for a batch</>;
    default:
      return <>{entry.note ? `Adjustment · ${entry.note}` : "Adjustment"}</>;
  }
}

/** "#1042", "#1042 and #1043", "#1042, #1043, and #1044". */
function heldForLine(held: CustomerBillingBatch[]): string {
  if (!held.length) return "Nothing held right now";
  const labels = held.map(batchLabel);
  const list =
    labels.length <= 2
      ? labels.join(" and ")
      : `${labels.slice(0, -1).join(", ")}, and ${labels.at(-1) ?? ""}`;
  return `For ${list} until ${held.length === 1 ? "it is" : "they are"} delivered`;
}

function BillingNumbers({ billing }: { billing: CustomerBilling }) {
  const rate = billing.lead_rate_cents_per_thousand;
  return (
    <KpiRow label="Billing" columns={4}>
      <Kpi
        label="Balance"
        value={formatCents(billing.available_balance_cents)}
        meta="Available for new requests"
      />
      <Kpi
        label="Held"
        value={formatCents(billing.held_cents)}
        meta={heldForLine(billing.held_for)}
      />
      <Kpi
        label="Rate"
        value={rate === null ? "—" : formatCents(rate)}
        meta="Per 1,000 leads"
      />
      <Kpi
        label="Spent · 30 days"
        value={formatCents(billing.spent_cents)}
        meta={plural(billing.spent_batches, "batch", "batches")}
      />
    </KpiRow>
  );
}

function History({ billing, now }: { billing: CustomerBilling; now: Date }) {
  const columns: DataColumn<CustomerBillingEntry>[] = [
    {
      key: "date",
      header: "Date",
      cell: (entry) => formatWhen(entry.occurred_at, now),
    },
    { key: "what", header: "What", cell: (entry) => <What entry={entry} /> },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (entry) => formatSigned(entry.amount_cents),
    },
    {
      key: "balance",
      header: "Balance",
      numeric: true,
      cell: (entry) => formatCents(entry.balance_after_cents),
    },
  ];
  return (
    <Section title="History">
      <DataTable
        label="Billing history"
        className="billing-history"
        columns={columns}
        rows={billing.history}
        rowKey={(entry) => entry.id}
        empty="Nothing yet. Credits you buy and batches you request appear here."
      />
      {billing.history_complete ? null : (
        <Text size="sm" tone="muted">
          {`Showing the latest ${billing.history.length.toLocaleString("en-US")} entries.`}
        </Text>
      )}
    </Section>
  );
}

/**
 * Account's Billing tab: balance, held, rate, and spending, Buy credits, and
 * the history linked to batches. Unbilled Customers have no Billing tab; a
 * direct visit says so instead of showing zeros.
 */
export function CustomerAccountBilling({ now = new Date() }: { now?: Date }) {
  const wallet = useBilledWallet();
  const [billing, setBilling] = useState<CustomerBilling | null | undefined>(undefined);
  const [failure, setFailure] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [buying, setBuying] = useState(false);

  // The header wallet polls while a purchase is processing; when its figures
  // move, the tab reads again so its numbers and history agree with it.
  const walletVersion = wallet
    ? `${wallet.balanceCents}:${wallet.activeHoldsCents}:${wallet.ledger.length}`
    : "";

  useEffect(() => {
    let cancelled = false;
    loadCustomerBilling()
      .then((loaded) => {
        if (cancelled) return;
        setBilling(loaded);
        setFailure("");
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setFailure(
          caught instanceof Error ? caught.message : "Billing could not be loaded.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [walletVersion, attempt]);

  if (failure && billing === undefined) {
    return (
      <div className="account-tab__message" role="alert">
        <span>{failure}</span>
        <Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>
          Try again
        </Button>
      </div>
    );
  }
  if (billing === undefined) {
    return (
      <Text size="sm" tone="muted" role="status">
        Loading billing…
      </Text>
    );
  }
  if (billing === null) {
    return (
      <Text>
        This account is not billed per lead, so there is no balance to manage.
        Email noah@jawnix.com with any billing question.
      </Text>
    );
  }

  return (
    <>
      <BillingNumbers billing={billing} />
      {wallet ? (
        <div>
          <Button variant="primary" onClick={() => setBuying(true)}>
            Buy credits
          </Button>
        </div>
      ) : null}
      <History billing={billing} now={now} />
      {wallet ? <PurchaseDialog open={buying} onClose={() => setBuying(false)} /> : null}
    </>
  );
}
