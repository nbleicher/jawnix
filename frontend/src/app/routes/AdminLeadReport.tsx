import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import type { DetailItem } from "../../design-system/primitives/detail";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import {
  ControlActionBar,
  formatDate,
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
}: LoaderFunctionArgs): Promise<LeadReportDetail> {
  return api<LeadReportDetail>(`/api/admin/lead-reports/${params.reportId}`);
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
    if (action.name === "correct") {
      const title = (values.title ?? "").trim();
      const state = (values.state ?? "").trim();
      return {
        url: `/api/admin/lead-reports/${report.id}/correct`,
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

function validateOverride(
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
    { term: "Observed", description: formatDate(evidence.observedAt) },
    { term: "Source", description: evidence.source || "—" },
  ];
}

/** The evidence panel shown inside the correct dialog, beside the override
 *  being typed. Rule of the ticket: the operator must be able to read what
 *  they are overriding while they type the thing that overrides it. */
function OverrideFields({
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
                  description: formatDate(correction.createdAt),
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

export function AdminLeadReportRoute() {
  const report = useLoaderData<LeadReportDetail>();
  useDocumentTitle(`Lead Report · ${report.reasonLabel}`);

  const { controls, evidence, distributionEvent, lead } = report;
  // Restoring is a control on the Lead, not a second decision about the
  // report, so it is offered separately and stays available after the report
  // is closed — which is exactly when a suppression usually needs undoing.
  const restoreActions = controls.actions ?? [];

  return (
    <Page
      title={`Lead Report — ${report.reasonLabel}`}
      description="Decide what happens to the Lead. The report and the Distribution Event beside it are evidence and are never rewritten."
      actions={
        <ActionLink href="/app/admin/fulfillment">Back to Fulfillment</ActionLink>
      }
    >
      <Stack gap={6}>
        <Section
          title="Report"
          description="The Customer's own account, kept exactly as they filed it. Nothing on this screen edits it."
        >
          <Card>
            <Stack gap={4}>
              <DetailList
                label="Lead Report"
                items={[
                  { term: "Reason", description: report.reasonLabel },
                  {
                    term: "What the Customer reported",
                    description: report.details ? (
                      /* Read-only text, never a form control: the report is
                         evidence, so there is nothing here to edit. */
                      <Text size="sm">{report.details}</Text>
                    ) : (
                      "No further detail was given."
                    ),
                  },
                  {
                    term: "Reported by",
                    description: (
                      <Link to={routePath(report.customer.href)}>
                        {report.customer.name}
                      </Link>
                    ),
                  },
                  { term: "Reported", description: formatDate(report.createdAt) },
                  {
                    term: "Status",
                    description: (
                      <StatusBadge tone={reportStatusTone(report.status)}>
                        {reportStatusLabel(report.status)}
                      </StatusBadge>
                    ),
                  },
                ]}
              />
              <Text size="sm" tone="muted">
                This Lead Report is immutable evidence. Resolving it records a
                decision beside it and leaves its text unchanged.
              </Text>
            </Stack>
          </Card>
        </Section>

        <Section
          title="What was delivered"
          description="The Distribution Event this report is about."
        >
          <Card>
            <Stack gap={4}>
              <DetailList
                label="Distribution Event"
                items={[
                  {
                    term: "Phone",
                    description: <Mono>{distributionEvent.phone}</Mono>,
                  },
                  {
                    term: "Title",
                    description: distributionEvent.title || "No title",
                  },
                  {
                    term: "State",
                    description: distributionEvent.state || "No state",
                  },
                  {
                    term: "Customer",
                    description: distributionEvent.customerName,
                  },
                  {
                    term: "Agency",
                    description:
                      distributionEvent.agencyName || "Standalone Customer",
                  },
                  {
                    term: "Delivered",
                    description: formatDate(distributionEvent.deliveredAt),
                  },
                  {
                    term: "Batch Request",
                    description: distributionEvent.requestId ? (
                      <Link
                        to={`/admin/fulfillment/requests/${distributionEvent.requestId}`}
                      >
                        {distributionEvent.requestId}
                      </Link>
                    ) : (
                      "Not linked to a Batch Request"
                    ),
                  },
                ]}
              />
              {/* Its own list rather than extra rows above: provenance keys are
                  whatever the listing carried, and merging them in could
                  collide with a term this screen already names. */}
              {Object.keys(distributionEvent.listingProvenance).length ? (
                <DetailList
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
            </Stack>
          </Card>
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
            <Card>
              <DetailList label="Evidence" items={evidenceItems(evidence)} />
            </Card>
          )}
        </Section>

        <Section
          title="Current controls"
          description="What is currently holding, correcting, or suppressing this Lead."
        >
          <Stack gap={4}>
            <Card>
              <Stack gap={4}>
                <Cluster gap={2} justify="space-between">
                  <Heading level={3} size="sm">
                    Eligibility Hold
                  </Heading>
                  <StatusBadge
                    tone={controls.eligibilityHeld ? "warning" : "neutral"}
                  >
                    {controls.eligibilityHeld ? "Held" : "Not held"}
                  </StatusBadge>
                </Cluster>
                <DetailList
                  label="Eligibility Hold"
                  items={[
                    {
                      term: "Reason",
                      description:
                        controls.holdReason || "No Eligibility Hold is in place.",
                    },
                    {
                      term: "Released",
                      description: controls.holdReleasedAt
                        ? formatDate(controls.holdReleasedAt)
                        : "Not released",
                    },
                  ]}
                />
                {/* Verbatim: the release rule is the domain's sentence, and
                    paraphrasing it would soften a rule that has no exception. */}
                <Text size="sm">{controls.holdRelease}</Text>
                <Text size="sm" tone="warning">
                  A Customer cannot release or bypass this Eligibility Hold. It
                  is released only by resolving this Lead Report here.
                </Text>
              </Stack>
            </Card>

            <Card>
              <Stack gap={4}>
                <Cluster gap={2} justify="space-between">
                  <Heading level={3} size="sm">
                    Suppression
                  </Heading>
                  <StatusBadge tone={controls.suppressed ? "warning" : "neutral"}>
                    {controls.suppressed ? "Suppressed" : "Not suppressed"}
                  </StatusBadge>
                </Cluster>
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
              </Stack>
            </Card>

            {lead.correction ? (
              <CorrectionPanel correction={lead.correction} leadId={lead.id} />
            ) : (
              <EmptyState
                title="No Lead Correction is in place"
                description="Correcting this Lead records the corrected title or state beside the evidence it overrides, without changing what was delivered."
              />
            )}
          </Stack>
        </Section>

        <Section
          title="Decision"
          description="Dismissed, Corrected, and Suppressed are three different decisions. Each states what it does before it is taken."
        >
          {report.actions.length ? (
            <Stack gap={3}>
              <ControlActionBar
                actions={report.actions}
                resolve={reportControlRequest(report)}
                validate={validateOverride}
                extras={(action, { values, set }) =>
                  action.requiresOverride ? (
                    <OverrideFields
                      evidence={evidence}
                      values={values}
                      set={set}
                    />
                  ) : null
                }
                settled="This Lead Report has already been decided."
              />
            </Stack>
          ) : (
            <Card>
              <Stack gap={4}>
                <DetailList
                  label="Resolution"
                  items={[
                    {
                      term: "Decision",
                      description: report.resolution?.action ?? "—",
                    },
                    {
                      term: "Reason",
                      description: report.resolution?.note ?? "—",
                    },
                    {
                      term: "Decided by",
                      description: report.resolution?.actorId ?? "—",
                    },
                    {
                      term: "Decided",
                      description: formatDate(
                        report.resolution?.createdAt ?? null,
                      ),
                    },
                  ]}
                />
                <Text size="sm" tone="muted">
                  This Lead Report is closed and cannot be decided again.
                </Text>
              </Stack>
            </Card>
          )}
        </Section>

        {restoreActions.length ? (
          <Section
            title="Restore eligibility"
            description="Undo the Lead Suppression this decision created."
          >
            <Card>
              <Stack gap={4}>
                <Text>{controls.restoreNotice}</Text>
                <ControlActionBar
                  actions={restoreActions}
                  resolve={reportControlRequest(report)}
                  settled="This Lead is not suppressed."
                />
              </Stack>
            </Card>
          </Section>
        ) : null}
      </Stack>
    </Page>
  );
}
