import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import {
  Field,
  Input,
  Select,
  Textarea,
} from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  DisclosureSection,
  Grid,
  Page,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import type { TerminalDestination } from "../../design-system/primitives/terminal";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { EXCLUSION_TYPES, INGESTING_STATUSES } from "./exclusionLists";
import type { ExclusionListStatus } from "./exclusionLists";

/**
 * The native Acquisition workspace (#68).
 *
 * Jawnix-owned acquisition records — Nightly Reviews, Scrape Anomalies, Source
 * Recommendations, Niche mappings, and Scraper Configuration versions — read
 * through one authenticated contract and wear the GMS/OPS identity #62
 * established.
 *
 * Two boundaries this screen exists to hold:
 *
 * A Scrape Anomaly decision posts to the endpoint that calls the same durable
 * command Telegram uses. The screen carries no decision logic of its own; it
 * renders what the contract says is decidable and posts the verb back.
 *
 * A Source Recommendation decision sends the evidence checksum it displayed.
 * Approving numbers that moved after they were read is refused server-side
 * rather than applied to numbers nobody saw.
 */

export interface AnomalySegment {
  key?: string;
  reasons?: string[];
}

export interface ScrapeAnomalyRow {
  id: string;
  status: string;
  scraperRunId: number;
  configurationId: string;
  datasetChecksum: string;
  decisionBy: string;
  decisionReason: string;
  decidedAt: string | null;
  createdAt: string;
  runStatus: string | null;
  anomalousSegments: AnomalySegment[];
  decidable: boolean;
}

export interface RecommendationRow {
  id: string;
  niche: string;
  segment: string;
  action: string;
  status: string;
  evidence: Record<string, unknown>;
  evidenceChecksum: string;
  configurationVersion: number | null;
  decisionBy: string;
  decisionReason: string;
  decidedAt: string | null;
  createdAt: string;
  resultingConfigurationId: string | null;
  decidable: boolean;
}

export interface NicheRow {
  segment: string;
  state: string;
  keyword: string;
  niche: string;
  confirmed: boolean;
  proposalSource: string;
  evidence: Record<string, unknown>;
  confirmedBy: string;
  confirmedAt: string | null;
  denied?: boolean;
  deniedBy?: string;
  deniedAt?: string | null;
}

export interface ConfigurationRow {
  id: string;
  version: number;
  checksum: string;
  status: string;
  reason: string;
  createdAt: string;
  scheduledAt: string | null;
  activatedAt: string | null;
  basedOnConfigurationId: string | null;
  segmentCount: number;
  anomalyThresholds: Record<string, unknown>;
}

export interface NightlyReviewRow {
  id: string;
  scraperRunId: number | null;
  reviewDate: string | null;
  status: string;
  summary: Record<string, unknown>;
  telegramDeliveryState: string;
  telegramMessageId: string;
  telegramDeliveryError: string;
  createdAt: string;
  reconcilable: boolean;
}

export interface ExclusionReviewRow {
  id: string;
  customerId: number | null;
  customerName: string;
  type: string;
  filename: string;
  status: string;
  acceptedRows: number;
  invalidRows: number;
  duplicateRows: number;
  poolImpact: number;
  createdAt: string;
}

interface ReviewFailure {
  kind?: string;
  message?: string;
}

/** Reads the Nightly Review summary defensively.
 *
 * Two builders write it — the in-process nightly attempt and the scheduled
 * review — and their shapes differ. Rendering what is present beats asserting
 * one shape and showing nothing when the other arrives. */
function summaryLines(summary: Record<string, unknown>): string[] {
  const lines: string[] = [];
  const read = (key: string) =>
    (summary[key] ?? {}) as Record<string, unknown>;

  const configuration = read("configuration");
  if (configuration["version"] !== undefined) {
    lines.push(
      `Configuration v${String(configuration["version"])} · ${String(
        configuration["status"] ?? "unknown",
      ).replaceAll("_", " ")}`,
    );
  }
  const run = read("run");
  if (run["status"] !== undefined) {
    lines.push(`Run ${String(run["status"]).replaceAll("_", " ")}`);
  }
  const dataset = read("dataset");
  if (dataset["version"] !== undefined && dataset["version"] !== null) {
    lines.push(
      `Dataset v${String(dataset["version"])} · sync ${String(
        dataset["syncStatus"] ?? "unknown",
      ).replaceAll("_", " ")}`,
    );
  }
  const segments = summary["segments"];
  if (Array.isArray(segments)) {
    const anomalous = segments.filter(
      (segment) => (segment as Record<string, unknown>)["anomalous"],
    ).length;
    lines.push(
      `${segments.length} Source Segments${
        anomalous ? ` · ${anomalous} anomalous` : ""
      }`,
    );
  }
  const inventory = read("inventory");
  if (inventory["total"] !== undefined) {
    lines.push(
      `Inventory ${Number(inventory["total"]).toLocaleString()} total · ${Number(
        inventory["eligible"] ?? 0,
      ).toLocaleString()} eligible`,
    );
  }
  return lines;
}

function summaryFailures(summary: Record<string, unknown>): ReviewFailure[] {
  const failures = summary["failures"];
  return Array.isArray(failures) ? (failures as ReviewFailure[]) : [];
}

export interface AcquisitionData {
  exclusionLists?: ExclusionReviewRow[];
  nightlyReviews: NightlyReviewRow[];
  scrapeAnomalies: ScrapeAnomalyRow[];
  sourceRecommendations: RecommendationRow[];
  nicheMappings: NicheRow[];
  scraperConfigurations: ConfigurationRow[];
}

function ExclusionReviewCard({ item }: { item: ExclusionReviewRow }) {
  return (
    <Card as="article">
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={3} size="sm">{item.customerName}</Heading>
          <StatusBadge tone="warning">Awaiting review</StatusBadge>
        </Cluster>
        <Text size="sm">
          {item.type.replaceAll("_", " ")} · {item.filename}
        </Text>
        <Grid minColumnWidth="9rem" gap={2}>
          <Text size="sm"><strong>{item.acceptedRows.toLocaleString()}</strong> accepted</Text>
          <Text size="sm"><strong>{item.poolImpact.toLocaleString()}</strong> pool impact</Text>
          <Text size="sm" tone="muted">{item.invalidRows.toLocaleString()} invalid</Text>
          <Text size="sm" tone="muted">{item.duplicateRows.toLocaleString()} duplicates</Text>
        </Grid>
        <Text size="xs" tone="muted">Uploaded {formatDate(item.createdAt)}</Text>
        <Cluster gap={2}>
          <Decision
            label="Confirm globally"
            consequence="Makes every accepted phone in this Customer upload globally ineligible. The current pool impact is recalculated at decision time."
            endpoint={`/api/admin/exclusion-lists/${item.id}/confirm`}
          />
          <Decision
            label="Deny global effect"
            consequence="Keeps this list scoped to the uploading Customer. Its phones will not become globally ineligible."
            endpoint={`/api/admin/exclusion-lists/${item.id}/deny`}
            destructive
          />
        </Cluster>
      </Stack>
    </Card>
  );
}

/** Administrator bulk upload (#153 story 25): global effect immediately after
 *  ingestion, no Nightly Review gate, so the form demands an audit reason. */
export function AdminExclusionUpload() {
  const revalidator = useRevalidator();
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState<string>(EXCLUSION_TYPES[0].value);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [upload, setUpload] = useState<ExclusionListStatus | null>(null);
  const [pollAttempt, setPollAttempt] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  const ingesting =
    upload !== null && INGESTING_STATUSES.includes(upload.status);

  useEffect(() => {
    if (!ingesting || upload === null) return;
    const timer = window.setTimeout(() => {
      void api<ExclusionListStatus>(`/api/admin/exclusion-lists/${upload.id}`)
        .then((next) => {
          setUpload(next);
          if (!INGESTING_STATUSES.includes(next.status)) {
            revalidator.revalidate();
          }
        })
        .catch(() => setPollAttempt((attempt) => attempt + 1));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [ingesting, upload, pollAttempt, revalidator]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setFailure("Choose a CSV file to upload.");
      return;
    }
    if (!reason.trim()) {
      setFailure("An upload reason is required.");
      return;
    }
    setBusy(true);
    setFailure("");
    const body = new FormData();
    body.append("file", file);
    body.append("type", type);
    body.append("reason", reason.trim());
    try {
      setUpload(
        await api<ExclusionListStatus>("/api/admin/exclusion-lists", {
          method: "POST",
          body,
        }),
      );
      setFile(null);
      setReason("");
      if (fileInput.current) fileInput.current.value = "";
    } catch (caught: unknown) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "The Exclusion List could not be uploaded.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card as="article">
      <form onSubmit={(event) => void submit(event)} noValidate>
        <Stack gap={4}>
          <Heading level={3} size="sm">
            Administrator bulk upload
          </Heading>
          <Text size="sm" tone="muted">
            Every accepted phone becomes globally ineligible as soon as
            processing completes — no Nightly Review confirmation. CSV with a
            phone column, 1,000–50,000 rows.
          </Text>
          <Cluster gap={3} align="end">
            <Field label="Type" id="admin-exclusion-type" required>
              <Select
                id="admin-exclusion-type"
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
            <Field label="CSV file" id="admin-exclusion-file" required>
              <Input
                id="admin-exclusion-file"
                ref={fileInput}
                type="file"
                accept=".csv,text/csv"
                onChange={(event) =>
                  setFile(event.currentTarget.files?.[0] ?? null)
                }
              />
            </Field>
          </Cluster>
          <Field
            label="Reason"
            id="admin-exclusion-reason"
            description="Recorded in Activity as the audit reason for the global effect."
            required
          >
            <Input
              id="admin-exclusion-reason"
              value={reason}
              onChange={(event) => setReason(event.currentTarget.value)}
            />
          </Field>
          <Cluster gap={3} align="center">
            <Button type="submit" disabled={busy}>
              {busy ? "Uploading…" : "Upload globally"}
            </Button>
            {upload ? (
              <Text size="sm" role="status">
                {upload.status === "failed"
                  ? `Failed: ${upload.error || "the file could not be processed."}`
                  : INGESTING_STATUSES.includes(upload.status)
                    ? `Processing ${upload.filename}…`
                    : `${upload.acceptedRows.toLocaleString()} phones from ${upload.filename} are now globally ineligible.`}
              </Text>
            ) : null}
          </Cluster>
          {failure ? (
            <Text size="sm" tone="danger" role="alert">
              {failure}
            </Text>
          ) : null}
        </Stack>
      </form>
    </Card>
  );
}

const DESTINATIONS: TerminalDestination[] = [
  { label: "Acquisition review", href: "/app/admin/acquisition", current: true },
  { label: "Scraper Operations", href: "/app/admin/acquisition/scraper" },
  // The administrator navigation deliberately omits Security, so this rail is
  // the only way to reach it. Dropping it here would strand the route.
  { label: "Administrator security", href: "/app/admin/security" },
  { label: "Exit to Overview", href: "/app/admin/overview" },
];

const REVIEW_TONES: Record<string, StatusTone> = {
  complete: "success",
  attention: "warning",
  waiting_publication: "warning",
};

const ANOMALY_TONES: Record<string, StatusTone> = {
  pending: "warning",
  confirmed: "success",
  denied: "neutral",
  superseded: "neutral",
};

const CONFIGURATION_TONES: Record<string, StatusTone> = {
  active: "success",
  scheduled: "info",
  draft: "neutral",
  superseded: "neutral",
  schedule_replaced: "neutral",
  restored: "info",
};

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The action could not be completed.";
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.toISOString().replace("T", " ").slice(0, 16)} UTC`;
}

export async function acquisitionLoader(): Promise<AcquisitionData> {
  return api<AcquisitionData>("/api/admin/acquisition");
}

interface DecisionProps {
  /** Label shown on the trigger, and the dialog's accessible name. */
  label: string;
  /** Plain-language statement of what confirming does. */
  consequence: string;
  endpoint: string;
  destructive?: boolean;
  /** Extra fields posted alongside the reason — the evidence binding. */
  body?: Record<string, unknown>;
}

/** One consequential action, behind a confirmation that collects its reason. */
function Decision({
  label,
  consequence,
  endpoint,
  destructive = false,
  body,
}: DecisionProps) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revalidator = useRevalidator();

  async function confirm() {
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(endpoint, {
        method: "POST",
        body: JSON.stringify({ ...body, reason: reason.trim() }),
      });
      setOpen(false);
      setReason("");
      revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
      // A refusal usually means the record moved on — in Telegram, or because
      // the evidence changed. Re-read so the screen stops offering it.
      revalidator.revalidate();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        variant={destructive ? "danger" : "secondary"}
        onClick={() => {
          setOpen(true);
          setReason("");
          setError("");
        }}
      >
        {label}
      </Button>
      <ConfirmDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={() => void confirm()}
        title={label}
        consequence={consequence}
        confirmLabel={label}
        destructive={destructive}
        busy={busy}
      >
        <Field
          label="Reason"
          description="Recorded in Activity so this decision can be explained later."
          required
          {...(error ? { error } : {})}
        >
          <Textarea
            name="reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
      </ConfirmDialog>
    </>
  );
}

/** Confirming or correcting a Niche mapping, which always needs a reason. */
function NicheCard({ item }: { item: NicheRow }) {
  const [open, setOpen] = useState(false);
  const [niche, setNiche] = useState(item.niche);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revalidator = useRevalidator();

  async function confirm() {
    if (!niche.trim()) {
      setError("A Niche is required.");
      return;
    }
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(
        `/api/admin/source-niches/${encodeURIComponent(item.segment)}/confirm`,
        {
          method: "POST",
          body: JSON.stringify({ niche: niche.trim(), reason: reason.trim() }),
        },
      );
      setOpen(false);
      setReason("");
      revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
      revalidator.revalidate();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card as="article">
      <Stack gap={2}>
        <Heading level={3} size="sm">
          {item.segment}
        </Heading>
        <Text size="sm" tone="muted">
          {item.state} · {item.keyword}
        </Text>
        <Text size="sm">
          Proposed <strong>{item.niche || "none"}</strong> by{" "}
          {item.proposalSource.replaceAll("_", " ")}.
        </Text>
        <Cluster gap={2}>
          <Button
            onClick={() => {
              setOpen(true);
              setNiche(item.niche);
              setReason("");
              setError("");
            }}
          >
            Review and confirm
          </Button>
          <Decision
            label="Deny proposal"
            consequence="Removes this proposal from the review queue without confirming a Niche. The Source Segment remains ineligible for automated recommendations until a later proposal is reviewed."
            endpoint={`/api/admin/source-niches/${encodeURIComponent(item.segment)}/deny`}
            destructive
          />
        </Cluster>
        <ConfirmDialog
          open={open}
          onClose={() => setOpen(false)}
          onConfirm={() => void confirm()}
          title={`Confirm Niche for ${item.segment}`}
          consequence="Confirming a Niche makes this Source Segment comparable against its same-niche peers, which is what lets Source Recommendations become eligible. Correct the Niche here if the proposal is wrong."
          confirmLabel="Confirm Niche"
          destructive={false}
          busy={busy}
        >
          <Stack gap={3}>
            <Field label="Niche" required>
              <Input
                name="niche"
                value={niche}
                onChange={(event) => setNiche(event.target.value)}
              />
            </Field>
            <Field
              label="Reason"
              description="Recorded in Activity so this decision can be explained later."
              required
              {...(error ? { error } : {})}
            >
              <Textarea
                name="reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
          </Stack>
        </ConfirmDialog>
      </Stack>
    </Card>
  );
}

function NicheReviewQueue({ items }: { items: NicheRow[] }) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return needle
      ? items.filter((item) =>
          [item.segment, item.state, item.keyword, item.niche].some((value) =>
            value.toLocaleLowerCase().includes(needle),
          ),
        )
      : items;
  }, [items, query]);

  useEffect(() => {
    setIndex((current) => Math.min(current, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  const current = filtered[index];
  return (
    <Stack gap={3}>
      <Field
        label="Find a Niche mapping"
        description={`${filtered.length.toLocaleString()} of ${items.length.toLocaleString()} pending proposals.`}
      >
        <Input
          type="search"
          value={query}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            setIndex(0);
          }}
          placeholder="State, keyword, segment, or proposed Niche"
        />
      </Field>
      {current ? (
        <Stack gap={3}>
          <Cluster gap={2} justify="space-between">
            <Text size="sm" tone="muted" role="status" aria-live="polite">
              Proposal {index + 1} of {filtered.length}
            </Text>
            <Cluster gap={2}>
              <Button
                variant="ghost"
                disabled={index === 0}
                onClick={() => setIndex((value) => value - 1)}
              >
                Previous mapping
              </Button>
              <Button
                variant="ghost"
                disabled={index >= filtered.length - 1}
                onClick={() => setIndex((value) => value + 1)}
              >
                Next mapping
              </Button>
            </Cluster>
          </Cluster>
          <NicheCard item={current} />
        </Stack>
      ) : (
        <EmptyState
          title="No Niche mappings match"
          description="Clear the search to return to the pending review queue."
          action={<Button onClick={() => setQuery("")}>Clear search</Button>}
        />
      )}
    </Stack>
  );
}

function AnomalyCard({ item }: { item: ScrapeAnomalyRow }) {
  return (
    <Card as="article">
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={3} size="sm">
            <Link to={`/admin/acquisition/runs/${item.scraperRunId}`}>
              Scrape Run {item.scraperRunId}
            </Link>
          </Heading>
          <StatusBadge tone={ANOMALY_TONES[item.status] ?? "neutral"}>
            {item.status}
          </StatusBadge>
        </Cluster>
        <Text size="sm" tone="muted">
          Held staged dataset <Mono>{item.datasetChecksum.slice(0, 12)}</Mono>
        </Text>
        {item.anomalousSegments.length ? (
          <Stack gap={1}>
            {item.anomalousSegments.map((segment, index) => (
              <Text size="sm" key={segment.key ?? index}>
                {segment.key ?? "Source Segment"} —{" "}
                {(segment.reasons ?? []).join(", ").replaceAll("_", " ") ||
                  "flagged"}
              </Text>
            ))}
          </Stack>
        ) : null}
        {item.decidable ? (
          <Cluster gap={2}>
            <Decision
              label="Confirm"
              consequence="Publishes the held staged dataset over the active Scraper Dataset and queues an Inventory Sync. The exact same command Telegram uses runs this."
              endpoint={`/api/admin/scrape-anomalies/${item.id}/confirm`}
            />
            <Decision
              label="Deny"
              consequence="Discards the held staged dataset. The last successful Scraper Dataset stays authoritative and nothing is published."
              endpoint={`/api/admin/scrape-anomalies/${item.id}/deny`}
              destructive
            />
          </Cluster>
        ) : (
          <Text size="sm" tone="muted">
            Decided by {item.decisionBy || "—"} on {formatDate(item.decidedAt)}.
            {item.decisionReason ? ` ${item.decisionReason}` : ""}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

function RecommendationCard({ item }: { item: RecommendationRow }) {
  const counts = (item.evidence?.["counts"] ?? {}) as Record<string, number>;
  const analysis = (item.evidence?.["analysis"] ?? {}) as Record<
    string,
    unknown
  >;
  return (
    <Card as="article">
      <Stack gap={3}>
        <Cluster gap={2} justify="space-between">
          <Heading level={3} size="sm">
            {item.action} · {item.segment}
          </Heading>
          <StatusBadge tone={item.decidable ? "info" : "neutral"}>
            {item.status}
          </StatusBadge>
        </Cluster>
        <Text size="sm" tone="muted">
          {item.niche}
          {item.configurationVersion !== null
            ? ` · evidence from configuration v${item.configurationVersion}`
            : ""}
        </Text>
        {/* The evidence must be readable before the decision, not after. */}
        <Stack gap={1}>
          <Text size="sm">
            Worked {counts["worked"] ?? 0} · rated {counts["rated"] ?? 0} ·
            eligibility {String(analysis["eligibility"] ?? "unknown")}
          </Text>
          {analysis["peerSegmentCount"] !== undefined ? (
            <Text size="sm" tone="muted">
              Compared against {String(analysis["peerSegmentCount"])} same-niche
              peers.
            </Text>
          ) : null}
          <Text size="sm" tone="muted">
            Evidence <Mono>{item.evidenceChecksum.slice(0, 12)}</Mono>
          </Text>
        </Stack>
        {item.decidable ? (
          <Cluster gap={2}>
            <Decision
              label="Approve"
              consequence="Records the approval. Where applying is enabled it schedules a new Scraper Configuration version carrying this change; in shadow mode it records the decision only. Either way no existing version is rewritten and no Scrape Run starts."
              endpoint={`/api/admin/source-recommendations/${item.id}/approve`}
              body={{ evidenceChecksum: item.evidenceChecksum }}
            />
            <Decision
              label="Deny"
              consequence="Records the refusal. Acquisition is unchanged, and this recommendation stays quiet until materially new evidence accrues."
              endpoint={`/api/admin/source-recommendations/${item.id}/deny`}
              destructive
              body={{ evidenceChecksum: item.evidenceChecksum }}
            />
          </Cluster>
        ) : (
          <Text size="sm" tone="muted">
            Decided by {item.decisionBy || "—"} on {formatDate(item.decidedAt)}.
          </Text>
        )}
      </Stack>
    </Card>
  );
}

export function AdminAcquisitionRoute() {
  const data = useLoaderData<AcquisitionData>();
  useDocumentTitle("Acquisition");

  const held = data.scrapeAnomalies.filter((item) => item.decidable);
  const pendingRecommendations = data.sourceRecommendations.filter(
    (item) => item.decidable,
  );
  const pendingExclusions = data.exclusionLists ?? [];
  const unconfirmedNiches = data.nicheMappings.filter(
    (item) => !item.confirmed && !item.denied,
  );
  const status = held.length
    ? `HELD / ${held.length} AWAITING DECISION`
    : "ONLINE / NOMINAL";

  return (
    <Page
      title="Acquisition"
      description="Nightly Reviews, held Scrape Anomalies, Source Recommendations, Niche mappings, and immutable Scraper Configuration versions."
    >
      <TerminalWorkspace
        status={status}
        tone={held.length ? "warning" : "online"}
        label="Acquisition workspace"
        destinations={DESTINATIONS}
      >
        <div id="acquisition-review">
          <Stack gap={6}>
            <div id="held-scrape-anomalies" />
            <DisclosureSection
              title="Held Scrape Anomalies"
              description="Flagged output waits here until an administrator confirms or denies it. Nothing is committed while it waits."
              summary={`${held.length} held`}
              defaultOpen={held.length > 0}
            >
              {held.length ? (
                <Grid minColumnWidth="20rem">
                  {held.map((item) => (
                    <AnomalyCard item={item} key={item.id} />
                  ))}
                </Grid>
              ) : (
                <EmptyState
                  title="No Scrape Anomalies are held"
                  description="A run is held when a Source Segment returns zero listings or moves more than its configured thresholds allow."
                />
              )}
            </DisclosureSection>

            <DisclosureSection
              title="Source Recommendations"
              description="Legacy worked-lead prescriptions are retained as dormant evidence. Existing proposals remain reviewable, but new prescriptions are not generated."
              summary={`${pendingRecommendations.length} legacy pending`}
            >
              {pendingRecommendations.length ? (
                <Grid minColumnWidth="20rem">
                  {pendingRecommendations.map((item) => (
                    <RecommendationCard item={item} key={item.id} />
                  ))}
                </Grid>
              ) : (
                <EmptyState
                  title="No Source Recommendations are waiting"
                  description="Recommendations appear once a Source Segment has enough worked Leads, enough Quality Ratings, a confirmed Niche, and eligible same-niche peers."
                />
              )}
            </DisclosureSection>

            <DisclosureSection
              title="Exclusion review"
              description="Customer uploads stay Customer-scoped until an administrator confirms or denies their global effect."
              summary={`${pendingExclusions.length} waiting`}
              defaultOpen={pendingExclusions.length > 0}
            >
              <Stack gap={4}>
                <AdminExclusionUpload />
                {pendingExclusions.length ? (
                  <Grid minColumnWidth="20rem">
                    {pendingExclusions.map((item) => (
                      <ExclusionReviewCard item={item} key={item.id} />
                    ))}
                  </Grid>
                ) : (
                  <EmptyState
                    title="No Exclusion Lists are waiting"
                    description="Ingested Customer uploads appear here with their validation counts and current pool impact."
                  />
                )}
              </Stack>
            </DisclosureSection>

            <DisclosureSection
              title="Niche mappings"
              description="Recommendations stay ineligible until a Niche is confirmed, so unconfirmed mappings block optimization."
              summary={`${unconfirmedNiches.length} awaiting review`}
              defaultOpen={unconfirmedNiches.length > 0}
            >
              {unconfirmedNiches.length ? (
                <NicheReviewQueue items={unconfirmedNiches} />
              ) : (
                <EmptyState
                  title="Every Niche mapping is confirmed"
                  description="Confirmed mappings let Source Recommendations compare a segment against its same-niche peers."
                />
              )}
            </DisclosureSection>

            <DisclosureSection
              id="scraper-configuration-versions"
              title="Scraper Configuration versions"
              description="Versions are immutable. Creating, scheduling, running, and rolling back all select a version; none rewrites one."
              summary={`${data.scraperConfigurations.length} versions`}
            >
              {data.scraperConfigurations.length ? (
                <Grid minColumnWidth="18rem">
                  {data.scraperConfigurations.map((item) => (
                    <Card as="article" key={item.id}>
                      <Stack gap={2}>
                        <Cluster gap={2} justify="space-between">
                          <Heading level={3} size="sm">
                            <Link
                              to={`/admin/acquisition/configurations/${item.id}`}
                            >
                              Version {item.version}
                            </Link>
                          </Heading>
                          <StatusBadge
                            tone={
                              CONFIGURATION_TONES[item.status] ?? "neutral"
                            }
                          >
                            {item.status.replaceAll("_", " ")}
                          </StatusBadge>
                        </Cluster>
                        <Text size="sm" tone="muted">
                          <Mono>{item.checksum.slice(0, 12)}</Mono> ·{" "}
                          {item.segmentCount} Source Segments
                        </Text>
                        <Text size="sm">{item.reason}</Text>
                        <Text size="sm" tone="muted">
                          Created {formatDate(item.createdAt)}
                          {item.basedOnConfigurationId
                            ? " · rolled forward from an earlier version"
                            : ""}
                        </Text>
                      </Stack>
                    </Card>
                  ))}
                </Grid>
              ) : (
                <EmptyState
                  title="No Scraper Configuration versions exist"
                  description="The first version is imported from the Scraper's authoritative Source Segment contract."
                />
              )}
            </DisclosureSection>

            <div id="nightly-reviews" />
            <DisclosureSection
              title="Nightly Reviews"
              description="The durable record of each night's run, its segments, inventory context, failures, and Telegram delivery."
              summary={`${data.nightlyReviews.length} recent`}
            >
              {data.nightlyReviews.length ? (
                <Grid minColumnWidth="20rem">
                  {data.nightlyReviews.map((item) => (
                    <Card as="article" key={item.id}>
                      <Stack gap={2}>
                        <Cluster gap={2} justify="space-between">
                          <Heading level={3} size="sm">
                            {item.scraperRunId !== null ? (
                              <Link
                                to={`/admin/acquisition/runs/${item.scraperRunId}`}
                              >
                                {item.reviewDate ?? formatDate(item.createdAt)}
                              </Link>
                            ) : (
                              item.reviewDate ?? formatDate(item.createdAt)
                            )}
                          </Heading>
                          <StatusBadge
                            tone={REVIEW_TONES[item.status] ?? "neutral"}
                          >
                            {item.status.replaceAll("_", " ")}
                          </StatusBadge>
                        </Cluster>
                        {summaryLines(item.summary).map((line) => (
                          <Text size="sm" key={line}>
                            {line}
                          </Text>
                        ))}
                        <Text size="sm" tone="muted">
                          Telegram delivery:{" "}
                          {item.telegramDeliveryState.replaceAll("_", " ")}
                        </Text>
                        {summaryFailures(item.summary).map((failure, index) => (
                          <Text
                            size="sm"
                            tone="danger"
                            key={failure.kind ?? index}
                          >
                            {(failure.kind ?? "failure").replaceAll("_", " ")}
                            {failure.message ? `: ${failure.message}` : ""}
                          </Text>
                        ))}
                        {item.telegramDeliveryError ? (
                          <Text size="sm" tone="danger">
                            {item.telegramDeliveryError}
                          </Text>
                        ) : null}
                        {item.reconcilable ? (
                          <Text size="sm" tone="warning">
                            Delivery is unknown and needs reconciling before
                            this review can be updated.
                          </Text>
                        ) : null}
                      </Stack>
                    </Card>
                  ))}
                </Grid>
              ) : (
                <EmptyState
                  title="No Nightly Reviews yet"
                  description="A review is written for each nightly run and preserves its evidence permanently."
                />
              )}
            </DisclosureSection>
          </Stack>
        </div>
      </TerminalWorkspace>
    </Page>
  );
}
