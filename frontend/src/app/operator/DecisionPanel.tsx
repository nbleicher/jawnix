import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { Field, Input } from "../../design-system/primitives/form";
import { Stack } from "../../design-system/primitives/layout";
import { SidePanel } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import type {
  ConflictDetail,
  ConflictSide,
  LeadReportDetail,
} from "../routes/AdminFulfillment";
import { RegistrationDecisions } from "../routes/RegistrationReview";
import type { PendingRegistration } from "../routes/RegistrationReview";
import { ControlActionBar, conflictEndpoint, postReason } from "./actions";
import type { ControlAction, ControlResolver } from "./actions";
import { BatchPanel } from "./BatchPanel";
import {
  ANOMALY_CONFIRM,
  ANOMALY_DENY,
  EXCLUSION_CONFIRM,
  EXCLUSION_DENY,
  NICHE_CONFIRM,
  NICHE_DENY,
} from "./decisionActions";
import {
  formatAge,
  formatCount,
  formatDateTime,
  formatStates,
} from "./format";

/** One item on Today's Decisions list (GET /api/admin/today). */
export interface Decision {
  key: string;
  kind:
    | "approval"
    | "deliveryFailed"
    | "failedBatch"
    | "inventoryConflict"
    | "leadReport"
    | "failedJob"
    | "scrapeAnomaly"
    | "nicheMapping"
    | "exclusionList"
    | "registration";
  label: string;
  recordId: string;
  subject: string;
  detail: string;
  since: string | null;
  href: string | null;
  batch: { id: string; number: number } | null;
  facts: Record<string, unknown> | null;
}

interface PanelFrame {
  onClose: () => void;
  onChanged: () => void;
  autoFocus?: boolean | undefined;
  returnFocus?: (() => HTMLElement | null) | undefined;
}

const RECORD_ROUTES: Partial<Record<Decision["kind"], (id: string) => string>> = {
  inventoryConflict: (id) => `/api/admin/inventory-conflicts/${id}`,
  leadReport: (id) => `/api/admin/lead-reports/${id}`,
};

/** A record read for a side panel, re-read after each action. */
function useRecord<T>(url: string | null) {
  const [record, setRecord] = useState<{ url: string; value: T } | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (!url) return;
    let current = true;
    setError("");
    api<T>(url)
      .then((value) => {
        if (current) setRecord({ url, value });
      })
      .catch((caught: unknown) => {
        if (current) setError(caught instanceof Error ? caught.message : "This record could not be read.");
      });
    return () => {
      current = false;
    };
  }, [url, version]);
  const reload = useCallback(() => setVersion((value) => value + 1), []);
  return { record: record && record.url === url ? record.value : null, error, reload };
}

function Waiting({ error, onRetry }: { error: string; onRetry: () => void }) {
  if (!error) {
    return (
      <Text size="sm" tone="muted" role="status">
        Reading…
      </Text>
    );
  }
  return (
    <Stack gap={2}>
      <Text size="sm" tone="danger" role="alert">
        {error}
      </Text>
      <div>
        <Button onClick={onRetry}>Try again</Button>
      </div>
    </Stack>
  );
}

function Settled() {
  return (
    <Text size="sm" role="status">
      Decided. This item has left the list.
    </Text>
  );
}

function batchSide(side: ConflictSide): ReactNode {
  if (!side.available) return "No longer available";
  const name = side.number ? `#${side.number}` : "Batch";
  return (
    <>
      <ActionLink href={`/app/admin/batches/${side.number ?? side.id}`}>{name}</ActionLink>
      {` · ${side.customerIdentity ?? ""} · ${formatCount(side.leadCount ?? 0)} leads`}
    </>
  );
}

function ConflictBody({ decision, settled, frame }: { decision: Decision; settled: boolean; frame: PanelFrame }) {
  const { record, error, reload } = useRecord<ConflictDetail>(
    RECORD_ROUTES.inventoryConflict!(decision.recordId),
  );
  if (!record) return <Waiting error={error} onRetry={reload} />;
  const older = record.olderRequest;
  const newer = record.newerRequest;
  return (
    <Stack gap={4}>
      <FactList
        label="Inventory conflict"
        items={[
          { term: "Older batch", description: batchSide(older) },
          { term: "Newer batch", description: batchSide(newer) },
          { term: "Overlapping leads", description: formatCount(record.overlappingLeadCount) },
          {
            term: "Older can get",
            description: older.eligibleCount == null ? "—" : formatCount(older.eligibleCount),
          },
          {
            term: "Newer can get",
            description: newer.eligibleCount == null ? "—" : formatCount(newer.eligibleCount),
          },
        ]}
      />
      {settled ? (
        <Settled />
      ) : (
        <ControlActionBar
          actions={record.actions}
          resolve={postReason(conflictEndpoint(record.id))}
          settled="This conflict has already been decided."
          primaryAction="confirm"
          onConfirmed={() => {
            reload();
            frame.onChanged();
          }}
          refresh={reload}
        />
      )}
      <Text size="xs" tone="muted">
        {record.recurrenceRule}
      </Text>
    </Stack>
  );
}

function ReportBody({ decision }: { decision: Decision }) {
  const { record, error, reload } = useRecord<LeadReportDetail>(
    RECORD_ROUTES.leadReport!(decision.recordId),
  );
  if (!record) return <Waiting error={error} onRetry={reload} />;
  const event = record.distributionEvent;
  return (
    <Stack gap={4}>
      <FactList
        label="Lead report"
        items={[
          { term: "Reason", description: record.reasonLabel },
          { term: "Reported by", description: record.customer.name },
          { term: "Reported", description: formatDateTime(record.createdAt) },
          { term: "What they said", description: record.details || "No detail given" },
          {
            term: "Lead",
            description: `${record.lead.phone} · ${record.lead.title || "No title"} · ${record.lead.state || "—"}`,
          },
          {
            term: "Delivered in",
            description: event.requestNumber ? (
              <ActionLink href={`/app/admin/batches/${event.requestNumber}`}>
                Batch #{event.requestNumber}
              </ActionLink>
            ) : (
              "Not linked to a batch"
            ),
          },
          {
            term: "Eligibility hold",
            description: record.controls.eligibilityHeld ? (
              <StatusBadge tone="danger">Held</StatusBadge>
            ) : (
              "Not held"
            ),
          },
        ]}
      />
      <Text size="sm" tone="muted">
        Dismiss, correct, or suppress on the full page, where the delivered record and its
        evidence sit beside the decision.
      </Text>
    </Stack>
  );
}

function reasonLines(segments: unknown): { key: string; label: string; reasons: string[] }[] {
  return Array.isArray(segments)
    ? (segments as { key: string; label: string; reasons: string[] }[])
    : [];
}

function AnomalyBody({ decision, settled, frame }: { decision: Decision; settled: boolean; frame: PanelFrame }) {
  const facts = decision.facts ?? {};
  const segments = reasonLines(facts.segments);
  return (
    <Stack gap={4}>
      <FactList
        label="Scrape anomaly"
        items={[
          {
            term: "Run",
            description: decision.href ? (
              <ActionLink href={decision.href}>{decision.subject}</ActionLink>
            ) : (
              decision.subject
            ),
          },
          { term: "Held segments", description: formatCount(segments.length) },
          ...segments.slice(0, 8).map((segment) => ({
            term: segment.label,
            description: segment.reasons.join(", ") || "flagged",
          })),
        ]}
      />
      {segments.length > 8 ? (
        <Text size="xs" tone="muted">
          {formatCount(segments.length - 8)} more on the run page.
        </Text>
      ) : null}
      {settled ? (
        <Settled />
      ) : (
        <ControlActionBar
          actions={[ANOMALY_CONFIRM, ANOMALY_DENY]}
          resolve={postReason(
            (action) => `/api/admin/scrape-anomalies/${decision.recordId}/${action.name}`,
          )}
          settled=""
          onConfirmed={frame.onChanged}
          refresh={frame.onChanged}
        />
      )}
    </Stack>
  );
}

function nicheRequest(segment: string): ControlResolver {
  return (action, reason, values) => ({
    url: `/api/admin/source-niches/${encodeURIComponent(segment)}/${action.name}`,
    method: "POST",
    body:
      action.name === "confirm"
        ? { niche: (values.niche ?? "").trim(), reason }
        : { reason },
  });
}

function NicheBody({ decision, settled, frame }: { decision: Decision; settled: boolean; frame: PanelFrame }) {
  const facts = (decision.facts ?? {}) as {
    segment?: string;
    state?: string;
    keyword?: string;
    niche?: string;
    proposalSource?: string;
  };
  return (
    <Stack gap={4}>
      <FactList
        label="Niche mapping"
        items={[
          { term: "Keyword", description: facts.keyword ?? "—" },
          { term: "State", description: facts.state ?? "—" },
          { term: "Proposed niche", description: facts.niche || "None" },
          {
            term: "Proposed by",
            description: (facts.proposalSource ?? "").replaceAll("_", " ") || "—",
          },
        ]}
      />
      {settled ? (
        <Settled />
      ) : (
        <ControlActionBar
          actions={[NICHE_CONFIRM, NICHE_DENY]}
          resolve={nicheRequest(decision.recordId)}
          settled=""
          primaryAction="confirm"
          initialValues={(action) =>
            action.name === "confirm" ? { niche: facts.niche ?? "" } : {}
          }
          validate={(action, values) =>
            action.name === "confirm" && !(values.niche ?? "").trim() ? "A Niche is required." : ""
          }
          extras={(action: ControlAction, { values, set }) =>
            action.name === "confirm" ? (
              <Field label="Niche" required>
                <Input
                  name="niche"
                  value={values.niche ?? ""}
                  onChange={(event) => set("niche", event.target.value)}
                />
              </Field>
            ) : null
          }
          onConfirmed={frame.onChanged}
          refresh={frame.onChanged}
        />
      )}
    </Stack>
  );
}

function ExclusionBody({ decision, settled, frame }: { decision: Decision; settled: boolean; frame: PanelFrame }) {
  const facts = (decision.facts ?? {}) as {
    customerName?: string;
    typeLabel?: string;
    filename?: string;
    acceptedRows?: number;
    invalidRows?: number;
    duplicateRows?: number;
    poolImpact?: number;
    createdAt?: string;
  };
  return (
    <Stack gap={4}>
      <FactList
        label="Exclusion list"
        items={[
          { term: "Customer", description: facts.customerName ?? "—" },
          { term: "Type", description: facts.typeLabel ?? "—" },
          { term: "File", description: facts.filename ?? "—" },
          { term: "Accepted phones", description: formatCount(facts.acceptedRows ?? 0) },
          { term: "In the pool now", description: formatCount(facts.poolImpact ?? 0) },
          { term: "Invalid rows", description: formatCount(facts.invalidRows ?? 0) },
          { term: "Duplicates", description: formatCount(facts.duplicateRows ?? 0) },
          { term: "Uploaded", description: formatDateTime(facts.createdAt) },
        ]}
      />
      {settled ? (
        <Settled />
      ) : (
        <ControlActionBar
          actions={[EXCLUSION_CONFIRM, EXCLUSION_DENY]}
          resolve={postReason(
            (action) => `/api/admin/exclusion-lists/${decision.recordId}/${action.name}`,
          )}
          settled=""
          onConfirmed={frame.onChanged}
          refresh={frame.onChanged}
        />
      )}
    </Stack>
  );
}

function RegistrationBody({ decision, settled }: { decision: Decision; settled: boolean }) {
  const item = decision.facts as unknown as PendingRegistration;
  const matches = item.matches ?? [];
  return (
    <Stack gap={4}>
      <FactList
        label="Registration"
        items={[
          { term: "Name", description: `${item.firstName} ${item.lastName}` },
          { term: "Email", description: item.email },
          { term: "Phone", description: item.phone },
          { term: "States", description: formatStates(item.states ?? []) },
          {
            term: "Archived matches",
            description: matches.length
              ? matches.map((match) => match.slug).join(", ")
              : "None",
          },
        ]}
      />
      {settled ? (
        <Settled />
      ) : (
        <Stack gap={2}>
          <Text size="sm">
            Compare the name and email before granting access to an archived Customer.
          </Text>
          <RegistrationDecisions item={item} />
        </Stack>
      )}
    </Stack>
  );
}

function JobBody({ decision }: { decision: Decision }) {
  const facts = (decision.facts ?? {}) as {
    job?: number;
    kind?: string;
    attempts?: number;
    lastError?: string;
    affected?: { label: string; href: string };
  };
  const items: Fact[] = [
    { term: "Job", description: facts.job ? `#${facts.job}` : "—" },
    { term: "Work", description: (facts.kind ?? "").replaceAll("_", " ") || "—" },
    { term: "Attempts", description: formatCount(facts.attempts ?? 0) },
    { term: "Last error", description: facts.lastError || "None recorded" },
    { term: "Failed", description: formatDateTime(decision.since) },
  ];
  return (
    <Stack gap={4}>
      <FactList label="Failed job" items={items} />
      <Text size="sm" tone="muted">
        Check the affected record’s current state before trying its action again.
      </Text>
    </Stack>
  );
}

export interface DecisionPanelProps extends PanelFrame {
  decision: Decision;
  /** The decision has left the list since the panel opened. */
  settled: boolean;
}

/** A Decision opened beside Today's list, with its real actions where an
 *  endpoint exists and a summary with the full page otherwise. */
export function DecisionPanel({ decision, settled, ...frame }: DecisionPanelProps) {
  if (decision.batch) {
    return (
      <BatchPanel
        reference={decision.batch.number}
        kind={decision.label}
        fallbackTitle={`#${decision.batch.number} · ${decision.subject}`}
        onClose={frame.onClose}
        onChanged={frame.onChanged}
        autoFocus={frame.autoFocus}
        returnFocus={frame.returnFocus}
      />
    );
  }
  const waiting = decision.since ? ` · waiting ${formatAge(decision.since)}` : "";
  const body = (() => {
    switch (decision.kind) {
      case "inventoryConflict":
        return <ConflictBody decision={decision} settled={settled} frame={frame} />;
      case "leadReport":
        return <ReportBody decision={decision} />;
      case "scrapeAnomaly":
        return <AnomalyBody decision={decision} settled={settled} frame={frame} />;
      case "nicheMapping":
        return <NicheBody decision={decision} settled={settled} frame={frame} />;
      case "exclusionList":
        return <ExclusionBody decision={decision} settled={settled} frame={frame} />;
      case "registration":
        return <RegistrationBody decision={decision} settled={settled} />;
      default:
        return <JobBody decision={decision} />;
    }
  })();
  return (
    <SidePanel
      eyebrow={`${decision.label}${waiting}`}
      title={decision.subject}
      onClose={frame.onClose}
      fullHref={decision.href ?? undefined}
      autoFocus={frame.autoFocus}
      returnFocus={frame.returnFocus}
    >
      {body}
    </SidePanel>
  );
}
