import { useState } from "react";
import { Link, useLoaderData, useRevalidator } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input, Textarea } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import type { TerminalDestination } from "../../design-system/primitives/terminal";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";

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
  nightlyReviews: NightlyReviewRow[];
  scrapeAnomalies: ScrapeAnomalyRow[];
  sourceRecommendations: RecommendationRow[];
  nicheMappings: NicheRow[];
  scraperConfigurations: ConfigurationRow[];
}

const DESTINATIONS: TerminalDestination[] = [
  { label: "Acquisition review", href: "#acquisition-review", current: true },
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
          Proposed “{item.niche || "none"}” by{" "}
          {item.proposalSource.replaceAll("_", " ")}
        </Text>
        <div>
          <Button
            onClick={() => {
              setOpen(true);
              setNiche(item.niche);
              setReason("");
              setError("");
            }}
          >
            Confirm Niche
          </Button>
        </div>
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
  useRouteTheme("terminal", "jawnix");
  useDocumentTitle("Acquisition");

  const held = data.scrapeAnomalies.filter((item) => item.decidable);
  const pendingRecommendations = data.sourceRecommendations.filter(
    (item) => item.decidable,
  );
  const unconfirmedNiches = data.nicheMappings.filter(
    (item) => !item.confirmed,
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
        label="Acquisition terminal"
        destinations={DESTINATIONS}
      >
        <div id="acquisition-review">
          <Stack gap={6}>
            <Section
              title="Held Scrape Anomalies"
              description="Flagged output waits here until an administrator confirms or denies it. Nothing is committed while it waits."
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
            </Section>

            <Section
              title="Source Recommendations"
              description="Evidence-based proposals. Nothing here changes acquisition until an administrator approves it."
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
            </Section>

            <Section
              title="Niche mappings"
              description="Recommendations stay ineligible until a Niche is confirmed, so unconfirmed mappings block optimization."
            >
              {unconfirmedNiches.length ? (
                <Grid minColumnWidth="18rem">
                  {unconfirmedNiches.map((item) => (
                    <NicheCard item={item} key={item.segment} />
                  ))}
                </Grid>
              ) : (
                <EmptyState
                  title="Every Niche mapping is confirmed"
                  description="Confirmed mappings let Source Recommendations compare a segment against its same-niche peers."
                />
              )}
            </Section>

            <Section
              id="scraper-configuration-versions"
              title="Scraper Configuration versions"
              description="Versions are immutable. Creating, scheduling, running, and rolling back all select a version; none rewrites one."
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
            </Section>

            <Section
              title="Nightly Reviews"
              description="The durable record of each night's run, its segments, inventory context, failures, and Telegram delivery."
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
            </Section>
          </Stack>
        </div>
      </TerminalWorkspace>
    </Page>
  );
}
