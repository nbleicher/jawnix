import { useId, useState } from "react";

import { api } from "../auth/adminMFA";
import { Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input, Select } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Text } from "../../design-system/primitives/typography";
import { errorMessage, formatAdminDate } from "./AdminCustomers";
import { Fact, useActionDialog } from "./AdminCustomerActions";

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
  batchRequestId: string | null;
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

export function formatUsdCents(cents: number): string {
  const sign = cents < 0 ? "−" : "";
  return `${sign}$${(Math.abs(cents) / 100).toFixed(2)}`;
}

export function formatLeadRate(centsPerThousand: number | null): string {
  if (centsPerThousand === null) return "Not set";
  const dollarsPerLead = centsPerThousand / 100_000;
  return `${centsPerThousand}¢ per 1,000 leads ($${dollarsPerLead.toFixed(3)}/lead)`;
}

function ledgerKindLabel(kind: string): string {
  switch (kind) {
    case "purchase":
      return "Credit Purchase";
    case "batch_charge":
      return "Batch Charge";
    case "admin_adjustment":
      return "Administrator adjustment";
    default:
      return kind;
  }
}

interface ControlProps {
  customerId: number;
  onChanged: () => Promise<void> | void;
}

export function CustomerBillingSection({
  billing,
  customerId,
  onChanged,
}: ControlProps & { billing: CustomerBillingView }) {
  const configure = useActionDialog(onChanged);
  const adjust = useActionDialog(onChanged);
  const [enabled, setEnabled] = useState(billing.billingEnabled);
  const [leadRate, setLeadRate] = useState(
    billing.leadRateCentsPerThousand === null
      ? ""
      : String(billing.leadRateCentsPerThousand),
  );
  const [configureReason, setConfigureReason] = useState("");
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustReason, setAdjustReason] = useState("");

  function openConfigure() {
    setEnabled(billing.billingEnabled);
    setLeadRate(
      billing.leadRateCentsPerThousand === null
        ? ""
        : String(billing.leadRateCentsPerThousand),
    );
    setConfigureReason("");
    configure.show();
  }

  function openAdjust() {
    setAdjustAmount("");
    setAdjustReason("");
    adjust.show();
  }

  const rateValue = Number(leadRate);
  const rateDescription =
    Number.isInteger(rateValue) && rateValue > 0
      ? `Integer from 100 to 2000. ${rateValue} = $${(rateValue / 100_000).toFixed(3)} per lead.`
      : "Integer from 100 to 2000. Example: 1000 = $0.010 per lead.";
  const configureReady =
    configureReason.trim().length > 0 &&
    (!enabled ||
      (Number.isInteger(rateValue) && rateValue >= 100 && rateValue <= 2_000));

  const adjustCents = Math.round(Number(adjustAmount) * 100);
  const adjustReady =
    adjustReason.trim().length > 0 &&
    Number.isFinite(adjustCents) &&
    adjustCents !== 0 &&
    Number(adjustAmount) !== 0;

  return (
    <Section
      title="Billing"
      description="Prepaid Credit Wallet for this Customer. Switching billing on requires a Lead Rate; the wallet balance remains while billing is off."
    >
      <Card>
        <Stack gap={4}>
          <Cluster justify="space-between" align="flex-start">
            <Heading level={3} size="sm">
              Credit Wallet
            </Heading>
            <StatusBadge tone={billing.billingEnabled ? "success" : "neutral"}>
              {billing.billingEnabled ? "Billing on" : "Billing off"}
            </StatusBadge>
          </Cluster>
          <dl className="admin-customer-details__facts">
            <Fact label="Lead Rate">
              {formatLeadRate(billing.leadRateCentsPerThousand)}
            </Fact>
            <Fact label="Balance">{formatUsdCents(billing.balanceCents)}</Fact>
            <Fact label="Active Batch Holds">
              {formatUsdCents(billing.activeHoldsCents)}
            </Fact>
            <Fact label="Available">
              {formatUsdCents(billing.availableBalanceCents)}
            </Fact>
          </dl>
          <Cluster gap={3}>
            <Button variant="primary" onClick={openConfigure}>
              Configure billing
            </Button>
            <Button onClick={openAdjust}>Post adjustment</Button>
          </Cluster>

          <Stack gap={2}>
            <Heading level={3} size="sm">
              Credit Ledger
            </Heading>
            {billing.ledger.length ? (
              <div
                className="admin-customer-details__ledger"
                role="table"
                aria-label="Credit Ledger"
                tabIndex={0}
              >
                <div className="admin-customer-details__ledger-row admin-customer-details__ledger-row--head" role="row">
                  <span role="columnheader">When</span>
                  <span role="columnheader">Kind</span>
                  <span role="columnheader">Amount</span>
                  <span role="columnheader">Reason</span>
                </div>
                {billing.ledger.map((entry) => (
                  <div
                    key={entry.id}
                    className="admin-customer-details__ledger-row"
                    role="row"
                  >
                    <span role="cell">{formatAdminDate(entry.createdAt)}</span>
                    <span role="cell">{ledgerKindLabel(entry.kind)}</span>
                    <span role="cell">{formatUsdCents(entry.amountCents)}</span>
                    <span role="cell">{entry.reason}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                title="No Credit Ledger entries"
                description="Purchases, Batch Charges, and administrator adjustments will appear here."
              />
            )}
          </Stack>
        </Stack>
      </Card>

      <Dialog
        open={configure.open}
        onClose={configure.close}
        title="Configure billing"
        description="Enabling billing requires a Lead Rate. Existing Batch Requests keep the billed-or-free status frozen at submission."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {configure.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {configure.failure}
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
            label="Lead Rate (¢ per 1,000 leads)"
            required={enabled}
            description={rateDescription}
          >
            <Input
              type="number"
              min={100}
              max={2000}
              step={1}
              value={leadRate}
              onChange={(event) => setLeadRate(event.target.value)}
              disabled={!enabled}
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={configureReason}
              onChange={(event) => setConfigureReason(event.target.value)}
              autoComplete="off"
              autoFocus
            />
          </Field>
          <Cluster gap={3}>
            <Button
              variant="primary"
              busy={configure.busy}
              disabled={!configureReady}
              onClick={() =>
                void configure.run(() =>
                  api(`/api/admin/customers/${customerId}/billing`, {
                    method: "PUT",
                    body: JSON.stringify({
                      billing_enabled: enabled,
                      lead_rate_cents_per_thousand: enabled
                        ? rateValue
                        : null,
                      reason: configureReason.trim(),
                    }),
                  }),
                )
              }
            >
              Save billing
            </Button>
            <Button onClick={configure.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>

      <Dialog
        open={adjust.open}
        onClose={adjust.close}
        title="Post Credit Wallet adjustment"
        description="Appends an administrator adjustment to the Credit Ledger. Card refunds happen in Stripe and reconcile here."
        dismissOnBackdrop={false}
      >
        <Stack gap={4}>
          {adjust.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {adjust.failure}
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
              value={adjustAmount}
              onChange={(event) => setAdjustAmount(event.target.value)}
              autoFocus
            />
          </Field>
          <Field label="Reason" required>
            <Input
              value={adjustReason}
              onChange={(event) => setAdjustReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Cluster gap={3}>
            <Button
              variant="primary"
              busy={adjust.busy}
              disabled={!adjustReady}
              onClick={() =>
                void adjust.run(() =>
                  api(
                    `/api/admin/customers/${customerId}/billing/adjustments`,
                    {
                      method: "POST",
                      body: JSON.stringify({
                        amount_cents: adjustCents,
                        reason: adjustReason.trim(),
                      }),
                    },
                  ),
                )
              }
            >
              Post adjustment
            </Button>
            <Button onClick={adjust.close}>Cancel</Button>
          </Cluster>
        </Stack>
      </Dialog>
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
      title="Cooldown Window"
      description="Minimum age of another Customer's Distribution Event before this Customer may draw that Lead. Default is seven days; minimum is one."
    >
      <Card>
        <Stack gap={4} align="flex-start">
          <dl className="admin-customer-details__facts">
            <Fact label="Current window">{cooldown.days} days</Fact>
          </dl>
          <Button
            variant="primary"
            onClick={() => {
              setDays(String(cooldown.days));
              setReason("");
              dialog.show();
            }}
          >
            Change Cooldown Window
          </Button>
        </Stack>
      </Card>

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

function parseDraftRows(rows: DraftPolicyRow[]): NichePolicyRowView[] {
  return rows
    .map((row) => ({
      state: row.state.trim() ? row.state.trim().toUpperCase() : null,
      mode: row.mode,
      niches: row.niches
        .split(",")
        .map((niche) => niche.trim())
        .filter(Boolean),
    }))
    .filter((row) => row.niches.length > 0);
}

function describeRow(row: NichePolicyRowView): string {
  const scope = row.state ?? "All states";
  const niches = row.niches.join(", ");
  return row.mode === "exclude"
    ? `${scope}: exclude ${niches}`
    : `${scope}: only ${niches}`;
}

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

  return (
    <Section
      title="Niche Policy"
      description="Per-state rows that either exclude listed Niches or restrict allocation to only them. A state's row fully overrides the all-states default."
    >
      <Card>
        <Stack gap={4} align="flex-start">
          {policy.rows.length ? (
            <ul className="admin-customer-details__list" aria-label="Current Niche Policy">
              {policy.rows.map((row) => (
                <li key={`${row.state ?? "all"}-${row.mode}-${row.niches.join(",")}`}>
                  <Text>{describeRow(row)}</Text>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No Niche Policy rows"
              description="Without rows, Niche does not restrict this Customer's eligibility beyond Licensed States and history."
            />
          )}
          <Button variant="primary" onClick={openEditor}>
            Edit Niche Policy
          </Button>
        </Stack>
      </Card>

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
                    <Field label="Niches" required>
                      <Input
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
            <Button onClick={() => void previewAvailability()} busy={previewBusy}>
              Preview projected availability
            </Button>
            {projected !== null ? (
              <Text weight="semibold" role="status">
                Projected available Leads: {projected.toLocaleString()}
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
              disabled={!ready}
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
