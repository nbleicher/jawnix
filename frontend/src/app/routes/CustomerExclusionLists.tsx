import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { Field, Input, Select } from "../../design-system/primitives/form";
import { Cluster, Section } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { formatCount, formatDay } from "../customer/format";
import {
  EXCLUSION_TYPES,
  INGESTING_STATUSES,
  exclusionTypeLabel,
  listMyExclusionLists,
  uploadMyExclusionList,
} from "./exclusionLists";
import type { ExclusionListStatus } from "./exclusionLists";

import "./CustomerExclusionLists.css";

function statusPresentation(item: ExclusionListStatus): {
  label: string;
  tone: StatusTone;
} {
  // Once ingestion completes, the phones protect this Customer's batches
  // permanently — the admin's global decision never changes that, so
  // pending_confirmation, confirmed, and denied all read as protection here.
  switch (item.status) {
    case "queued":
      return { label: "Queued", tone: "info" };
    case "ingesting":
      return { label: "Processing", tone: "info" };
    case "failed":
      return { label: "Failed", tone: "danger" };
    default:
      return { label: "Protecting your batches", tone: "success" };
  }
}

function failureMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function processed(item: ExclusionListStatus): boolean {
  return item.status !== "failed" && item.ingestedAt !== null;
}

/** "30 invalid, 20 duplicates": rows the list could not use. */
function skipped(item: ExclusionListStatus): string {
  if (!processed(item)) return "—";
  if (!item.invalidRows && !item.duplicateRows) return "None";
  return `${formatCount(item.invalidRows)} invalid, ${formatCount(item.duplicateRows)} duplicates`;
}

const COLUMNS: DataColumn<ExclusionListStatus>[] = [
  { key: "file", header: "File", cell: (item) => item.filename },
  { key: "type", header: "Type", cell: (item) => exclusionTypeLabel(item.type) },
  {
    key: "status",
    header: "Status",
    cell: (item) => {
      const status = statusPresentation(item);
      return (
        <>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
          {item.status === "failed" ? (
            <span className="exclusion-lists__error">
              {item.error || "The file could not be processed."}
            </span>
          ) : null}
        </>
      );
    },
  },
  {
    key: "phones",
    header: "Phones",
    numeric: true,
    cell: (item) => (processed(item) ? formatCount(item.acceptedRows) : "—"),
  },
  { key: "skipped", header: "Skipped", cell: skipped },
  { key: "uploaded", header: "Uploaded", cell: (item) => formatDay(item.createdAt) },
];

export function CustomerExclusionListsSection() {
  const [items, setItems] = useState<ExclusionListStatus[] | null>(null);
  const [loadFailure, setLoadFailure] = useState("");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState<string>(EXCLUSION_TYPES[0].value);
  const [busy, setBusy] = useState(false);
  const [uploadFailure, setUploadFailure] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    listMyExclusionLists()
      .then((loaded) => {
        if (cancelled) return;
        setItems(loaded);
        setLoadFailure("");
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setLoadFailure(failureMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [loadAttempt]);

  const ingesting =
    items?.some((item) => INGESTING_STATUSES.includes(item.status)) ?? false;

  useEffect(() => {
    if (!ingesting) return;
    const timer = window.setTimeout(() => {
      listMyExclusionLists()
        .then((loaded) => setItems(loaded))
        .catch(() => setPollAttempt((attempt) => attempt + 1));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [ingesting, items, pollAttempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setUploadFailure("Choose a CSV file to upload.");
      return;
    }
    setBusy(true);
    setUploadFailure("");
    try {
      const created = await uploadMyExclusionList(file, type);
      setItems((current) => [created, ...(current ?? [])]);
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
    } catch (caught: unknown) {
      setUploadFailure(failureMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Section
        title="Upload a list"
        description="Phones you know are landlines, on the Do Not Call registry, or TCPA litigators. Once a list is processed, those phones never appear in your batches."
      >
        <form className="exclusion-lists__upload" onSubmit={(event) => void submit(event)} noValidate>
          <Cluster gap={3} align="end">
            <Field label="Type" id="exclusion-type" required>
              <Select
                id="exclusion-type"
                value={type}
                onChange={(event) => setType(event.currentTarget.value)}
              >
                {EXCLUSION_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="CSV file" id="exclusion-file" required>
              <Input
                id="exclusion-file"
                ref={fileInput}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => setFile(event.currentTarget.files?.[0] ?? null)}
              />
            </Field>
            <Button type="submit" busy={busy} busyLabel="Uploading…">
              Upload list
            </Button>
          </Cluster>
          <Text size="sm" tone="muted">
            A CSV with a phone column, between 1,000 and 50,000 rows.
          </Text>
          {uploadFailure ? (
            <Text size="sm" tone="danger" role="alert">
              {uploadFailure}
            </Text>
          ) : null}
        </form>
      </Section>
      <Section title="Your lists">
        {loadFailure ? (
          <Cluster gap={3} justify="space-between" align="center" className="exclusion-lists__retry">
            <Text size="sm" role="alert">
              Your exclusion lists could not be loaded right now.
            </Text>
            <Button variant="secondary" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
              Retry
            </Button>
          </Cluster>
        ) : items === null ? null : (
          <DataTable
            label="Your exclusion lists"
            columns={COLUMNS}
            rows={items}
            rowKey={(item) => item.id}
            empty="No lists yet. Uploads appear here with their status and how many phones they protect."
          />
        )}
      </Section>
    </>
  );
}
