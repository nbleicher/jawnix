import { useState } from "react";

import { api } from "../api";
import { Button } from "../../design-system/primitives/Button";
import type { ButtonVariant } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Stack } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import { formatDatabase, formatRate } from "../operator/format";
import { useActionDialog } from "./AdminCustomerActions";

import "./AdminCustomerDetails.css";

export interface CreditLedgerEntryView {
  id: string;
  kind: string;
  amountCents: number;
  reason: string;
  actor: string | null;
  /** Who posted it, in words. Present on the operator's read. */
  actorLabel?: string;
  batchRequestId: string | null;
  /** The Batch the entry belongs to, by number. */
  batchNumber?: number | null;
  createdAt: string;
}

export interface CreditPurchaseView {
  id: string;
  amountCents: number;
  status: string;
  createdAt: string;
  completedAt: string | null;
}

export interface CustomerBillingView {
  customerId: number;
  billingEnabled: boolean;
  leadRateCentsPerThousand: number | null;
  balanceCents: number;
  activeHoldsCents: number;
  availableBalanceCents: number;
  purchases: CreditPurchaseView[];
  ledger: CreditLedgerEntryView[];
}

export interface NichePolicyRowView {
  state: string | null;
  mode: "exclude" | "only";
  niches: string[];
}

export interface NichePolicyView {
  rows: NichePolicyRowView[];
}

export interface CooldownWindowView {
  days: number;
}

export interface CustomerExclusionListView {
  id: string;
  type: string;
  filename: string;
  status: string;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  poolImpact: number | null;
  global: boolean;
  createdAt: string;
}

/** Money always with cents (V5); a debit carries a real minus sign. */
export function formatUsdCents(cents: number): string {
  const sign = cents < 0 ? "−" : "";
  const value = (Math.abs(cents) / 100).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${sign}$${value}`;
}

/** Rates as "$5.00 per 1,000" (V5). */
export function formatLeadRate(centsPerThousand: number | null): string {
  if (centsPerThousand === null) return "Not set";
  return formatRate(centsPerThousand);
}

/** "5" or "5.00" dollars per 1,000 leads, as integer cents per 1,000. */
export function parseRatePerThousand(value: string): number | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim());
  if (!match) return null;
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0"));
  return Number.isSafeInteger(cents) ? cents : null;
}

const RATE_MIN = 100;
const RATE_MAX = 2_000;

export interface ControlProps {
  customerId: number;
  onChanged: () => Promise<void> | void;
}

// ------------------------------------------------------------ quick actions

/** Post a Credit Wallet adjustment. Shared by the Customers side panel and
 *  the Billing tab. */
export function PostAdjustmentAction({
  customerId,
  onChanged,
  variant = "secondary",
}: ControlProps & { variant?: ButtonVariant }) {
  const dialog = useActionDialog(onChanged);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const cents = Math.round(Number(amount) * 100);
  const ready =
    reason.trim().length > 0 &&
    amount.trim().length > 0 &&
    Number.isFinite(cents) &&
    cents !== 0;

  return (
    <>
      <Button
        variant={variant}
        onClick={() => {
          setAmount("");
          setReason("");
          dialog.show();
        }}
      >
        Post adjustment
      </Button>
      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Post Credit Wallet adjustment"
        description="Appends an administrator adjustment to the Credit Ledger. Card refunds happen in Stripe and reconcile here."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          <Field
            label="Amount (USD)"
            required
            description="Positive credits the wallet; negative debits it. Zero is not allowed."
          >
            <Input
              type="number"
              step="0.01"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              autoFocus
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Cluster gap={3}>
            <Button
              variant="primary"
              busy={dialog.busy}
              disabled={!ready}
              onClick={() =>
                void dialog.run(() =>
                  api(`/api/admin/customers/${customerId}/billing/adjustments`, {
                    method: "POST",
                    body: JSON.stringify({
                      amount_cents: cents,
                      reason: reason.trim(),
                    }),
                  }),
                )
              }
            >
              Post adjustment
            </Button>
            <Button onClick={dialog.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
    </>
  );
}

/** Choose which database a Customer's future allocations draw from. */
export function ChangeDatabaseAction({
  customerId,
  database,
  onChanged,
  variant = "secondary",
}: ControlProps & { database: string; variant?: ButtonVariant }) {
  const dialog = useActionDialog(onChanged);
  const current = database === "db2" ? "db2" : "db1";
  const [choice, setChoice] = useState<"db1" | "db2">(current);
  const [reason, setReason] = useState("");
  const ready = choice !== current && reason.trim().length > 0;

  return (
    <>
      <Button
        variant={variant}
        onClick={() => {
          setChoice(current === "db1" ? "db2" : "db1");
          setReason("");
          dialog.show();
        }}
      >
        Change database
      </Button>
      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Change database"
        description="Future allocations draw from this database. Batches already allocated keep the leads they hold. Billing and the rate stay as they are."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          <Field
            label="Database"
            description={`Currently ${formatDatabase(current)}. The availability count is recounted for the new database.`}
          >
            <Select
              value={choice}
              onChange={(event) => setChoice(event.target.value as "db1" | "db2")}
            >
              <option value="db1">DB1 · Operator leads</option>
              <option value="db2">DB2 · CSV uploads</option>
            </Select>
          </Field>
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
              autoFocus
            />
          </Field>
          <Cluster gap={3}>
            <Button
              variant="primary"
              busy={dialog.busy}
              disabled={!ready}
              onClick={() =>
                void dialog.run(() =>
                  api(`/api/admin/customers/${customerId}/lead-database`, {
                    method: "PUT",
                    body: JSON.stringify({
                      lead_database: choice,
                      reason: reason.trim(),
                    }),
                  }),
                )
              }
            >
              Use {formatDatabase(choice)}
            </Button>
            <Button onClick={dialog.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
    </>
  );
}

export function ConfigureBillingAction({
  billing,
  customerId,
  onChanged,
}: ControlProps & { billing: CustomerBillingView }) {
  const dialog = useActionDialog(onChanged);
  const configured = billing.billingEnabled;
  const initialRate =
    billing.leadRateCentsPerThousand === null
      ? ""
      : (billing.leadRateCentsPerThousand / 100).toFixed(2);
  const [enabled, setEnabled] = useState(configured);
  const [rate, setRate] = useState(initialRate);
  const [reason, setReason] = useState("");

  const rateCents = parseRatePerThousand(rate);
  const rateValid = rateCents !== null && rateCents >= RATE_MIN && rateCents <= RATE_MAX;
  const ready = reason.trim().length > 0 && (!enabled || rateValid);

  return (
    <>
      <Button
        variant="primary"
        onClick={() => {
          setEnabled(configured);
          setRate(initialRate);
          setReason("");
          dialog.show();
        }}
      >
        Configure billing
      </Button>
      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Configure billing"
        description="Enabling billing requires a rate. Batches already submitted keep the billed-or-free status frozen at submission."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          <label className="admin-customer-details__toggle">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            Billing enabled
          </label>
          <Field
            label="Rate per 1,000 leads (USD)"
            required={enabled}
            description={
              rateValid
                ? `${formatRate(rateCents)}. Applies to future batches.`
                : "$1.00 to $20.00 per 1,000 leads, to the cent."
            }
          >
            <Input
              type="number"
              min="1"
              max="20"
              step="0.01"
              inputMode="decimal"
              value={rate}
              onChange={(event) => setRate(event.target.value)}
              disabled={!enabled}
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
              autoFocus
            />
          </Field>
          <Cluster gap={3}>
            <Button
              variant="primary"
              busy={dialog.busy}
              disabled={!ready}
              onClick={() =>
                void dialog.run(() =>
                  api(`/api/admin/customers/${customerId}/billing`, {
                    method: "PUT",
                    body: JSON.stringify({
                      billing_enabled: enabled,
                      lead_rate_cents_per_thousand: enabled ? rateCents : null,
                      reason: reason.trim(),
                    }),
                  }),
                )
              }
            >
              Save billing
            </Button>
            <Button onClick={dialog.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
    </>
  );
}


// The Billing and Rules tabs live in their own modules; these keep the
// imports that already name this module working.
export { CustomerBillingSection } from "./AdminCustomerBilling";
export {
  CustomerCooldownSection,
  CustomerDatabaseSection,
  CustomerExclusionListsSection,
  CustomerNichePolicySection,
} from "./AdminCustomerRules";
