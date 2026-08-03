import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  useLoaderData,
  useRevalidator,
  useSearchParams,
} from "react-router";

import { ActionLink, Button } from "../../design-system/primitives/Button";
import { cx } from "../../design-system/primitives/cx";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Mono,
  Text,
  VisuallyHidden,
} from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { useBilledWallet } from "../billing/CreditWalletContext";
import {
  batchCostCents,
  formatBalanceRefusal,
  formatCents,
  formatLeadRate,
} from "../billing/money";
import type { CreditWallet } from "../billing/wallet";

import { MilestoneGraph, formatMilestoneTime } from "./MilestoneGraph";
import {
  cancelBatchRequest,
  newSubmissionKey,
  submitBatchRequest,
} from "./batchRequests";
import type {
  BatchArtifact,
  BatchRequest,
  BatchRequestReceipt,
  BatchRequestWorkspace,
  RequestLimits,
} from "./batchRequests";

import "./CustomerRequests.css";

/**
 * The guided stages.
 *
 * Quantity, scope, and files each carry their own rule, and a rule that can be
 * broken has to be answerable before the Customer is asked to confirm anything.
 * Review is the only stage with a Submit button.
 */
const STAGES = [
  { key: "quantity", title: "Quantity" },
  { key: "scope", title: "Licensed States" },
  { key: "files", title: "Files" },
  { key: "review", title: "Review" },
] as const;

const MAX_ROWS_PER_FILE = 100_000;

function formatCount(value: number): string {
  return value.toLocaleString();
}

function formatStates(states: string[]): string {
  return states.join(", ");
}

/** How many CSV parts a frozen rows-per-file choice yields. */
export function previewFileCount(
  leadCount: number,
  rowsPerFile: number,
): number {
  if (leadCount < 1 || rowsPerFile < 1) return 0;
  return Math.ceil(leadCount / rowsPerFile);
}

/** Live preview copy matching the submission freeze ("50,000 at 10,000/file = 5 files"). */
export function formatFileCountPreview(
  leadCount: number,
  rowsPerFile: number,
): string {
  const files = previewFileCount(leadCount, rowsPerFile);
  const fileWord = files === 1 ? "file" : "files";
  return `${formatCount(leadCount)} at ${formatCount(rowsPerFile)}/file = ${formatCount(files)} ${fileWord}`;
}

export const ACTIVE_REQUEST_REFRESH_MS = 10_000;

/** Delivered requests and requests with a terminal outcome cannot change on
 * their own. Everything else is still moving through fulfillment. */
export function isRequestSettled(request: BatchRequest): boolean {
  return request.delivered_at !== null || request.milestones.outcome !== null;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

export function formatArtifactExpiry(expiresAt: string, now: number): string {
  const remaining = Date.parse(expiresAt) - now;
  if (remaining <= 0) return "Expired — contact us";
  if (remaining >= DAY_MS) {
    const days = Math.ceil(remaining / DAY_MS);
    return `Expires in ${days} ${days === 1 ? "day" : "days"}`;
  }
  if (remaining >= HOUR_MS) {
    const hours = Math.ceil(remaining / HOUR_MS);
    return `Expires in ${hours} ${hours === 1 ? "hour" : "hours"}`;
  }
  const minutes = Math.max(1, Math.ceil(remaining / MINUTE_MS));
  return `Expires in ${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

// --- The guided flow --------------------------------------------------------

function StageIndicator({ stage }: { stage: number }) {
  return (
    <ol className="request-flow__stages" role="list" aria-label="Request stages">
      {STAGES.map((entry, index) => (
        <li
          key={entry.key}
          className={cx(
            "request-flow__stage",
            index === stage && "request-flow__stage--current",
            index < stage && "request-flow__stage--done",
          )}
          {...(index === stage ? { "aria-current": "step" as const } : {})}
        >
          <Text as="span" size="sm" weight={index === stage ? "semibold" : "normal"}>
            {`${index + 1}. ${entry.title}`}
          </Text>
          <VisuallyHidden>
            {index < stage
              ? "Completed"
              : index === stage
                ? "Current stage"
                : "Not started"}
          </VisuallyHidden>
        </li>
      ))}
    </ol>
  );
}

function Receipt({
  receipt,
  onRestart,
}: {
  receipt: BatchRequestReceipt;
  onRestart: () => void;
}) {
  const headingRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  return (
    <Card className="request-flow__receipt">
      <Stack gap={4}>
        <div ref={headingRef} tabIndex={-1} role="status">
          <Stack gap={2}>
            <Heading level={3} size="lg">
              Request submitted
            </Heading>
            <Text>
              {`We have your request for ${formatCount(receipt.request.lead_count)} leads in ${formatStates(receipt.request.states)}, submitted ${formatMilestoneTime(receipt.request.submitted_at)}.`}
            </Text>
            {receipt.created ? null : (
              <Text size="sm" tone="muted">
                This is the request your earlier submission already created — it
                was not sent twice.
              </Text>
            )}
          </Stack>
        </div>
        <MilestoneGraph
          graph={receipt.request.milestones}
          label="Progress for the request you just submitted"
        />
        <Cluster>
          <ActionLink href={receipt.request.receipt_href} variant="primary">
            View this Batch Request
          </ActionLink>
          <Button onClick={onRestart}>Request another Batch</Button>
        </Cluster>
      </Stack>
    </Card>
  );
}

function RequestFlow({
  limits,
  billing,
  onSubmitted,
}: {
  limits: RequestLimits;
  billing: CreditWallet | null;
  onSubmitted: () => void;
}) {
  const [stage, setStage] = useState(0);
  const [submissionKey, setSubmissionKey] = useState(newSubmissionKey);
  const [quantity, setQuantity] = useState("");
  const [wholeScope, setWholeScope] = useState(true);
  const [chosenStates, setChosenStates] = useState<string[]>([]);
  const [oneFile, setOneFile] = useState(true);
  const [rowsPerFile, setRowsPerFile] = useState("");
  const [quantityError, setQuantityError] = useState("");
  const [scopeError, setScopeError] = useState("");
  const [filesError, setFilesError] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<BatchRequestReceipt | null>(null);
  const quantityRef = useRef<HTMLInputElement>(null);
  const scopeRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<HTMLInputElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);

  // Selected scope can only ever contain Licensed States, so an unlicensed
  // request is not something the Customer can express and then be told off for.
  const states = wholeScope ? limits.licensed_states : chosenStates;
  const leadCount = /^\d+$/.test(quantity.trim())
    ? Number(quantity.trim())
    : null;
  const splitRows = Number(rowsPerFile.trim());
  const previewRows = oneFile
    ? (leadCount ?? 0)
    : Number.isFinite(splitRows) && splitRows >= 1
      ? splitRows
      : 0;
  const filePreview =
    leadCount != null && leadCount >= 1 && previewRows >= 1
      ? formatFileCountPreview(leadCount, previewRows)
      : null;

  const holdCents =
    billing?.leadRateCentsPerThousand != null && leadCount != null
      ? batchCostCents(leadCount, billing.leadRateCentsPerThousand)
      : null;
  const insufficient =
    holdCents != null
    && billing != null
    && holdCents > billing.availableBalanceCents;

  useEffect(() => {
    if (receipt) return;
    if (stage === 0) quantityRef.current?.focus();
    if (stage === 1) scopeRef.current?.focus();
    if (stage === 2) filesRef.current?.focus();
  }, [stage, receipt]);

  useEffect(() => {
    if (failure) failureRef.current?.focus();
  }, [failure]);

  function validQuantity(): boolean {
    const trimmed = quantity.trim();
    if (!trimmed) {
      setQuantityError("Enter how many leads you want.");
      return false;
    }
    if (!/^\d+$/.test(trimmed)) {
      setQuantityError("Enter a whole number of leads.");
      return false;
    }
    const value = Number(trimmed);
    if (
      value < limits.minimum_lead_count
      || value > limits.maximum_lead_count
    ) {
      setQuantityError(
        `Enter between ${formatCount(limits.minimum_lead_count)} and ${formatCount(limits.maximum_lead_count)} leads.`,
      );
      return false;
    }
    setQuantityError("");
    return true;
  }

  function validScope(): boolean {
    if (states.length === 0) {
      setScopeError("Choose at least one Licensed State.");
      return false;
    }
    setScopeError("");
    return true;
  }

  function validFiles(): boolean {
    if (oneFile) {
      setFilesError("");
      return true;
    }
    const trimmed = rowsPerFile.trim();
    if (!trimmed) {
      setFilesError("Enter how many rows each file should hold.");
      return false;
    }
    if (!/^\d+$/.test(trimmed)) {
      setFilesError("Enter a whole number of rows per file.");
      return false;
    }
    const value = Number(trimmed);
    if (value < 1 || value > MAX_ROWS_PER_FILE) {
      setFilesError(
        `Enter between 1 and ${formatCount(MAX_ROWS_PER_FILE)} rows per file.`,
      );
      return false;
    }
    setFilesError("");
    return true;
  }

  function toggleState(state: string) {
    setScopeError("");
    setChosenStates((current) =>
      current.includes(state)
        ? current.filter((entry) => entry !== state)
        : [...current, state],
    );
  }

  async function send() {
    // The guarded re-entry and the submission key answer the same question at
    // two levels: this one stops a second request leaving the browser, the key
    // stops a second Batch Request existing if one does.
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      setReceipt(
        await submitBatchRequest({
          idempotency_key: submissionKey,
          lead_count: Number(quantity.trim()),
          ...(oneFile
            ? {}
            : { rows_per_file: Number(rowsPerFile.trim()) }),
          state_mode: wholeScope ? "all_saved" : "selected",
          states: wholeScope ? [] : chosenStates,
        }),
      );
      onSubmitted();
    } catch (caught) {
      const message =
        caught instanceof Error
          ? formatBalanceRefusal(caught.message)
          : "We could not submit this request. Please try again.";
      setFailure(message);
    } finally {
      setBusy(false);
    }
  }

  function advance(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (stage === 0) {
      if (validQuantity()) setStage(1);
      return;
    }
    if (stage === 1) {
      if (validScope()) setStage(2);
      return;
    }
    if (stage === 2) {
      if (validFiles()) setStage(3);
      return;
    }
    if (stage === 3) {
      if (insufficient) return;
      void send();
    }
  }

  function restart() {
    setReceipt(null);
    setSubmissionKey(newSubmissionKey());
    setQuantity("");
    setWholeScope(true);
    setChosenStates([]);
    setOneFile(true);
    setRowsPerFile("");
    setQuantityError("");
    setScopeError("");
    setFilesError("");
    setFailure("");
    setStage(0);
  }

  if (receipt) {
    return <Receipt receipt={receipt} onRestart={restart} />;
  }

  return (
    <Card>
      <Stack gap={5}>
        <StageIndicator stage={stage} />
        <form className="request-flow__form" onSubmit={advance} noValidate>
          <Stack gap={5}>
            {failure ? (
              <div
                className="request-flow__failure"
                ref={failureRef}
                role="alert"
                tabIndex={-1}
              >
                <Text tone="danger">{failure}</Text>
              </div>
            ) : null}

            {stage === 0 ? (
              <Field
                label="How many leads do you want?"
                description={`Any exact quantity from ${formatCount(limits.minimum_lead_count)} to ${formatCount(limits.maximum_lead_count)}. Your Batch is delivered whole or not at all.`}
                required
                {...(quantityError ? { error: quantityError } : {})}
              >
                <Input
                  ref={quantityRef}
                  type="number"
                  inputMode="numeric"
                  min={limits.minimum_lead_count}
                  max={limits.maximum_lead_count}
                  step={1}
                  value={quantity}
                  onChange={(event) => {
                    setQuantity(event.currentTarget.value);
                    setQuantityError("");
                  }}
                />
              </Field>
            ) : null}

            {stage === 1 ? (
              <Stack gap={4}>
                <Fieldset
                  legend="Which states should this Batch cover?"
                  description="Only the states saved to your account can be requested."
                  {...(scopeError ? { error: scopeError } : {})}
                >
                  <Stack gap={2}>
                    <label className="request-flow__choice">
                      <input
                        ref={scopeRef}
                        type="radio"
                        name="request-scope"
                        checked={wholeScope}
                        onChange={() => {
                          setWholeScope(true);
                          setScopeError("");
                        }}
                      />
                      <Text as="span" weight="semibold">
                        {`All my Licensed States (${formatStates(limits.licensed_states)})`}
                      </Text>
                    </label>
                    <label className="request-flow__choice">
                      <input
                        type="radio"
                        name="request-scope"
                        checked={!wholeScope}
                        onChange={() => {
                          setWholeScope(false);
                          setScopeError("");
                        }}
                      />
                      <Text as="span" weight="semibold">
                        Choose specific states
                      </Text>
                    </label>
                  </Stack>
                </Fieldset>
                {wholeScope ? null : (
                  <Fieldset legend="States for this Batch">
                    <div className="request-flow__states">
                      {limits.licensed_states.map((state) => (
                        <label key={state} className="request-flow__choice">
                          <input
                            type="checkbox"
                            checked={chosenStates.includes(state)}
                            onChange={() => toggleState(state)}
                          />
                          <Text as="span" weight="semibold">
                            {state}
                          </Text>
                        </label>
                      ))}
                    </div>
                  </Fieldset>
                )}
              </Stack>
            ) : null}

            {stage === 2 ? (
              <Stack gap={4}>
                <Fieldset
                  legend="How should the Batch be split into files?"
                  description="This choice is frozen when you submit. Delivery is one zip either way."
                >
                  <Stack gap={2}>
                    <label className="request-flow__choice">
                      <input
                        ref={filesRef}
                        type="radio"
                        name="request-files"
                        checked={oneFile}
                        onChange={() => {
                          setOneFile(true);
                          setFilesError("");
                        }}
                      />
                      <Text as="span" weight="semibold">
                        One file
                      </Text>
                    </label>
                    <label className="request-flow__choice">
                      <input
                        type="radio"
                        name="request-files"
                        checked={!oneFile}
                        onChange={() => {
                          setOneFile(false);
                          setFilesError("");
                        }}
                      />
                      <Text as="span" weight="semibold">
                        Split by rows per file
                      </Text>
                    </label>
                  </Stack>
                </Fieldset>
                {oneFile ? null : (
                  <Field
                    label="Rows per file"
                    description={`Any whole number from 1 to ${formatCount(MAX_ROWS_PER_FILE)}. Remainder rows land in the last file.`}
                    required
                    {...(filesError ? { error: filesError } : {})}
                  >
                    <Input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={MAX_ROWS_PER_FILE}
                      step={1}
                      value={rowsPerFile}
                      onChange={(event) => {
                        setRowsPerFile(event.currentTarget.value);
                        setFilesError("");
                      }}
                    />
                  </Field>
                )}
                {filePreview ? (
                  <Text
                    size="sm"
                    tone="muted"
                    role="status"
                    aria-live="polite"
                  >
                    {filePreview}
                  </Text>
                ) : null}
              </Stack>
            ) : null}

            {stage === 3 ? (
              <Stack gap={3}>
                <Heading level={3}>Review your request</Heading>
                <dl className="request-flow__review">
                  <div>
                    <dt>
                      <LabelText>Quantity</LabelText>
                    </dt>
                    <dd>{`${formatCount(Number(quantity.trim()))} leads`}</dd>
                  </div>
                  <div>
                    <dt>
                      <LabelText>Licensed States</LabelText>
                    </dt>
                    <dd>{formatStates(states)}</dd>
                  </div>
                  <div>
                    <dt>
                      <LabelText>Files</LabelText>
                    </dt>
                    <dd>
                      {oneFile
                        ? "One file"
                        : formatFileCountPreview(
                            Number(quantity.trim()),
                            Number(rowsPerFile.trim()),
                          )}
                    </dd>
                  </div>
                  {billing && holdCents != null && billing.leadRateCentsPerThousand != null ? (
                    <>
                      <div>
                        <dt>
                          <LabelText>Lead Rate</LabelText>
                        </dt>
                        <dd>
                          {formatLeadRate(billing.leadRateCentsPerThousand)}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          <LabelText>Cost</LabelText>
                        </dt>
                        <dd>
                          {`${formatCount(Number(quantity.trim()))} × ${formatLeadRate(billing.leadRateCentsPerThousand)} = ${formatCents(holdCents)}`}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          <LabelText>Batch Hold</LabelText>
                        </dt>
                        <dd>
                          {`${formatCents(holdCents)} reserved from your Credit Wallet on submit`}
                        </dd>
                      </div>
                      <div>
                        <dt>
                          <LabelText>Available balance</LabelText>
                        </dt>
                        <dd>{formatCents(billing.availableBalanceCents)}</dd>
                      </div>
                    </>
                  ) : null}
                </dl>
                {insufficient ? (
                  <div className="request-flow__failure" role="alert">
                    <Text tone="danger">
                      {`The Credit Wallet does not have enough available balance for this Batch Hold. Required ${formatCents(holdCents!)}; available ${formatCents(billing!.availableBalanceCents)}. Buy credits before submitting.`}
                    </Text>
                  </div>
                ) : (
                  <Text size="sm" tone="muted">
                    Submitting sends this to Jawnix for review. You can cancel it
                    while it is still waiting.
                    {billing
                      ? " Submitting places a Batch Hold for the full cost against your Credit Wallet."
                      : ""}
                  </Text>
                )}
              </Stack>
            ) : null}

            <Cluster>
              {stage > 0 ? (
                <Button onClick={() => setStage(stage - 1)}>Back</Button>
              ) : null}
              <Button
                type="submit"
                variant="primary"
                busy={busy}
                busyLabel="Submitting…"
                disabled={stage === 3 && insufficient}
              >
                {stage === 3 ? "Submit request" : "Continue"}
              </Button>
            </Cluster>
          </Stack>
        </form>
      </Stack>
    </Card>
  );
}

// --- Submitted requests -----------------------------------------------------

function ArtifactCard({ artifact }: { artifact: BatchArtifact | null }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!artifact?.available) return;
    const interval = window.setInterval(() => setNow(Date.now()), MINUTE_MS);
    return () => window.clearInterval(interval);
  }, [artifact?.available]);

  const expiresAt = artifact?.expires_at
    ? Date.parse(artifact.expires_at)
    : Number.NaN;
  const expired = !Number.isFinite(expiresAt) || expiresAt <= now;
  const live = Boolean(
    artifact?.available && artifact.download_href && !expired,
  );
  const status = expired
    ? "Expired — contact us"
    : live && artifact?.expires_at
      ? formatArtifactExpiry(artifact.expires_at, now)
      : "Unavailable — contact us";
  const parts = artifact?.parts ?? [];

  return (
    <section
      className={cx(
        "request-artifact",
        !live && "request-artifact--unavailable",
      )}
      aria-labelledby="batch-artifact-title"
    >
      <Stack gap={4}>
        <Cluster justify="space-between" align="flex-start">
          <Stack gap={1}>
            <Heading level={3} id="batch-artifact-title">
              Batch Artifact
            </Heading>
            <Text size="sm" tone="muted">
              The exact zip created for this Batch Request, with one CSV part
              per file choice.
            </Text>
          </Stack>
          <Text
            size="sm"
            weight="semibold"
            tone={live ? "success" : "warning"}
          >
            {artifact?.expires_at ? (
              <time dateTime={artifact.expires_at}>{status}</time>
            ) : (
              status
            )}
          </Text>
        </Cluster>

        {artifact ? (
          <Stack gap={4}>
            <dl className="request-artifact__metadata">
              <div>
                <dt>
                  <LabelText>Filename</LabelText>
                </dt>
                <dd>
                  <Mono>{artifact.filename}</Mono>
                </dd>
              </div>
              <div>
                <dt>
                  <LabelText>Rows</LabelText>
                </dt>
                <dd>{formatCount(artifact.row_count)}</dd>
              </div>
            </dl>
            {parts.length ? (
              <div>
                <LabelText id="batch-artifact-parts-label">Parts</LabelText>
                <ul
                  className="request-artifact__parts"
                  aria-labelledby="batch-artifact-parts-label"
                >
                  {parts.map((part) => (
                    <li key={part.filename} className="request-artifact__part">
                      <Mono>{part.filename}</Mono>
                      <Text as="span" size="sm" tone="muted">
                        {`${formatCount(part.row_count)} ${part.row_count === 1 ? "row" : "rows"}`}
                      </Text>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </Stack>
        ) : null}

        {live && artifact?.download_href ? (
          <ActionLink href={artifact.download_href} variant="primary">
            Download zip
          </ActionLink>
        ) : (
          <Text size="sm">
            Batch files are retained for 30 days. Contact Jawnix to have this
            exact file regenerated.
          </Text>
        )}
      </Stack>
    </section>
  );
}

function RequestDetail({
  request,
  onCanceled,
}: {
  request: BatchRequest;
  onCanceled: (updated: BatchRequest) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const { pause, outcome } = request.milestones;

  async function confirmCancel() {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      onCanceled(await cancelBatchRequest(request.id));
      setConfirming(false);
    } catch (caught) {
      setConfirming(false);
      setFailure(
        caught instanceof Error
          ? caught.message
          : "We could not cancel this request.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div id={`request-${request.id}`}>
      <Stack gap={4}>
        <Cluster justify="space-between" align="flex-start">
          <Stack gap={1}>
            <Heading level={2}>
              {`${formatCount(request.lead_count)} lead Batch Request`}
            </Heading>
            <Text size="sm" tone="muted">
              {`${formatStates(request.states)} · submitted ${formatMilestoneTime(request.submitted_at)}`}
            </Text>
          </Stack>
          <StatusBadge tone={request.status.tone}>
            {request.status.label}
          </StatusBadge>
        </Cluster>

        <Text>{request.status.description}</Text>

        <MilestoneGraph
          graph={request.milestones}
          label={`Progress for the ${formatCount(request.lead_count)} lead request submitted ${formatMilestoneTime(request.submitted_at)}`}
        />

        {pause ? (
          <div className="request-card__note request-card__note--pause">
            <Stack gap={1}>
              <Text weight="semibold">{pause.label}</Text>
              <Text size="sm">{pause.description}</Text>
            </Stack>
          </div>
        ) : null}

        {outcome ? (
          <div
            className={cx(
              "request-card__note",
              `request-card__note--${outcome.tone}`,
            )}
          >
            <Stack gap={1}>
              <Text weight="semibold">
                {outcome.occurred_at
                  ? `${outcome.label} · ${formatMilestoneTime(outcome.occurred_at)}`
                  : outcome.label}
              </Text>
              <Text size="sm">{outcome.description}</Text>
            </Stack>
          </div>
        ) : null}

        {request.delivered_at ? (
          <ArtifactCard artifact={request.artifact} />
        ) : null}

        {failure ? (
          <Text tone="danger" role="alert">
            {failure}
          </Text>
        ) : null}

        {request.next_action || request.can_cancel ? (
          <Cluster>
            {request.next_action ? (
              <ActionLink href={request.next_action.href} variant="secondary">
                {request.next_action.label}
              </ActionLink>
            ) : null}
            {/* Offered only while the domain still allows withdrawal, so the
                button is never a promise the backend will refuse. */}
            {request.can_cancel ? (
              <Button variant="danger" onClick={() => setConfirming(true)}>
                Cancel request
              </Button>
            ) : null}
          </Cluster>
        ) : null}
      </Stack>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void confirmCancel()}
        title="Cancel this Batch Request?"
        consequence={`Cancelling withdraws your request for ${formatCount(request.lead_count)} leads. It cannot be undone, but you can submit a new request at any time.`}
        confirmLabel="Cancel request"
        cancelLabel="Keep request"
        busy={busy}
      />
    </div>
  );
}

function RequestSummary({ request }: { request: BatchRequest }) {
  return (
    <li>
      <Card padding={4}>
        <Cluster justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Heading level={3}>{`${formatCount(request.lead_count)} leads`}</Heading>
            <Text size="sm" tone="muted">
              {`${formatStates(request.states)} · submitted ${formatMilestoneTime(request.submitted_at)}`}
            </Text>
            <Text size="sm">{request.status.description}</Text>
            <ActionLink href={request.receipt_href} variant="secondary">
              View request
              <VisuallyHidden>
                {` for ${formatCount(request.lead_count)} leads submitted ${formatMilestoneTime(request.submitted_at)}`}
              </VisuallyHidden>
            </ActionLink>
          </Stack>
          <StatusBadge tone={request.status.tone}>
            {request.status.label}
          </StatusBadge>
        </Cluster>
      </Card>
    </li>
  );
}

// --- The route --------------------------------------------------------------

export function CustomerRequestsRoute() {
  const workspace = useLoaderData<BatchRequestWorkspace>();
  const revalidator = useRevalidator();
  const billing = useBilledWallet();
  const [searchParams] = useSearchParams();
  const requestId = searchParams.get("request");

  // A cancellation answers with the request's new timeline, so the screen shows
  // it at once instead of waiting for the list to be fetched again.
  const [canceled, setCanceled] = useState<Record<string, BatchRequest>>({});
  useEffect(() => {
    setCanceled((current) => (Object.keys(current).length ? {} : current));
  }, [workspace]);

  const requests = workspace.requests.map(
    (request) => canceled[request.id] ?? request,
  );

  const selectedRequest = requestId
    ? requests.find((request) => request.id === requestId)
    : undefined;
  const hasActiveRequest = requests.some(
    (request) => !isRequestSettled(request),
  );
  useDocumentTitle(selectedRequest ? "Batch Request" : "Requests");

  useEffect(() => {
    if (!hasActiveRequest) return;
    const interval = window.setInterval(() => {
      if (revalidator.state === "idle") void revalidator.revalidate();
    }, ACTIVE_REQUEST_REFRESH_MS);
    return () => window.clearInterval(interval);
  }, [hasActiveRequest, revalidator]);

  if (requestId) {
    return (
      <Page
        title="Batch Request"
        description="Follow this request from submission through delivery."
        actions={
          selectedRequest ? (
            <ActionLink href="/app/requests">All Requests</ActionLink>
          ) : undefined
        }
      >
        {selectedRequest ? (
          <Card>
            <RequestDetail
              request={selectedRequest}
              onCanceled={(updated) =>
                setCanceled((current) => ({
                  ...current,
                  [updated.id]: updated,
                }))
              }
            />
          </Card>
        ) : (
          <EmptyState
            title="Batch Request not found"
            description="This request is not in your request history."
            action={<ActionLink href="/app/requests">All Requests</ActionLink>}
          />
        )}
      </Page>
    );
  }

  return (
    <Page
      title="Requests"
      density="data"
      description="Ask for an exact Batch, then follow it from Submitted to Delivered."
    >
      <Section
        title="Request a Batch"
        description="Ask for an exact Batch — quantity, Licensed States, file split — then review."
      >
        {workspace.blocker ? (
          <EmptyState
            title={workspace.blocker.label}
            description={workspace.blocker.description}
            action={
              <ActionLink href={workspace.blocker.action.href} variant="primary">
                {workspace.blocker.action.label}
              </ActionLink>
            }
          />
        ) : (
          <RequestFlow
            limits={workspace.limits}
            billing={billing}
            onSubmitted={() => void revalidator.revalidate()}
          />
        )}
      </Section>

      <Section
        title="Your Batch Requests"
        description="Every request you have submitted, newest first."
      >
        {requests.length ? (
          <Stack as="ul" gap={4} className="request-list">
            {requests.map((request) => (
              <RequestSummary key={request.id} request={request} />
            ))}
          </Stack>
        ) : (
          <EmptyState
            title="No Batch Requests yet"
            description="Nothing has been requested. Your first Batch Request will appear here with its full timeline."
          />
        )}
      </Section>
    </Page>
  );
}
