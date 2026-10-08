import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import {
  Card,
  Cluster,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Mono,
  Text,
} from "../../design-system/primitives/typography";
import {
  useBilledWallet,
  useCreditWallet,
} from "./CreditWalletContext";
import { formatCents } from "./money";
import {
  LEDGER_KIND_LABEL,
  hasProcessingPurchase,
  type CreditLedgerEntry,
  type CreditPurchase,
  type CreditWallet,
} from "./wallet";
import { formatMilestoneTime } from "../routes/MilestoneGraph";

import "./CreditLedgerSection.css";

function LedgerDescription({ entry }: { entry: CreditLedgerEntry }) {
  if (entry.kind === "admin_adjustment" && entry.reason) {
    return <>{entry.reason}</>;
  }
  if (entry.kind === "batch_charge" && entry.batchRequestId) {
    return (
      <>
        Batch Request <Mono>{entry.batchRequestId.slice(0, 8)}</Mono>
      </>
    );
  }
  return <>{LEDGER_KIND_LABEL[entry.kind]}</>;
}

function PurchaseRow({ purchase }: { purchase: CreditPurchase }) {
  const presentation = {
    processing: { label: "Processing", tone: "info" as const },
    completed: { label: "Completed", tone: "success" as const },
    failed: { label: "Failed", tone: "danger" as const },
    expired: { label: "Expired", tone: "neutral" as const },
  }[purchase.status];
  return (
    <Card as="li" padding={4}>
      <Cluster justify="space-between" align="start">
        <Stack gap={1}>
          <Heading level={3}>
            <Mono>{formatCents(purchase.amountCents)}</Mono>
          </Heading>
          <Text size="sm" tone="muted">
            Started <Mono>{formatMilestoneTime(purchase.createdAt)}</Mono>
          </Text>
        </Stack>
        <StatusBadge tone={presentation.tone}>
          {presentation.label}
        </StatusBadge>
      </Cluster>
    </Card>
  );
}

interface LedgerRow {
  entry: CreditLedgerEntry;
  /** Wallet balance immediately after this entry — derivable because the
   *  ledger is append-only, newest-first, and sums to the wallet balance. */
  balanceAfterCents: number;
}

/** Running balance per row: start at the wallet balance and walk the
 *  append-only, newest-first ledger backwards. Reconciles by construction. */
export function ledgerRowsWithBalance(wallet: CreditWallet): LedgerRow[] {
  let balance = wallet.balanceCents;
  return wallet.ledger.map((entry) => {
    const row = { entry, balanceAfterCents: balance };
    balance -= entry.amountCents;
    return row;
  });
}

/**
 * The ledger is the hero: an append-only table of record where every row is
 * checkable — signed amounts, mono references, and a running balance that
 * reconciles to the wallet totals above it.
 */
function LedgerTable({ wallet }: { wallet: CreditWallet }) {
  const rows = useMemo<LedgerRow[]>(
    () => ledgerRowsWithBalance(wallet),
    [wallet],
  );

  return (
    <div className="credit-ledger-tablewrap">
      <table className="credit-ledger">
        <thead>
          <tr>
            <th scope="col">Date</th>
            <th scope="col">Entry</th>
            <th scope="col">Reference</th>
            <th scope="col" className="credit-ledger__num">Amount</th>
            <th scope="col" className="credit-ledger__num">Balance</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ entry, balanceAfterCents }) => {
            const credit = entry.amountCents >= 0;
            return (
              <tr key={entry.id}>
                <td className="credit-ledger__data">
                  {formatMilestoneTime(entry.createdAt)}
                </td>
                <td>
                  <span className="credit-ledger__kind">
                    {LEDGER_KIND_LABEL[entry.kind]}
                  </span>
                  <span className="credit-ledger__detail">
                    <LedgerDescription entry={entry} />
                  </span>
                </td>
                <td className="credit-ledger__data">
                  <Mono>{entry.id}</Mono>
                </td>
                <td className="credit-ledger__num">
                  <span
                    className={
                      credit
                        ? "credit-ledger__amount credit-ledger__amount--credit"
                        : "credit-ledger__amount credit-ledger__amount--debit"
                    }
                  >
                    {`${credit ? "+" : "−"}${formatCents(Math.abs(entry.amountCents))}`}
                  </span>
                </td>
                <td className="credit-ledger__num">
                  {formatCents(balanceAfterCents)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PurchaseReturnNotice({
  outcome,
  processing,
  failed,
  onClear,
}: {
  outcome: "success" | "cancelled" | null;
  processing: boolean;
  failed: boolean;
  onClear: () => void;
}) {
  const { refresh } = useCreditWallet();
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    const purchase = params.get("purchase");
    if (purchase !== "success" && purchase !== "cancelled") return;
    const next = new URLSearchParams(params);
    next.delete("purchase");
    setParams(next, { replace: true });
  }, [params, setParams]);

  useEffect(() => {
    if (outcome === "success") void refresh();
  }, [outcome, refresh]);

  useEffect(() => {
    if (outcome === "cancelled") {
      const timer = window.setTimeout(onClear, 8_000);
      return () => window.clearTimeout(timer);
    }
    if (outcome === "success" && !processing) {
      const timer = window.setTimeout(onClear, 8_000);
      return () => window.clearTimeout(timer);
    }
    return undefined;
  }, [outcome, processing, onClear]);

  if (outcome === "cancelled") {
    return (
      <div
        className="credit-ledger-notice credit-ledger-notice--neutral"
        role="status"
      >
        Credit Purchase cancelled. Your Credit Wallet was not charged.
      </div>
    );
  }

  if (outcome === "success") {
    const tone = processing ? "info" : failed ? "danger" : "success";
    return (
      <div
        className={`credit-ledger-notice credit-ledger-notice--${tone}`}
        role="status"
      >
        {processing
          ? "Your Credit Purchase is processing. The Credit Wallet updates when Stripe confirms payment."
          : failed
            ? "Stripe did not complete this Credit Purchase. The Credit Wallet was not charged."
            : "Your Credit Purchase is complete. The Credit Wallet has been updated."}
      </div>
    );
  }

  return null;
}

/**
 * Credit Wallet facts and the full Credit Ledger for a Billed Customer.
 * Free Customers never render this section.
 */
export function CreditLedgerSection() {
  const wallet = useBilledWallet();
  const [params] = useSearchParams();
  const [outcome, setOutcome] = useState<"success" | "cancelled" | null>(null);

  useEffect(() => {
    const purchase = params.get("purchase");
    if (purchase === "success" || purchase === "cancelled") {
      setOutcome(purchase);
    }
  }, [params]);

  if (!wallet) return null;

  const processing = hasProcessingPurchase(wallet);
  const pendingOrFailedPurchases = wallet.purchases.filter(
    (purchase) => purchase.status !== "completed",
  );
  const latestPurchaseFailed = ["failed", "expired"].includes(
    wallet.purchases[0]?.status ?? "",
  );

  return (
    <>
      <PurchaseReturnNotice
        outcome={outcome}
        processing={processing}
        failed={latestPurchaseFailed}
        onClear={() => setOutcome(null)}
      />

      <Section
        title="Credit Wallet"
        description="Prepaid balance for Batch Requests. Purchases credit the wallet after Stripe confirms payment."
      >
        <Card>
          <dl className="credit-wallet-facts">
            <div>
              <dt>
                <LabelText>Available balance</LabelText>
              </dt>
              <dd>{formatCents(wallet.availableBalanceCents)}</dd>
            </div>
            <div>
              <dt>
                <LabelText>Wallet balance</LabelText>
              </dt>
              <dd>{formatCents(wallet.balanceCents)}</dd>
            </div>
            <div>
              <dt>
                <LabelText>Active Batch Holds</LabelText>
              </dt>
              <dd>{formatCents(wallet.activeHoldsCents)}</dd>
            </div>
            {wallet.leadRateCentsPerThousand != null ? (
              <div>
                <dt>
                  <LabelText>Lead Rate</LabelText>
                </dt>
                <dd>
                  {`${formatCents(wallet.leadRateCentsPerThousand)} per 1,000 leads`}
                </dd>
              </div>
            ) : null}
          </dl>
        </Card>
      </Section>

      {pendingOrFailedPurchases.length ? (
        <Section
          title="Recent purchase attempts"
          description="Processing purchases await Stripe confirmation; failed or expired attempts never credit the wallet."
        >
          <Stack as="ul" gap={3}>
            {pendingOrFailedPurchases.map((purchase) => (
              <PurchaseRow key={purchase.id} purchase={purchase} />
            ))}
          </Stack>
        </Section>
      ) : null}

      <Section
        title="Credit Ledger"
        description="Every Credit Purchase, Batch Charge, and adjustment that makes up the Credit Wallet."
      >
        {wallet.ledger.length ? (
          <LedgerTable wallet={wallet} />
        ) : (
          <Card padding={4}>
            <Text tone="muted">
              No Credit Ledger entries yet. Buy credits from the Credit Wallet
              in the header to top up.
            </Text>
          </Card>
        )}
      </Section>
    </>
  );
}
