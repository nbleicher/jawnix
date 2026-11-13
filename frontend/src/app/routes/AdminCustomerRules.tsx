import { useId, useState } from "react";

import { api } from "../api";
import { Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Fieldset, Input, Select } from "../../design-system/primitives/form";
import { Card, Cluster, Grid, Section, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { formatCount, formatDay } from "../operator/format";
import { errorMessage } from "./AdminCustomers";
import { useActionDialog } from "./AdminCustomerActions";
import { ChangeDatabaseAction } from "./AdminCustomerControls";
import type {
  ControlProps,
  CooldownWindowView,
  CustomerExclusionListView,
  NichePolicyRowView,
  NichePolicyView,
} from "./AdminCustomerControls";

import "./AdminCustomerDetails.css";

const US_STATE_CODES = [
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
  "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
  "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
  "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
] as const;

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
