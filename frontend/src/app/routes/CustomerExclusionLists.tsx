import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Select } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { Heading, Text } from "../../design-system/primitives/typography";
import {
  EXCLUSION_TYPES,
  INGESTING_STATUSES,
  exclusionTypeLabel,
  listMyExclusionLists,
  uploadMyExclusionList,
} from "./exclusionLists";
import type { ExclusionListStatus } from "./exclusionLists";

function statusPresentation(item: ExclusionListStatus): {
  label: string;
  tone: StatusTone;
} {
  // Once ingestion completes, the phones protect this Customer's batches
  // permanently — the admin's global decision never changes that, so
  // pending_confirmation, confirmed, and denied all read as protection here.
  switch (item.status) {
    case "queued":
      return { label: "Queued", tone: "neutral" };
    case "ingesting":
      return { label: "Processing", tone: "neutral" };
    case "failed":
      return { label: "Failed", tone: "danger" };
    default:
      return { label: "Protecting your batches", tone: "success" };
  }
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function failureMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function ExclusionListCard({ item }: { item: ExclusionListStatus }) {
  const status = statusPresentation(item);
  return (
    <Card as="article">
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between" align="start">
          <Heading level={3} size="sm">
            {item.filename}
          </Heading>
          <StatusBadge tone={status.tone}>{status.label}</StatusBadge>
        </Cluster>
        <Text size="sm">{exclusionTypeLabel(item.type)}</Text>
        {item.status === "failed" ? (
          <Text size="sm" tone="danger">
            {item.error || "The file could not be processed."}
          </Text>
        ) : item.ingestedAt ? (
          <Grid minColumnWidth="9rem" gap={2}>
            <Text size="sm">
              <strong>{item.acceptedRows.toLocaleString()}</strong> phones
            </Text>
            <Text size="sm" tone="muted">
              {item.invalidRows.toLocaleString()} invalid
            </Text>
            <Text size="sm" tone="muted">
              {item.duplicateRows.toLocaleString()} duplicates
            </Text>
          </Grid>
        ) : null}
        <Text size="xs" tone="muted">
          Uploaded {formatDate(item.createdAt)}
        </Text>
      </Stack>
    </Card>
  );
}

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
    <Section
      title="Exclusion Lists"
      description="Upload phones you know are landlines, DNC-registered, or TCPA litigators. Once a list is processed, those phones never appear in your batches — permanently."
    >
      <Stack gap={5}>
        <Card>
          <form onSubmit={(event) => void submit(event)} noValidate>
            <Stack gap={4}>
              <Text size="sm" tone="muted">
                A CSV with a phone column, between 1,000 and 50,000 rows.
              </Text>
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
                    onChange={(event) =>
                      setFile(event.currentTarget.files?.[0] ?? null)
                    }
                  />
                </Field>
                <Button type="submit" disabled={busy}>
                  {busy ? "Uploading…" : "Upload list"}
                </Button>
              </Cluster>
              {uploadFailure ? (
                <Text size="sm" tone="danger" role="alert">
                  {uploadFailure}
                </Text>
              ) : null}
            </Stack>
          </form>
        </Card>
        {loadFailure ? (
          <Card>
            <Cluster gap={3} justify="space-between" align="center">
              <Text size="sm" role="alert">
                Your Exclusion Lists could not be loaded right now.
              </Text>
              <Button
                variant="secondary"
                onClick={() => setLoadAttempt((attempt) => attempt + 1)}
              >
                Retry
              </Button>
            </Cluster>
          </Card>
        ) : items === null ? null : items.length ? (
          <Grid minColumnWidth="18rem">
            {items.map((item) => (
              <ExclusionListCard item={item} key={item.id} />
            ))}
          </Grid>
        ) : (
          <EmptyState
            title="No Exclusion Lists yet"
            description="Uploads appear here with their processing status and accepted phone counts."
          />
        )}
      </Stack>
    </Section>
  );
}
