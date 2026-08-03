import { useEffect, useState } from "react";
import { Form, Link, useLoaderData, useNavigate } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  DisclosureSection,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Numeral,
  Text,
} from "../../design-system/primitives/typography";

import "./AdminCustomers.css";

export type CustomerAdminTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger";

/** Backend-authored standing. The label carries the meaning; the tone only
 *  reinforces it, so no screen re-derives status from raw record state. */
export interface CustomerAdminStatus {
  label: string;
  description: string;
  tone: CustomerAdminTone;
}

export interface CustomerDirectoryAgency {
  id: number;
  name: string;
  active: boolean;
}

export interface CustomerDirectoryRow {
  id: number;
  slug: string;
  name: string;
  agency_id: number | null;
  agency: string;
  licensed_states: string[];
  /** Standing of the durable party. */
  customer_status: CustomerAdminStatus;
  /** Standing of the replaceable access. Never the same thing. */
  account_status: CustomerAdminStatus;
  account_email: string;
  last_activity_at: string | null;
  problems: string[];
  href: string;
}

export interface CustomerDirectoryData {
  filters: {
    query: string;
    status: "all" | "active" | "deactivated";
    agency_id: number | null;
    state: string;
    problems_only: boolean;
  };
  agencies: CustomerDirectoryAgency[];
  states: string[];
  total: number;
  matched: number;
  customers: CustomerDirectoryRow[];
}

interface CreatedCustomer {
  ok: boolean;
  customerId: number;
  slug: string;
  userId: string;
  mappingConfirmed: boolean;
}

interface NicheAssignmentUploadStatus {
  id: string;
  filename: string;
  status: "queued" | "ingesting" | "complete" | "failed";
  totalRows: number;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  skippedMappedRows: number;
  error: string;
}

const FILTER_KEYS = ["q", "status", "agency_id", "state", "problems_only"] as const;

export function formatAdminDate(value: string | null): string {
  if (!value) return "Never";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(value),
  );
}

/** The directory's href is absolute so it is usable anywhere. Inside the shell
 *  we navigate through the router instead, so the loader, the pending
 *  indicator, and the route announcer all still run. */
function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

/** Filters live in the URL so a directory view is shareable and the browser's
 *  back button restores the exact result set the operator was looking at. */
export async function adminCustomerDirectoryLoader({
  request,
}: LoaderFunctionArgs): Promise<CustomerDirectoryData> {
  const requested = new URL(request.url).searchParams;
  const query = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = requested.get(key);
    if (value) query.set(key, value);
  }
  const search = query.toString();
  return api<CustomerDirectoryData>(
    `/api/admin/customers/directory${search ? `?${search}` : ""}`,
  );
}

function Standing({
  label,
  status,
  detail,
}: {
  label: string;
  status: CustomerAdminStatus;
  detail: string;
}) {
  return (
    <Stack gap={2} align="flex-start">
      <LabelText>{label}</LabelText>
      <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
      <Text size="xs" tone="muted">
        {detail}
      </Text>
    </Stack>
  );
}

function CustomerCard({ row }: { row: CustomerDirectoryRow }) {
  return (
    <Card as="article" aria-label={row.name}>
      <Stack gap={4}>
        <Stack gap={1}>
          <Heading level={3}>
            <Link className="admin-customers__name" to={routePath(row.href)}>
              {row.name}
            </Link>
          </Heading>
          <Text size="sm" tone="muted">
            {row.agency_id === null ? "No Agency" : row.agency}
          </Text>
        </Stack>

        {/* The two standings sit side by side and are labelled separately:
            replacing access must never read as a change to the Customer. */}
        <Grid minColumnWidth="11rem" gap={4}>
          <Standing
            label="Customer"
            status={row.customer_status}
            detail={row.customer_status.description}
          />
          <Standing
            label="User Account"
            status={row.account_status}
            detail={row.account_email}
          />
        </Grid>

        <dl className="admin-customers__facts">
          <div>
            <dt>
              <LabelText>Licensed States</LabelText>
            </dt>
            <dd>
              {row.licensed_states.length
                ? row.licensed_states.join(", ")
                : "None yet"}
            </dd>
          </div>
          <div>
            <dt>
              <LabelText>Last activity</LabelText>
            </dt>
            <dd>{formatAdminDate(row.last_activity_at)}</dd>
          </div>
        </dl>

        {row.problems.length ? (
          <Stack gap={2} align="flex-start">
            <LabelText>Setup problems</LabelText>
            <ul
              className="admin-customers__problems"
              aria-label={`Setup problems for ${row.name}`}
            >
              {row.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </Stack>
        ) : null}
      </Stack>
    </Card>
  );
}

function CreateCustomerDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const text = (key: string) => String(fields.get(key) ?? "").trim();
    setBusy(true);
    setFailure("");
    try {
      const created = await api<CreatedCustomer>("/api/admin/customers", {
        method: "POST",
        body: JSON.stringify({
          name: text("name"),
          email: text("email"),
          first_name: text("first_name") || undefined,
          last_name: text("last_name") || undefined,
        }),
      });
      await navigate(`/admin/customers/${created.customerId}`);
    } catch (caught) {
      // The dialog stays open so the operator keeps everything they typed.
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Create Customer"
      description="This creates the durable Customer and emails an invitation to the first User Account. The Customer sets Licensed States from Account after accepting; administrators never set or see a password."
    >
      <form onSubmit={(event) => void submit(event)}>
        <Stack gap={4}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          <Field label="Customer name" required>
            <Input name="name" autoComplete="off" autoFocus />
          </Field>
          <Field
            label="Email"
            description="The invitation is sent here. It is the address that signs in, not the Customer's identity."
            required
          >
            <Input name="email" type="email" autoComplete="off" />
          </Field>
          <Grid minColumnWidth="12rem" gap={4}>
            <Field label="First name">
              <Input name="first_name" autoComplete="off" />
            </Field>
            <Field label="Last name">
              <Input name="last_name" autoComplete="off" />
            </Field>
          </Grid>
          <Cluster gap={3}>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Creating…">
              Create Customer and send invitation
            </Button>
            <Button onClick={onClose}>Cancel</Button>
          </Cluster>
        </Stack>
      </form>
    </Dialog>
  );
}

function NicheAssignmentTransfer() {
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<NicheAssignmentUploadStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (!upload || !["queued", "ingesting"].includes(upload.status)) return;
    const timer = window.setTimeout(() => {
      void api<NicheAssignmentUploadStatus>(
        `/api/admin/niche-assignments/${upload.id}`,
      )
        .then(setUpload)
        .catch((caught: unknown) => setFailure(errorMessage(caught)));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [upload]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setFailure("Choose a Niche Assignment CSV.");
      return;
    }
    setBusy(true);
    setFailure("");
    const body = new FormData();
    body.append("file", file);
    try {
      setUpload(
        await api<NicheAssignmentUploadStatus>(
          "/api/admin/niche-assignments",
          { method: "POST", body },
        ),
      );
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const tone = upload?.status === "complete"
    ? "success"
    : upload?.status === "failed"
      ? "danger"
      : "info";

  return (
    <DisclosureSection
      title="Niche Assignment transfer"
      description="Export inventory that has no source-derived Niche, assign a Niche in CSV, then import the reviewed assignments. Source mappings always retain precedence."
      summary="Export / import CSV"
    >
      <Grid minColumnWidth="18rem">
        <Card>
          <Stack gap={3} align="flex-start">
            <Heading level={3} size="sm">1. Export unmapped inventory</Heading>
            <Text size="sm" tone="muted">
              Downloads phone, title, and state so the working file can be reviewed outside Jawnix.
            </Text>
            <ActionLink
              href="/api/admin/niche-assignments/export"
              variant="primary"
            >
              Export unmapped inventory
            </ActionLink>
          </Stack>
        </Card>
        <Card>
          <form onSubmit={(event) => void submit(event)}>
            <Stack gap={3} align="flex-start">
              <Heading level={3} size="sm">2. Import reviewed assignments</Heading>
              <Field
                label="Niche Assignment CSV"
                description="CSV with phone and niche headers. Rows with a source-derived Niche are safely skipped."
                required
              >
                <Input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(event) => {
                    setFile(event.currentTarget.files?.[0] ?? null);
                    setFailure("");
                  }}
                />
              </Field>
              <Button type="submit" variant="primary" busy={busy} disabled={!file}>
                Import assignments
              </Button>
            </Stack>
          </form>
        </Card>
      </Grid>
      {failure ? <Text role="alert" tone="danger">{failure}</Text> : null}
      {upload ? (
        <Card as="article" aria-label="Niche Assignment import status">
          <Stack gap={3}>
            <Cluster gap={2} justify="space-between">
              <Heading level={3} size="sm">{upload.filename}</Heading>
              <StatusBadge tone={tone}>{upload.status}</StatusBadge>
            </Cluster>
            {upload.error ? <Text tone="danger">{upload.error}</Text> : null}
            <Grid minColumnWidth="9rem" gap={2}>
              <Text size="sm"><strong>{upload.totalRows.toLocaleString()}</strong> rows</Text>
              <Text size="sm"><strong>{upload.acceptedRows.toLocaleString()}</strong> assigned</Text>
              <Text size="sm"><strong>{upload.skippedMappedRows.toLocaleString()}</strong> source-mapped skipped</Text>
              <Text size="sm" tone="muted">{upload.invalidRows.toLocaleString()} invalid</Text>
              <Text size="sm" tone="muted">{upload.duplicateRows.toLocaleString()} duplicates</Text>
            </Grid>
          </Stack>
        </Card>
      ) : null}
    </DisclosureSection>
  );
}

export function AdminCustomersRoute() {
  const data = useLoaderData<CustomerDirectoryData>();
  const [createOpen, setCreateOpen] = useState(false);
  useDocumentTitle("Customers");

  return (
    <Page
      title="Customers"
      density="data"
      description="A Customer is the durable party that owns Licensed States, Agency membership, and permanent distribution history. Its User Account is only replaceable access."
      actions={
        <Cluster gap={3}>
          <ActionLink href="/app/admin/agencies">View Agencies</ActionLink>
          <Button variant="primary" onClick={() => setCreateOpen(true)}>
            Create Customer
          </Button>
        </Cluster>
      }
    >
      <Section
        title="Find a Customer"
        description="Search by Customer name, slug, or the email on the current User Account."
      >
        <Card>
          <Form method="get" role="search">
            <Stack gap={4}>
              <Grid minColumnWidth="14rem" gap={4}>
                <Field label="Search">
                  <Input
                    name="q"
                    type="search"
                    defaultValue={data.filters.query}
                    autoComplete="off"
                  />
                </Field>
                <Field label="Status">
                  <Select name="status" defaultValue={data.filters.status}>
                    <option value="all">All</option>
                    <option value="active">Active</option>
                    <option value="deactivated">Deactivated</option>
                  </Select>
                </Field>
                <Field label="Agency">
                  <Select
                    name="agency_id"
                    defaultValue={
                      data.filters.agency_id === null
                        ? ""
                        : String(data.filters.agency_id)
                    }
                  >
                    <option value="">All Agencies</option>
                    {data.agencies.map((agency) => (
                      <option key={agency.id} value={String(agency.id)}>
                        {agency.active ? agency.name : `${agency.name} (inactive)`}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Licensed State">
                  <Select name="state" defaultValue={data.filters.state}>
                    <option value="">All Licensed States</option>
                    {data.states.map((state) => (
                      <option key={state} value={state}>
                        {state}
                      </option>
                    ))}
                  </Select>
                </Field>
              </Grid>
              <Cluster gap={4}>
                <label className="admin-customers__toggle">
                  <input
                    type="checkbox"
                    name="problems_only"
                    value="true"
                    defaultChecked={data.filters.problems_only}
                  />
                  <span>Only show setup problems</span>
                </label>
                <Button type="submit" variant="primary">
                  Search
                </Button>
              </Cluster>
            </Stack>
          </Form>
        </Card>
      </Section>

      <Section
        title="Customer directory"
        description="Each Customer shows its durable standing and its replaceable access standing separately."
      >
        <Stack gap={4}>
          <Text size="sm" tone="muted">
            Showing <Numeral>{data.matched.toLocaleString()}</Numeral> of{" "}
            <Numeral>{data.total.toLocaleString()}</Numeral> Customers.
          </Text>
          {data.customers.length ? (
            <Grid minColumnWidth="22rem">
              {data.customers.map((row) => (
                <CustomerCard key={row.id} row={row} />
              ))}
            </Grid>
          ) : (
            <EmptyState
              title="No Customers match this search"
              description="Nothing matched these filters. Widen the search, or create a Customer if this one does not exist yet."
            />
          )}
        </Stack>
      </Section>

      <NicheAssignmentTransfer />

      <CreateCustomerDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
    </Page>
  );
}
