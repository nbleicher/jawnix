import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { Button, ButtonLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Field, Input } from "../../design-system/primitives/form";
import { Page, Section, Stack } from "../../design-system/primitives/layout";
import { Panel } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { errorMessage } from "../operator/actions";
import { formatCount } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { BackToInventory } from "./inventoryTools";
import "./AdminInventory.css";

/** Niche assignment: export the inventory with no source-derived Niche,
 *  assign one in a CSV, import it. */

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

const NICHE_IMPORT_STATUS: Record<NicheAssignmentUploadStatus["status"], { label: string; tone: "info" | "success" | "danger" }> = {
  queued: { label: "Queued", tone: "info" },
  ingesting: { label: "Importing", tone: "info" },
  complete: { label: "Imported", tone: "success" },
  failed: { label: "Failed", tone: "danger" },
};

/** Niche Assignment transfer, moved here from Customers: export the
 *  inventory with no source-derived Niche, assign one in a CSV, import it. */
export function AdminNicheAssignmentRoute() {
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<NicheAssignmentUploadStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  useDocumentTitle("Niche assignment");

  useEffect(() => {
    if (!upload || !["queued", "ingesting"].includes(upload.status)) return;
    const timer = window.setTimeout(() => {
      // While the tab is hidden, keep the chain alive without reading.
      if (document.visibilityState === "hidden") {
        setPollAttempt((attempt) => attempt + 1);
        return;
      }
      void api<NicheAssignmentUploadStatus>(`/api/admin/niche-assignments/${upload.id}`)
        .then((next) => {
          setFailure("");
          setUpload(next);
        })
        .catch((caught: unknown) => {
          setFailure(errorMessage(caught));
          setPollAttempt((attempt) => attempt + 1);
        });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [upload, pollAttempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
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
        await api<NicheAssignmentUploadStatus>("/api/admin/niche-assignments", {
          method: "POST",
          body,
        }),
      );
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const status = upload ? NICHE_IMPORT_STATUS[upload.status] : null;
  return (
    <Page
      title="Niche assignment"
      width="data"
      density="data"
      description="Give a Niche to inventory that has none from its source. Source mappings always take precedence."
    >
      <Section title="1. Export unmapped inventory">
        <Text size="sm" tone="muted">
          Downloads phone, title, and state for every lead with no source-derived Niche, so the
          working file can be reviewed outside Jawnix.
        </Text>
        <div>
          <ButtonLink href="/api/admin/niche-assignments/export" variant="secondary" download>
            Export unmapped inventory
          </ButtonLink>
        </div>
      </Section>
      <Section title="2. Import reviewed assignments">
        <form onSubmit={(event) => void submit(event)} aria-label="Import Niche assignments">
          <Stack gap={3} align="flex-start">
            <Field
              label="Niche Assignment CSV"
              description="CSV with phone and niche headers. Rows with a source-derived Niche are skipped."
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
            <Button type="submit" variant="primary" busy={busy} busyLabel="Importing…" disabled={!file}>
              Import assignments
            </Button>
          </Stack>
        </form>
        {failure ? (
          <Text role="alert" tone="danger">
            {failure}
          </Text>
        ) : null}
      </Section>
      {upload && status ? (
        <Section title="Import status">
          <Panel title={upload.filename} action={<StatusBadge tone={status.tone}>{status.label}</StatusBadge>}>
            {upload.error ? <Text tone="danger">{upload.error}</Text> : null}
            <FactList
              label="Niche Assignment import status"
              items={[
                { term: "Rows", description: formatCount(upload.totalRows) },
                { term: "Assigned", description: formatCount(upload.acceptedRows) },
                { term: "Skipped, source-mapped", description: formatCount(upload.skippedMappedRows) },
                { term: "Invalid", description: formatCount(upload.invalidRows) },
                { term: "Duplicates", description: formatCount(upload.duplicateRows) },
              ]}
            />
          </Panel>
        </Section>
      ) : null}
      <BackToInventory />
    </Page>
  );
}
