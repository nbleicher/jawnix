import { useId, useState } from "react";
import { Link } from "react-router";

import { api } from "../auth/adminMFA";
import { Button } from "../../design-system/primitives/Button";
import type { ButtonVariant } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Fieldset, Input, Select } from "../../design-system/primitives/form";
import { Card, Cluster, Grid, Section, Stack } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { formatCount, formatDatabase, formatDateTime, formatDay, formatRate } from "../operator/format";
import { errorMessage } from "./AdminCustomers";
import { useActionDialog } from "./AdminCustomerActions";

import "./AdminCustomerDetails.css";

const US_STATE_CODES = [
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
  "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
  "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
  "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
] as const;

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

interface ControlProps {
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

function ConfigureBillingAction({
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

// -------------------------------------------------------------- Billing tab

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
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

// ---------------------------------------------------------------- Rules tab

/** Which database future allocations draw from. */
export function CustomerDatabaseSection({
  customerId,
  database,
  onChanged,
}: ControlProps & { database: string }) {
  return (
    <Section
      title="Database"
      description="Future allocations draw from this database."
    >
      <Stack gap={3} align="flex-start">
        <FactList
          label="Database"
          className="admin-customer-details__facts-narrow"
          items={[
            {
              term: "Draws from",
              description: database === "db2" ? "DB2 · CSV uploads" : "DB1 · Operator leads",
            },
          ]}
        />
        <ChangeDatabaseAction customerId={customerId} database={database} onChanged={onChanged} />
      </Stack>
    </Section>
  );
}

export function CustomerCooldownSection({
  cooldown,
  customerId,
  onChanged,
}: ControlProps & { cooldown: CooldownWindowView }) {
  const dialog = useActionDialog(onChanged);
  const [days, setDays] = useState(String(cooldown.days));
  const [reason, setReason] = useState("");
  const daysValue = Number(days);
  const ready =
    reason.trim().length > 0 &&
    Number.isInteger(daysValue) &&
    daysValue >= 1;

  return (
    <Section
      title="Cooldown window"
      description="How old another customer's delivery of a lead must be before this customer can receive it. Default seven days; minimum one."
    >
      <Stack gap={3} align="flex-start">
        <FactList
          label="Cooldown window"
          className="admin-customer-details__facts-narrow"
          items={[
            {
              term: "Current window",
              description: `${formatCount(cooldown.days)} day${cooldown.days === 1 ? "" : "s"}`,
            },
          ]}
        />
        <Button
          onClick={() => {
            setDays(String(cooldown.days));
            setReason("");
            dialog.show();
          }}
        >
          Change Cooldown Window
        </Button>
      </Stack>

      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Change Cooldown Window"
        description="The window belongs to the drawing Customer. Permanent no-repeat per Customer and Agency is unchanged."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          <Field label="Days" required description="Integer of one or more.">
            <Input
              type="number"
              min={1}
              step={1}
              value={days}
              onChange={(event) => setDays(event.target.value)}
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
                  api(`/api/admin/customers/${customerId}/cooldown-window`, {
                    method: "PUT",
                    body: JSON.stringify({
                      days: daysValue,
                      reason: reason.trim(),
                    }),
                  }),
                )
              }
            >
              Save Cooldown Window
            </Button>
            <Button onClick={dialog.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
    </Section>
  );
}

interface DraftPolicyRow {
  id: string;
  state: string;
  mode: "exclude" | "only";
  niches: string;
}

function toDraftRows(rows: NichePolicyRowView[]): DraftPolicyRow[] {
  if (!rows.length) {
    return [
      {
        id: crypto.randomUUID(),
        state: "",
        mode: "exclude",
        niches: "",
      },
    ];
  }
  return rows.map((row) => ({
    id: crypto.randomUUID(),
    state: row.state ?? "",
    mode: row.mode,
    niches: row.niches.join(", "),
  }));
}

function hasNiches(row: DraftPolicyRow): boolean {
  return row.niches.split(",").some((niche) => niche.trim().length > 0);
}

function isBlankRow(row: DraftPolicyRow): boolean {
  // A chosen State with no Niches is a half-finished edit, not an empty row;
  // treating it as blank would let a Save silently clear the policy while a
  // State sits visibly selected.
  return !row.state.trim() && !hasNiches(row);
}

function parseDraftRows(rows: DraftPolicyRow[]): NichePolicyRowView[] {
  // The editor always holds at least one row, so leaving every row's Niches
  // empty is the only way an administrator can express "no Niche Policy".
  // Rows are dropped only when *all* of them are blank; a single blank row
  // beside a filled one is a mistake the editor refuses rather than discards.
  if (rows.every(isBlankRow)) return [];
  return rows
    .map((row) => ({
      state: row.state.trim() ? row.state.trim().toUpperCase() : null,
      mode: row.mode,
      niches: row.niches
        .split(",")
        .map((niche) => niche.trim())
        .filter(Boolean),
    }));
}

const POLICY_COLUMNS: DataColumn<NichePolicyRowView>[] = [
  { key: "state", header: "States", cell: (row) => row.state ?? "All states" },
  { key: "mode", header: "Mode", cell: (row) => (row.mode === "exclude" ? "Exclude" : "Only") },
  { key: "niches", header: "Niches", cell: (row) => row.niches.join(", ") },
];

export function CustomerNichePolicySection({
  policy,
  customerId,
  onChanged,
}: ControlProps & {
  policy: NichePolicyView;
}) {
  const dialog = useActionDialog(onChanged);
  const [draft, setDraft] = useState<DraftPolicyRow[]>(() =>
    toDraftRows(policy.rows),
  );
  const [reason, setReason] = useState("");
  const [projected, setProjected] = useState<number | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewFailure, setPreviewFailure] = useState("");
  const listId = useId();

  function openEditor() {
    setDraft(toDraftRows(policy.rows));
    setReason("");
    setProjected(null);
    setPreviewFailure("");
    dialog.show();
  }

  function updateRow(id: string, patch: Partial<DraftPolicyRow>) {
    setProjected(null);
    setDraft((rows) =>
      rows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
    );
  }

  async function previewAvailability() {
    setPreviewBusy(true);
    setPreviewFailure("");
    try {
      const result = await api<{ available: number }>(
        `/api/admin/customers/${customerId}/niche-policy/projected-availability`,
        {
          method: "POST",
          body: JSON.stringify({ rows: parseDraftRows(draft) }),
        },
      );
      setProjected(result.available);
    } catch (caught) {
      setProjected(null);
      setPreviewFailure(errorMessage(caught));
    } finally {
      setPreviewBusy(false);
    }
  }

  const ready = reason.trim().length > 0 && projected !== null;
  const clearing = draft.every(isBlankRow);
  const rowsValid = clearing || draft.every(hasNiches);

  return (
    <Section
      title="Niche policy"
      description="Per-state rows that exclude listed niches or allow only them. A state's row replaces the all-states default."
    >
      <Stack gap={3} align="flex-start">
        <div className="admin-customer-details__full">
          <DataTable
            label="Current Niche Policy"
            columns={POLICY_COLUMNS}
            rows={policy.rows}
            rowKey={(row) => `${row.state ?? "all"}-${row.mode}-${row.niches.join(",")}`}
            empty="No rows. Niche does not narrow what this customer can receive beyond its states and history."
          />
        </div>
        <Button onClick={openEditor}>Edit Niche Policy</Button>
      </Stack>

      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Edit Niche Policy"
        description="Preview projected availability for the draft rows, then provide an audit reason before saving."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          {previewFailure ? (
            <Text role="alert" tone="danger" size="sm">
              {previewFailure}
            </Text>
          ) : null}

          <Fieldset
            legend="Policy rows"
            description="Leave State blank for the all-states default. Niches are comma-separated."
          >
            <Stack gap={4} id={listId}>
              {draft.map((row, index) => (
                <Card key={row.id} aria-label={`Policy row ${index + 1}`}>
                  <Stack gap={3}>
                    <Grid minColumnWidth="8rem">
                      <Field label="State">
                        <Select
                          value={row.state}
                          onChange={(event) =>
                            updateRow(row.id, { state: event.target.value })
                          }
                        >
                          <option value="">All states</option>
                          {US_STATE_CODES.map((code) => (
                            <option key={code} value={code}>
                              {code}
                            </option>
                          ))}
                        </Select>
                      </Field>
                      <Field label="Mode" required>
                        <Select
                          value={row.mode}
                          onChange={(event) =>
                            updateRow(row.id, {
                              mode: event.target.value as "exclude" | "only",
                            })
                          }
                        >
                          <option value="exclude">Exclude</option>
                          <option value="only">Only</option>
                        </Select>
                      </Field>
                    </Grid>
                    <Field label="Niches" required={!clearing}>
                      <Input
                        required={!clearing}
                        value={row.niches}
                        onChange={(event) =>
                          updateRow(row.id, { niches: event.target.value })
                        }
                        placeholder="roofing, solar"
                      />
                    </Field>
                    <div>
                      <Button
                        onClick={() => {
                          setProjected(null);
                          setDraft((rows) =>
                            rows.length === 1
                              ? [
                                  {
                                    id: crypto.randomUUID(),
                                    state: "",
                                    mode: "exclude",
                                    niches: "",
                                  },
                                ]
                              : rows.filter((entry) => entry.id !== row.id),
                          );
                        }}
                      >
                        Remove row
                      </Button>
                    </div>
                  </Stack>
                </Card>
              ))}
            </Stack>
          </Fieldset>

          {clearing ? (
            <Text size="sm" tone="muted" role="status">
              Every row is empty, so saving removes this Customer's Niche
              Policy entirely.
            </Text>
          ) : !rowsValid ? (
            <Text size="sm" tone="danger" role="alert">
              Every row needs at least one Niche. Remove the empty rows, or
              empty them all to clear the policy.
            </Text>
          ) : null}

          <div>
            <Button
              onClick={() => {
                setProjected(null);
                setDraft((rows) => [
                  ...rows,
                  {
                    id: crypto.randomUUID(),
                    state: "",
                    mode: "exclude",
                    niches: "",
                  },
                ]);
              }}
            >
              Add row
            </Button>
          </div>

          <Cluster gap={3} align="center">
            <Button
              onClick={() => void previewAvailability()}
              busy={previewBusy}
              disabled={!rowsValid}
            >
              Preview projected availability
            </Button>
            {projected !== null ? (
              <Text weight="semibold" role="status">
                Projected available Leads: {projected.toLocaleString("en-US")}
              </Text>
            ) : (
              <Text size="sm" tone="muted">
                Preview availability before saving.
              </Text>
            )}
          </Cluster>

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
              disabled={!ready || !rowsValid}
              onClick={() =>
                void dialog.run(() =>
                  api(`/api/admin/customers/${customerId}/niche-policy`, {
                    method: "PUT",
                    body: JSON.stringify({
                      rows: parseDraftRows(draft),
                      reason: reason.trim(),
                    }),
                  }),
                )
              }
            >
              Save Niche Policy
            </Button>
            <Button onClick={dialog.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
    </Section>
  );
}

const EXCLUSION_TYPES: Record<string, string> = {
  landline: "Landline",
  dnc: "DNC",
  tcpa_litigator: "TCPA litigator",
};

function exclusionTone(status: string) {
  if (status === "failed") return "danger" as const;
  if (status === "pending_confirmation") return "danger" as const;
  if (status === "active" || status === "confirmed") return "success" as const;
  if (status === "queued" || status === "ingesting") return "info" as const;
  return "neutral" as const;
}

const EXCLUSION_COLUMNS: DataColumn<CustomerExclusionListView>[] = [
  { key: "file", header: "File", cell: (item) => item.filename },
  { key: "type", header: "Type", cell: (item) => EXCLUSION_TYPES[item.type] ?? item.type.replaceAll("_", " ") },
  {
    key: "status",
    header: "Status",
    cell: (item) => (
      <StatusBadge tone={exclusionTone(item.status)}>{item.status.replaceAll("_", " ")}</StatusBadge>
    ),
  },
  { key: "scope", header: "Scope", cell: (item) => (item.global ? "Global" : "Customer-scoped") },
  { key: "accepted", header: "Phones", numeric: true, cell: (item) => formatCount(item.acceptedRows) },
  {
    key: "impact",
    header: "In pool",
    numeric: true,
    cell: (item) => (item.poolImpact === null ? "—" : formatCount(item.poolImpact)),
  },
  { key: "uploaded", header: "Uploaded", cell: (item) => formatDay(item.createdAt) },
];

/** Exclusion lists this Customer uploaded. Global effect is confirmed on the
 *  operator's decisions, not here. */
export function CustomerExclusionListsSection({
  items,
}: {
  items: CustomerExclusionListView[];
}) {
  return (
    <Section
      title="Exclusion lists"
      description="Lists this customer uploaded. They apply to this customer only unless their global effect is confirmed."
    >
      <DataTable
        label="Exclusion lists"
        columns={EXCLUSION_COLUMNS}
        rows={items}
        rowKey={(item) => item.id}
        empty="This customer has not uploaded an exclusion list."
      />
    </Section>
  );
}
