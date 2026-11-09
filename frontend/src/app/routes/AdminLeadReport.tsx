import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import type { DetailItem } from "../../design-system/primitives/detail";
import { FactList } from "../../design-system/primitives/facts";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Card,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { formatDateTime } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import {
  ControlActionBar,
  leadControlRequest,
  reportStatusLabel,
  reportStatusTone,
  routePath,
} from "./AdminFulfillment";
import type {
  ControlAction,
  ControlResolver,
  LeadCorrection,
  LeadEvidence,
  LeadReportDetail,
} from "./AdminFulfillment";
import {
  ActivityTimeline,
  loadEntityActivity,
} from "./AdminActivity";
import type { ActivityPage } from "./AdminActivity";
import "./AdminLeadReport.css";

/**
 * One Lead Report and the eligibility controls beside it (#58).
 *
 * The report itself is immutable: it is the Customer's account of what they
 * received, and the Distribution Event beside it is what was actually
 * delivered. Neither is rewritten by any decision on this screen. What the
 * decisions change is the *Lead* — whether it stays eligible, whether a Lead
 * Correction overrides the listing it came from, whether it is suppressed.
 */

export async function adminLeadReportLoader({
  params,
}: LoaderFunctionArgs): Promise<LeadReportDetail & { activityTimeline: ActivityPage }> {
  const [report, activityTimeline] = await Promise.all([
    api<LeadReportDetail>(`/api/admin/lead-reports/${params.reportId}`),
    loadEntityActivity("lead_report", params.reportId),
  ]);
  return { ...report, activityTimeline };
}

/** Listing provenance arrives keyed by its storage names; this makes them
 *  readable without pretending to know which keys exist. */
function provenanceTerm(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Report decisions record the operator's words as `note`; the Lead-level
 *  controls take a `reason`. The two families keep their own endpoints. */
function reportControlRequest(report: LeadReportDetail): ControlResolver {
  return (action, reason, values) => {
    if (action.name === "restore" || action.name === "remove-correction") {
      return leadControlRequest(report.lead.id)(action, reason, values);
    }
    // Keyed on the domain flag rather than the name, so the one decision that
    // proposes a replacement value stays defined by the server.
    if (action.requiresOverride) {
      const title = (values.title ?? "").trim();
      const state = (values.state ?? "").trim();
      return {
        url: `/api/admin/lead-reports/${report.id}/${action.name}`,
        method: "POST",
        body: {
          note: reason,
          // Only what was typed is sent: an empty field is not an assertion
          // that the value is empty.
          ...(title ? { title } : {}),
          ...(state ? { state } : {}),
        },
      };
    }
    return {
      url: `/api/admin/lead-reports/${report.id}/${action.name}`,
      method: "POST",
      body: { note: reason },
    };
  };
}

export function validateOverride(
  action: ControlAction,
  values: Record<string, string>,
): string {
  if (!action.requiresOverride) return "";
  if ((values.title ?? "").trim() || (values.state ?? "").trim()) return "";
  return "Enter a corrected title, a corrected state, or both.";
}

function evidenceItems(evidence: LeadEvidence): DetailItem[] {
  return [
    { term: "Evidence", description: evidence.label },
    { term: "Title", description: evidence.title || "No title recorded" },
    { term: "State", description: evidence.state || "No state recorded" },
    { term: "Observed", description: formatDateTime(evidence.observedAt) },
    { term: "Source", description: evidence.source || "—" },
  ];
}

/** The evidence panel shown inside the correct dialog, beside the override
 *  being typed. Rule of the ticket: the operator must be able to read what
 *  they are overriding while they type the thing that overrides it. */
export function OverrideFields({
  evidence,
  values,
  set,
}: {
  evidence: LeadEvidence;
  values: Record<string, string>;
  set: (name: string, value: string) => void;
}) {
  return (
    <Grid minColumnWidth="15rem" gap={4}>
      <Card padding={4}>
        <Stack gap={3}>
          <Heading level={3} size="sm">
            What this overrides
          </Heading>
          {evidence.kind === "none" ? (
            <Text size="sm" tone="muted">
              There is no listing underneath this Lead. A correction here records
              the corrected values on their own.
            </Text>
          ) : (
            <DetailList
              label="Evidence being overridden"
              items={evidenceItems(evidence)}
            />
          )}
        </Stack>
      </Card>
      <Stack gap={3}>
        {/* Nothing is pre-filled. A defaulted override is one Enter away from
            recording the evidence back over itself as a correction. */}
        <Field
          label="Corrected title"
          description="Leave blank to keep the title as it is."
        >
          <Input
            name="title"
            value={values.title ?? ""}
            onChange={(event) => set("title", event.target.value)}
          />
        </Field>
        <Field
          label="Corrected state"
          description="Leave blank to keep the state as it is."
        >
          <Input
            name="state"
            value={values.state ?? ""}
            onChange={(event) => set("state", event.target.value)}
          />
        </Field>
      </Stack>
    </Grid>
  );
}

function CorrectionPanel({
  correction,
  leadId,
}: {
  correction: LeadCorrection;
  leadId: number;
}) {
  return (
    <Stack gap={4}>
      <Grid minColumnWidth="15rem">
        <Card as="article" padding={4}>
          <Stack gap={3}>
            <Heading level={3} size="sm">
              Lead Correction
            </Heading>
            <DetailList
              label="Lead Correction"
              items={[
                { term: "Title", description: correction.title || "Unchanged" },
                { term: "State", description: correction.state || "Unchanged" },
                { term: "Reason", description: correction.reason },
                {
                  term: "Recorded",
                  description: formatDateTime(correction.createdAt),
                },
              ]}
            />
          </Stack>
        </Card>
        {/* Side by side, because a correction only means something next to the
            thing it overrode. */}
        <Card as="article" padding={4}>
          <Stack gap={3}>
            <Heading level={3} size="sm">
              What it overrides
            </Heading>
            <DetailList
              label="Overridden evidence"
              items={[
                { term: "Evidence", description: correction.basedOnLabel },
                {
                  term: "Title",
                  description: correction.basedOnTitle || "No title recorded",
                },
                {
                  term: "State",
                  description: correction.basedOnState || "No state recorded",
                },
              ]}
            />
          </Stack>
        </Card>
      </Grid>
      <ControlActionBar
        actions={correction.actions}
        resolve={leadControlRequest(leadId)}
        settled="This Lead Correction cannot be removed from here."
      />
    </Stack>
  );
}

const RESOLUTION_WORDS: Record<string, string> = {
  dismiss: "Dismissed",
  dismissed: "Dismissed",
  correct: "Corrected",
  corrected: "Corrected",
  suppress: "Suppressed",
  suppressed: "Suppressed",
};

const HOLD_REASONS: Record<string, string> = {
  invalid_phone: "Invalid phone",
  do_not_contact: "Do not contact",
};

/** Who decided, in words: Activity keeps identities, not names. */
function deciderName(actor: string): string {
  if (!actor) return "—";
  if (actor.startsWith("telegram:")) return "Operator in Telegram";
  if (actor.startsWith("system")) return "System";
  return "Operator";
}

function batchLink(event: LeadReportDetail["distributionEvent"]) {
  if (!event.requestId) return "Not linked to a batch";
  return (
    <Link to={`/admin/batches/${event.requestNumber ?? event.requestId}`}>
      {event.requestNumber ? `Batch #${event.requestNumber}` : "Open the batch"}
    </Link>
  );
}

/**
 * One Lead Report (P3): the decision first, then the Customer's own words,
 * what was delivered and in which Batch, and the evidence underneath. The
 * controls holding, correcting, or suppressing the Lead sit on the right.
 */
export function AdminLeadReportRoute() {
  const report = useLoaderData<
    LeadReportDetail & { activityTimeline: ActivityPage }
  >();
  useDocumentTitle(`Lead report · ${report.reasonLabel}`);

  const { controls, evidence, distributionEvent, lead } = report;
  // Restoring is a control on the Lead, not a second decision about the
  // report, so it is offered separately and stays available after the report
  // is closed — which is exactly when a suppression usually needs undoing.
  const restoreActions = controls.actions ?? [];
  const where = distributionEvent.requestNumber
    ? ` from Batch #${distributionEvent.requestNumber}`
    : "";

  return (
    <Page
      title={`Lead report · ${report.reasonLabel}`}
      width="data"
      density="data"
      description={`${report.customer.name} reported ${distributionEvent.phone}${where} on ${formatDateTime(report.createdAt)}.`}
      actions={
        <StatusBadge tone={report.status === "open" ? "danger" : reportStatusTone(report.status)}>
          {reportStatusLabel(report.status)}
        </StatusBadge>
      }
    >
      <RecordColumns
        main={
          <>
            <Section
              title="Decision"
              description="Dismissed, Corrected, and Suppressed are three different decisions. Each states what it does before it is taken."
            >
              {report.actions.length ? (
                <ControlActionBar
                  actions={report.actions}
                  resolve={reportControlRequest(report)}
                  validate={validateOverride}
                  extras={(action, { values, set }) =>
                    action.requiresOverride ? (
                      <OverrideFields evidence={evidence} values={values} set={set} />
                    ) : null
                  }
                  settled="This Lead Report has already been decided."
                />
              ) : (
                <Stack gap={3}>
                  <FactList
                    label="Resolution"
                    items={[
                      {
                        term: "Decision",
                        description: report.resolution
                          ? RESOLUTION_WORDS[report.resolution.action] ?? report.resolution.action
                          : "—",
                      },
                      { term: "Reason", description: report.resolution?.note ?? "—" },
                      {
                        term: "Decided by",
                        description: deciderName(report.resolution?.actorId ?? ""),
                      },
                      {
                        term: "Decided",
                        description: formatDateTime(report.resolution?.createdAt ?? null),
                      },
                    ]}
                  />
                  <Text size="sm" tone="muted">
                    This Lead Report is closed and cannot be decided again.
                  </Text>
                </Stack>
              )}
            </Section>

            <Section title="Report">
              <FactList
                label="Lead Report"
                items={[
                  { term: "Reason", description: report.reasonLabel },
                  {
                    term: "What the Customer reported",
                    description: report.details ? (
                      /* Read-only text, never a form control: the report is
                         evidence, so there is nothing here to edit. */
                      <Text as="span" size="sm">{report.details}</Text>
                    ) : (
                      "No further detail was given."
                    ),
                  },
                  {
                    term: "Reported by",
                    description: (
                      <Link to={routePath(report.customer.href)}>{report.customer.name}</Link>
                    ),
                  },
                  { term: "Reported", description: formatDateTime(report.createdAt) },
                ]}
              />
              <Text size="sm" tone="muted">
                This Lead Report is immutable evidence. Resolving it records a
                decision beside it and leaves its text unchanged.
              </Text>
            </Section>

            <Section title="What was delivered">
              <FactList
                label="Distribution Event"
                items={[
                  { term: "Phone", description: <Mono>{distributionEvent.phone}</Mono> },
                  { term: "Title", description: distributionEvent.title || "No title" },
                  { term: "State", description: distributionEvent.state || "No state" },
                  { term: "Customer", description: distributionEvent.customerName },
                  {
                    term: "Agency",
                    description: distributionEvent.agencyName || "Standalone Customer",
                  },
                  { term: "Delivered", description: formatDateTime(distributionEvent.deliveredAt) },
                  { term: "Batch", description: batchLink(distributionEvent) },
                ]}
              />
              {/* Its own list rather than extra rows above: provenance keys are
                  whatever the listing carried, and merging them in could
                  collide with a term this screen already names. */}
              {Object.keys(distributionEvent.listingProvenance).length ? (
                <FactList
                  label="Listing provenance"
                  items={Object.entries(distributionEvent.listingProvenance).map(
                    ([key, value]) => ({
                      term: provenanceTerm(key),
                      description: String(value),
                    }),
                  )}
                />
              ) : null}
              <Text size="sm" tone="muted">
                This is what was delivered. Dismissing, correcting, or
                suppressing the Lead never rewrites this Distribution Event.
              </Text>
            </Section>

            <Section
              title="Evidence"
              description="What the Lead looked like underneath the report — the record a Lead Correction would override."
            >
              {evidence.kind === "none" ? (
                <EmptyState
                  title="There is nothing underneath this Lead"
                  description="No listing or earlier snapshot backs this Lead, so there is no record for a Lead Correction to override. A correction would stand on its own."
                />
              ) : (
                <FactList label="Evidence" items={evidenceItems(evidence)} />
              )}
            </Section>

            {restoreActions.length ? (
              <Section
                title="Restore eligibility"
                description="Undo the Lead Suppression this decision created."
              >
                <Stack gap={3}>
                  <Text size="sm">{controls.restoreNotice}</Text>
                  <ControlActionBar
                    actions={restoreActions}
                    resolve={reportControlRequest(report)}
                    settled="This Lead is not suppressed."
                  />
                </Stack>
              </Section>
            ) : null}

            <Section
              title="Activity"
              description="Consequential decisions on this Lead Report, most recent first."
            >
              <ActivityTimeline
                activity={report.activityTimeline}
                emptyDescription="No administrator has decided this Lead Report yet."
              />
            </Section>
          </>
        }
        aside={
          <section className="jx-report-controls" aria-label="Current controls">
            <Panel
              title="Eligibility Hold"
              action={
                <StatusBadge tone={controls.eligibilityHeld ? "danger" : "neutral"}>
                  {controls.eligibilityHeld ? "Held" : "Not held"}
                </StatusBadge>
              }
            >
              <FactList
                label="Eligibility Hold"
                items={[
                  {
                    term: "Reason",
                    description: controls.holdReason
                      ? HOLD_REASONS[controls.holdReason] ?? controls.holdReason
                      : "No Eligibility Hold is in place.",
                  },
                  {
                    term: "Released",
                    description: controls.holdReleasedAt
                      ? formatDateTime(controls.holdReleasedAt)
                      : "Not released",
                  },
                ]}
              />
              {/* Only where a hold actually exists. Stating the release rule
                  beside a "Not held" badge would assert a hold and deny one in
                  the same breath. Not every report creates one: Wrong Business
                  is report-only. */}
              {controls.holdId ? (
                <>
                  {/* Verbatim: the release rule is the domain's sentence, and
                      paraphrasing it would soften a rule that has no
                      exception. */}
                  <Text size="sm">{controls.holdRelease}</Text>
                  <Text size="sm" tone="warning">
                    A Customer cannot release or bypass this Eligibility Hold. It
                    is released only by an administrator resolving this Lead
                    Report.
                  </Text>
                </>
              ) : (
                <Text size="sm" tone="muted">
                  This Lead Report never created an Eligibility Hold. Only an
                  Invalid Phone or Do Not Contact disposition does.
                </Text>
              )}
            </Panel>

            <Panel
              title="Suppression"
              action={
                <StatusBadge tone={controls.suppressed ? "danger" : "neutral"}>
                  {controls.suppressed ? "Suppressed" : "Not suppressed"}
                </StatusBadge>
              }
            >
              <Text size="sm">
                {controls.suppressed
                  ? controls.suppressionReason ||
                    "This Lead is suppressed from future distribution."
                  : "This Lead is eligible for future Batch Requests."}
              </Text>
              {controls.suppressed ? (
                <Text size="sm" tone="muted">
                  {controls.restoreNotice}
                </Text>
              ) : null}
            </Panel>

            <Panel title="Lead correction">
              {lead.correction ? (
                <CorrectionPanel correction={lead.correction} leadId={lead.id} />
              ) : (
                <Text size="sm" tone="muted">
                  No Lead Correction is in place. Correcting this Lead records the
                  corrected title or state beside the evidence it overrides,
                  without changing what was delivered.
                </Text>
              )}
            </Panel>
            <ActionLink href="/app/admin/today">Back to Today</ActionLink>
          </section>
        }
      />
    </Page>
  );
}
