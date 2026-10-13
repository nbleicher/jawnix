import { useEffect, useRef, useState } from "react";
import { useLoaderData, useRevalidator } from "react-router";
import { api } from "../auth/adminMFA";
import { submitLeadUpload, type LeadUploadStatus } from "../auth/managedAdmin";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Card, Cluster, Grid, Page, Section, Stack } from "../../design-system/primitives/layout";
import { Heading, Numeral, Text } from "../../design-system/primitives/typography";
import { StatusBadge } from "../../design-system/primitives/status";
import { EmptyState } from "../../design-system/primitives/feedback";
import "./ManagedAdmin.css";

interface UploadWorkspace {
  uploads: LeadUploadStatus[];
  states: { code: string; name: string }[];
  inventory: { total: number; states: Record<string, number>; asOf: string | null };
}
export const managedUploadsLoader = () => api<UploadWorkspace>("/api/manager/uploads");

interface ManagedCustomer {
  id: number;
  name: string;
  email: string;
  active: boolean;
  licensed_states: string[];
  lead_database: "db1" | "db2";
}
interface CustomerWorkspace { customers: ManagedCustomer[]; available: ManagedCustomer[] }
export const managedCustomersLoader = () => api<CustomerWorkspace>("/api/manager/customers");

const STATUS = { uploading:"Uploading", queued:"Queued", processing:"Importing", complete:"Ready", failed:"Failed" };
function active(item: LeadUploadStatus) { return item.status === "queued" || item.status === "processing"; }

export function ManagedUploadsRoute() {
  const initial = useLoaderData<UploadWorkspace>();
  const revalidator = useRevalidator();
  const revalidate = revalidator.revalidate;
  const [uploads, setUploads] = useState(initial.uploads);
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const revisions = useRef<Record<string, number>>({});
  useDocumentTitle("Upload leads");
  useEffect(() => {
    setUploads(current => {
      const known = new Set(initial.uploads.map(item => item.id));
      return [...current.filter(item => !known.has(item.id)), ...initial.uploads].slice(0,50);
    });
  }, [initial]);
  const scope = uploads.filter(active).map(item => item.id).sort().join(",");

  useEffect(() => {
    if (!scope) return;
    let mounted = true;
    let inFlight = false;
    const ids = scope.split(",");
    async function refresh() {
      if (inFlight || document.visibilityState === "hidden") return;
      inFlight = true;
      const versions = ids.map(id => revisions.current[id] ?? 0);
      try {
        const changed = await Promise.all(ids.map(id => api<LeadUploadStatus>(`/api/manager/uploads/${id}`)));
        if (!mounted) return;
        setUploads(current => current.map(item => {
          const index = changed.findIndex(row => row.id === item.id);
          return index >= 0 && (revisions.current[item.id] ?? 0) === versions[index] ? changed[index]! : item;
        }));
        if (changed.some(item => item.status === "complete")) void revalidate();
      } catch (caught) {
        const status = (caught as { status?: number }).status;
        if (status === 401 || status === 403) void revalidate();
      } finally { inFlight = false; }
    }
    const interval = setInterval(() => { void refresh(); }, 5000);
    return () => { mounted = false; clearInterval(interval); };
  }, [scope, revalidate]);

  function choose(selected: File | null) {
    if (busy) return;
    if (selected && !selected.name.toLowerCase().endsWith(".csv")) {
      setError("Choose a CSV file."); return;
    }
    if (picker.current) {
      if (selected) {
        const transfer = new DataTransfer();
        transfer.items.add(selected);
        picker.current.files = transfer.files;
      } else picker.current.value = "";
    }
    setFile(selected); setError("");
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || !state || busy) return;
    setBusy(true); setProgress(0); setError("");
    try {
      const item = await submitLeadUpload(file, state, setProgress);
      setUploads(current => [item, ...current]);
      setFile(null);
      if (picker.current) picker.current.value = "";
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The upload could not be completed.");
      if ([401,403].includes((caught as { status?: number }).status ?? 0)) void revalidator.revalidate();
    } finally { setBusy(false); }
  }
  async function retry(id: string) {
    setError("");
    revisions.current[id] = (revisions.current[id] ?? 0)+1;
    try {
      const item = await api<LeadUploadStatus>(`/api/manager/uploads/${id}/retry`, { method:"POST" });
      setUploads(current => current.map(row => row.id === id ? item : row));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Retry was not accepted."); }
  }

  return <Page title="Upload leads" description="Add your CSV leads to DB2. Choose the state for the entire file, then upload.">
    <Section title="New upload">
      <Card><form onSubmit={submit}><Stack gap={5}>
        <div className="managed-upload__drop" data-dragging={dragging || undefined}
          onDragOver={event => { event.preventDefault(); if (!busy) setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={event => { event.preventDefault(); setDragging(false); if (event.dataTransfer.files.length === 1) choose(event.dataTransfer.files[0] ?? null); else setError("Choose one CSV file per upload."); }}>
          <Text weight="semibold">Drop a CSV file here</Text>
          <Field label="CSV file" required description="Phone or Phone Number column required. Name or Business Name is optional.">
            <Input ref={picker} type="file" accept=".csv,text/csv" disabled={busy} onChange={event => choose(event.target.files?.[0] ?? null)} />
          </Field>
          {file ? <Text size="sm">{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</Text> : null}
        </div>
        <Field label="State" required><Select value={state} onChange={event => setState(event.target.value)} disabled={busy}>
          <option value="">Choose a state</option>
          {initial.states.map(item => <option key={item.code} value={item.code}>{item.name} ({item.code})</option>)}
        </Select></Field>
        {error ? <Text role="alert" tone="danger">{error}</Text> : null}
        {busy ? <Stack gap={2}><Text role="status">Uploading · {progress}%</Text><progress aria-label="File upload progress" max={100} value={progress} /></Stack> : null}
        <Cluster><Button type="submit" variant="primary" busy={busy} busyLabel="Uploading…" disabled={!file || !state}>Upload CSV</Button></Cluster>
        <Text size="sm" tone="muted">Large files import in the background. Leads become available after the complete file is processed.</Text>
      </Stack></form></Card>
    </Section>
    <Section title="DB2 inventory"><Card><Stack gap={2}>
      <Text><Numeral>{initial.inventory.total.toLocaleString()}</Numeral> phone numbers</Text>
      <Text size="sm" tone="muted">{Object.entries(initial.inventory.states).map(([code,count]) => `${code}: ${count.toLocaleString()}`).join(" · ") || "No leads uploaded yet."}</Text>
    </Stack></Card></Section>
    <Section title="Recent uploads"><Stack gap={4}>
      {uploads.length ? uploads.map(item => <Card key={item.id} as="article" aria-label={item.filename}><Stack gap={3}>
        <Cluster justify="space-between"><Heading level={3}>{item.filename}</Heading><StatusBadge tone={item.status === "failed" ? "danger" : item.status === "complete" ? "success" : "info"}>{STATUS[item.status]}</StatusBadge></Cluster>
        <Text size="sm">{item.state} · {item.processedRows.toLocaleString()} records processed</Text>
        {item.status === "complete" ? <Text size="sm">{item.importedRows.toLocaleString()} added · {item.updatedRows.toLocaleString()} updated · {item.duplicateRows.toLocaleString()} duplicates · {item.rejectedRows.toLocaleString()} rejected</Text> : null}
        {item.error ? <Text role="alert" tone="danger">{item.error}</Text> : null}
        <Cluster>
          {item.status === "failed" ? <Button onClick={() => { void retry(item.id); }}>Retry import</Button> : null}
          {item.rejectedRows > 0 && ["complete","failed"].includes(item.status) ? <ActionLink href={`/api/manager/uploads/${item.id}/rejected`} download>Download rejected rows</ActionLink> : null}
        </Cluster>
      </Stack></Card>) : <EmptyState title="No uploads yet" description="Upload a CSV to start building your DB2 inventory." />}
    </Stack></Section>
  </Page>;
}

export function ManagedCustomersRoute() {
  const initial = useLoaderData<CustomerWorkspace>();
  const [data, setData] = useState(initial);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setData(initial); setSelected([]); }, [initial]);
  useDocumentTitle("Users");
  async function add() {
    if (!selected.length || busy) return;
    setBusy(true); setError("");
    try {
      setData(await api<CustomerWorkspace>("/api/manager/customers", { method:"POST", body:JSON.stringify({ customer_ids:selected }) }));
      setSelected([]);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "The selected users could not be added."); }
    finally { setBusy(false); }
  }
  return <Page title="Users" description="Manage the users assigned to you. The operator controls which users are available and which database they use.">
    <Section title="Your users"><Grid minColumnWidth="20rem">
      {data.customers.map(item => <Card key={item.id} as="article" aria-label={item.name}><Stack gap={2}>
        <Heading level={3}>{item.name}</Heading><Text size="sm">{item.email}</Text>
        <Text size="sm">{item.licensed_states.join(", ") || "No licensed states"} · {item.lead_database.toUpperCase()}</Text>
        {!item.active ? <StatusBadge tone="warning">Inactive</StatusBadge> : null}
      </Stack></Card>)}
      {!data.customers.length ? <EmptyState title="No users assigned" description="Select from the users the operator makes available below." /> : null}
    </Grid></Section>
    <Section title="Available users"><Card><Stack gap={4}>
      {data.available.map(item => <label className="managed-users__choice" key={item.id}>
        <input type="checkbox" disabled={busy} checked={selected.includes(item.id)} onChange={event => setSelected(current => event.target.checked ? [...current,item.id] : current.filter(id => id !== item.id))} />
        <span>{item.name} <small>{item.email}</small></span>
      </label>)}
      {!data.available.length ? <Text tone="muted">No additional users are available.</Text> : null}
      {error ? <Text role="alert" tone="danger">{error}</Text> : null}
      <Cluster><Button variant="primary" disabled={!selected.length} busy={busy} busyLabel="Adding…" onClick={() => { void add(); }}>Add selected users</Button></Cluster>
    </Stack></Card></Section>
  </Page>;
}
