import { useState } from "react";
import { useLoaderData } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState, ErrorState } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input, Select, Textarea } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { formatRequestRef } from "./batchRequests";
import { CustomerExclusionListsSection } from "./CustomerExclusionLists";

import "./CustomerFeedback.css";

/**
 * Guided Customer Feedback (#53).
 *
 * Three properties this screen exists to hold, all of which are easy to lose
 * to an ordinary-looking refactor:
 *
 * **One failure.** A phone that is malformed, was never delivered, or belongs
 * to another Customer must be indistinguishable. The backend already answers
 * all three identically; this screen must not add a difference of its own, so
 * it renders one message from one state and never branches on the cause.
 *
 * **Consequences before submission.** Invalid Phone and Do Not Contact file a
 * Lead Report *and* place an Eligibility Hold; Wrong Business files a report
 * with no hold. The wording is served by `/api/me/feedback/dispositions`,
 * derived from the rule that materializes the controls, so this screen cannot
 * misstate an irreversible effect.
 *
 * **Speed.** A valid disposition is three interactions from an empty field:
 * enter the phone, choose the disposition, submit. Quality Rating and notes are
 * genuinely optional and never gate that path.
 */

export interface DispositionOption {
  disposition: string;
  label: string;
  description: string;
  requiresNote: boolean;
  createsReport: boolean;
  createsHold: boolean;
  consequence: string;
}

export interface DispositionGroup {
  group: string;
  label: string;
  options: DispositionOption[];
}

export interface QualityRatingOption {
  value: string;
  label: string;
  description: string;
}

export interface FeedbackCatalog {
  groups: DispositionGroup[];
  qualityRating: {
    optional: boolean;
    description: string;
    options: QualityRatingOption[];
  };
}

export interface DeliveredLead {
  distributionEventId: number;
  businessName: string;
  phone: string;
  deliveredAt: string;
  batchId: string | null;
  currentDisposition: string | null;
}

export interface DispositionTransition {
  id: string;
  distributionEventId: number;
  disposition: string;
  note: string;
  actorUserId: string;
  previousTransitionId: string | null;
  createdAt: string;
}

/**
 * What `POST /api/me/feedback` actually answers.
 *
 * The transition is nested, and the response also echoes the controls the
 * submission materialized. That echo is what lets the receipt confirm the
 * consequence really happened rather than restating what was promised.
 * `tests/test_feedback_privacy.py` pins this shape.
 */
export interface FeedbackReceipt {
  distributionEventId: number;
  currentDisposition: string;
  transition: DispositionTransition;
  qualityRating: { id: string; kind: string; note: string } | null;
  reportId: string | null;
  eligibilityHoldId: string | null;
}

/**
 * The single failure for every unsuccessful lookup.
 *
 * Deliberately a constant, not a message derived from the response: deriving
 * it is how a difference between "not a phone number", "never delivered", and
 * "belongs to someone else" gets reintroduced.
 */
const LOOKUP_FAILURE =
  "No delivered Lead was found for that phone number. Check the number and try again.";

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep the generic message when the body is not JSON.
    }
    const error = new Error(detail || "request failed") as Error & {
      status?: number;
    };
    error.status = response.status;
    throw error;
  }
  return (await response.json()) as T;
}

function formatPhone(value: string): string {
  const digits = value.replace(/\D/g, "");
  if (digits.length !== 10) return value;
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

/** Ledger timestamps on record surfaces are ISO-UTC ("2026-07-20 15:00 UTC")
 *  so a delivery reads identically in every timezone. */
function formatLedgerTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())} ${pad(parsed.getUTCHours())}:${pad(parsed.getUTCMinutes())} UTC`;
}

export async function feedbackLoader(): Promise<FeedbackCatalog> {
  const response = await fetch("/api/me/feedback/dispositions", {
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("Feedback options could not be loaded.");
  }
  return (await response.json()) as FeedbackCatalog;
}

function labelFor(catalog: FeedbackCatalog, disposition: string): string {
  for (const group of catalog.groups) {
    const found = group.options.find(
      (option) => option.disposition === disposition,
    );
    if (found) return found.label;
  }
  return disposition;
}

/** The selected-state witness. Rendered, not pseudo-element text content, so
 *  the glyph never leaks into the accessible name or copy-paste. */
function CheckWitness() {
  return (
    <svg
      className="customer-feedback__option-check"
      width="12"
      height="12"
      viewBox="0 0 12 12"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M2 6.5 4.8 9.3 10 2.7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="square"
      />
    </svg>
  );
}

export function CustomerFeedbackRoute() {
  const catalog = useLoaderData<FeedbackCatalog>();
  useDocumentTitle("Feedback");

  const [phone, setPhone] = useState("");
  const [lead, setLead] = useState<DeliveredLead | null>(null);
  const [lookupError, setLookupError] = useState("");
  const [lookingUp, setLookingUp] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<DeliveredLead[] | null>(
    null,
  );
  const [searchError, setSearchError] = useState("");
  const [searching, setSearching] = useState(false);

  const [selected, setSelected] = useState<DispositionOption | null>(null);
  const [note, setNote] = useState("");
  const [rating, setRating] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<FeedbackReceipt | null>(null);
  const [history, setHistory] = useState<DispositionTransition[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [reportReason, setReportReason] = useState("other");
  const [reportDetails, setReportDetails] = useState("");
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState("");
  const [reportSaved, setReportSaved] = useState("");

  // Dispositions are mutually exclusive across every group, so one
  // radiogroup spans the fieldsets; quality ratings below stay true toggles.
  const dispositionOrder = catalog.groups.flatMap((group) => group.options);
  const dispositionIndex = new Map(
    dispositionOrder.map((option, index) => [option.disposition, index]),
  );

  function chooseDisposition(option: DispositionOption) {
    setSelected(option);
    setSubmitError("");
    setReceipt(null);
  }

  function onDispositionKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const { key } = event;
    if (
      key !== "ArrowRight"
      && key !== "ArrowDown"
      && key !== "ArrowLeft"
      && key !== "ArrowUp"
    ) {
      return;
    }
    const radios = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'),
    );
    const current = radios.indexOf(document.activeElement as HTMLElement);
    if (current < 0) return;
    event.preventDefault();
    const delta = key === "ArrowRight" || key === "ArrowDown" ? 1 : radios.length - 1;
    const next = radios[(current + delta) % radios.length];
    if (!next) return;
    next.focus();
    next.click();
  }

  function resetEntry() {
    setSelected(null);
    setNote("");
    setRating(null);
    setSubmitError("");
    setReceipt(null);
    setReportReason("other");
    setReportDetails("");
    setReportError("");
    setReportSaved("");
  }

  async function loadHistory(eventId: number) {
    setHistoryError("");
    try {
      const response = await fetch(
        `/api/me/distributions/${eventId}/dispositions`,
        { credentials: "same-origin" },
      );
      if (!response.ok) throw new Error("history request failed");
      setHistory((await response.json()) as DispositionTransition[]);
    } catch {
      setHistory([]);
      setHistoryError(
        "Feedback history could not be loaded. This does not mean the Lead has no feedback.",
      );
    }
  }

  async function chooseLead(found: DeliveredLead) {
    setLookupError("");
    setSearchError("");
    setLead(found);
    resetEntry();
    setHistory([]);
    setHistoryError("");
    await loadHistory(found.distributionEventId);
  }

  async function lookup(event: React.FormEvent) {
    event.preventDefault();
    setLookingUp(true);
    setLookupError("");
    setSearchResults(null);
    setLead(null);
    resetEntry();
    setHistory([]);
    setHistoryError("");
    try {
      const found = await post<DeliveredLead>("/api/me/feedback/lookup", {
        phone,
      });
      await chooseLead(found);
    } catch {
      // Every cause produces this one message. Not a branch — a constant.
      setLookupError(LOOKUP_FAILURE);
    } finally {
      setLookingUp(false);
    }
  }

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const query = searchQuery.trim();
    setSearchError("");
    setSearchResults(null);
    if (query.length < 2) {
      setSearchError("Enter at least two characters to search.");
      return;
    }
    setSearching(true);
    setLead(null);
    resetEntry();
    setHistory([]);
    setHistoryError("");
    try {
      setSearchResults(
        await post<DeliveredLead[]>("/api/me/feedback/search", { query }),
      );
    } catch {
      setSearchError(
        "Your delivered batches could not be searched. Try again.",
      );
    } finally {
      setSearching(false);
    }
  }

  async function submit() {
    if (!lead || !selected) return;
    if (selected.requiresNote && !note.trim()) {
      setSubmitError("A note is required for this answer.");
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const created = await post<FeedbackReceipt>("/api/me/feedback", {
        distribution_event_id: lead.distributionEventId,
        disposition: selected.disposition,
        note: note.trim(),
        ...(rating ? { quality_rating: rating } : {}),
      });
      setReceipt(created);
      await loadHistory(lead.distributionEventId);
      setSelected(null);
      setNote("");
      setRating(null);
    } catch (caught) {
      const status =
        caught instanceof Error && "status" in caught
          ? Number((caught as Error & { status?: number }).status)
          : 0;
      // Surface booking-gate and similar 409s; keep generic copy for unknowns
      // so a server detail never becomes a Customer-facing oracle.
      setSubmitError(
        status === 409
        && caught instanceof Error
        && caught.message
        && caught.message !== "request failed"
          ? caught.message
          : "Your feedback could not be recorded. Nothing was saved — try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function submitReport() {
    if (!lead) return;
    if (reportReason === "other" && !reportDetails.trim()) {
      setReportError("A note is required when the reason is Other.");
      return;
    }
    setReportBusy(true);
    setReportError("");
    setReportSaved("");
    try {
      await post(`/api/me/distributions/${lead.distributionEventId}/reports`, {
        reason: reportReason,
        details: reportDetails.trim(),
      });
      setReportSaved("Lead Report filed. Noah reviews every report.");
      setReportDetails("");
    } catch (caught) {
      setReportError(
        caught instanceof Error && caught.message && caught.message !== "request failed"
          ? caught.message
          : "The Lead Report could not be filed. Nothing was saved — try again.",
      );
    } finally {
      setReportBusy(false);
    }
  }

  return (
    <Page
      title="Feedback"
      description="Tell us what happened with a Lead we delivered."
    >
      <Stack gap={6}>
        <Section
          title="Find the Lead"
          description="Look up the exact phone number or search your delivered batches."
        >
          <Stack gap={5}>
            <Stack gap={3}>
              <Heading level={3} size="sm">
                Use a phone number
              </Heading>
              <form onSubmit={(event) => void lookup(event)}>
                <Stack gap={3}>
                  <Field
                    label="Delivered phone number"
                    required
                    {...(lookupError ? { error: lookupError } : {})}
                  >
                    <Input
                      name="phone"
                      type="tel"
                      inputMode="tel"
                      autoComplete="tel"
                      value={phone}
                      onChange={(event) => setPhone(event.target.value)}
                    />
                  </Field>
                  <div>
                    <Button
                      type="submit"
                      busy={lookingUp}
                      busyLabel="Looking up…"
                    >
                      Look up
                    </Button>
                  </div>
                </Stack>
              </form>
            </Stack>

            <Stack gap={3}>
              <Heading level={3} size="sm">
                Search your delivered batches
              </Heading>
              <form onSubmit={(event) => void search(event)}>
                <Stack gap={3}>
                  <Field
                    label="Business name or phone"
                    description="Enter at least two characters from the business name or phone number."
                    {...(searchError ? { error: searchError } : {})}
                  >
                    <Input
                      name="search"
                      type="search"
                      value={searchQuery}
                      onChange={(event) => setSearchQuery(event.target.value)}
                    />
                  </Field>
                  <div>
                    <Button
                      type="submit"
                      busy={searching}
                      busyLabel="Searching…"
                    >
                      Search
                    </Button>
                  </div>
                </Stack>
              </form>

              {searchResults ? (
                <div role="region" aria-label="Search results">
                  {searchResults.length ? (
                    <ol className="customer-feedback__search-results">
                      {searchResults.map((result) => (
                        <li key={result.distributionEventId}>
                          <button
                            type="button"
                            className="customer-feedback__search-result"
                            onClick={() => void chooseLead(result)}
                          >
                            <span className="customer-feedback__option-label">
                              {result.businessName}
                            </span>
                            <span className="customer-feedback__option-description">
                              <Mono>
                                {formatPhone(result.phone)}
                                {result.batchId
                                  ? ` · ${formatRequestRef(result.batchId)}`
                                  : ""}
                              </Mono>
                            </span>
                          </button>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <EmptyState
                      title="No matching Leads"
                      description="Try another part of the business name or phone number."
                    />
                  )}
                </div>
              ) : null}
            </Stack>
          </Stack>
        </Section>

        {lead ? (
          <>
            <Section
              title="Confirm the Lead"
              description="Check this is the right business before you answer."
            >
              <Card>
                <DetailList
                  label="Delivered Lead"
                  items={[
                    { term: "Business", description: lead.businessName },
                    { term: "Phone", description: formatPhone(lead.phone) },
                    {
                      term: "Delivered",
                      description: (
                        <Mono>{formatLedgerTime(lead.deliveredAt)}</Mono>
                      ),
                    },
                    {
                      term: "Batch",
                      description: lead.batchId ? (
                        <Mono>{formatRequestRef(lead.batchId)}</Mono>
                      ) : (
                        "Not part of a batch"
                      ),
                    },
                  ]}
                />
              </Card>
            </Section>

            <Section
              title="What happened?"
              description="Choose the closest answer. You can add another answer later; nothing is overwritten."
            >
              <Stack
                gap={5}
                role="radiogroup"
                aria-label="What happened?"
                onKeyDown={onDispositionKeyDown}
              >
                  {catalog.groups.map((group) => (
                    <Fieldset legend={group.label} key={group.group}>
                      <div className="customer-feedback__options">
                        {group.options.map((option) => {
                          const isSelected =
                            selected?.disposition === option.disposition;
                          return (
                            <button
                              type="button"
                              key={option.disposition}
                              className="customer-feedback__option"
                              role="radio"
                              aria-checked={isSelected}
                              tabIndex={
                                selected
                                  ? isSelected
                                    ? 0
                                    : -1
                                  : dispositionIndex.get(option.disposition) === 0
                                    ? 0
                                    : -1
                              }
                              onClick={() => chooseDisposition(option)}
                            >
                              <span className="customer-feedback__option-label">
                                {isSelected ? <CheckWitness /> : null}
                                {option.label}
                              </span>
                              <span className="customer-feedback__option-description">
                                {option.description}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </Fieldset>
                  ))}
                </Stack>
            </Section>

            {selected ? (
              <Section
                title={`Your answer: ${selected.label}`}
                description="Review this before you submit."
              >
                <Stack gap={4}>
                  {/* Stated before submission, and served by the same rule
                      that materializes the controls, so it cannot misdescribe
                      an irreversible effect. */}
                  {selected.consequence ? (
                    <Card>
                      <Stack gap={2}>
                        <Cluster gap={2}>
                          <StatusBadge tone="warning">
                            {selected.createsHold
                              ? "Files a report and holds the Lead"
                              : "Files a report"}
                          </StatusBadge>
                        </Cluster>
                        <Text>{selected.consequence}</Text>
                      </Stack>
                    </Card>
                  ) : null}

                  {selected.requiresNote ? (
                    <Field
                      label="Note"
                      description="Tell us what happened."
                      required
                      {...(submitError && !note.trim()
                        ? { error: submitError }
                        : {})}
                    >
                      <Textarea
                        name="note"
                        value={note}
                        onChange={(event) => setNote(event.target.value)}
                      />
                    </Field>
                  ) : null}

                  <Fieldset
                    legend="Lead quality (optional)"
                    description={catalog.qualityRating.description}
                  >
                    <div className="customer-feedback__options">
                      {catalog.qualityRating.options.map((option) => (
                        <button
                          type="button"
                          key={option.value}
                          className="customer-feedback__option"
                          aria-pressed={rating === option.value}
                          onClick={() =>
                            setRating(
                              rating === option.value ? null : option.value,
                            )
                          }
                        >
                          <span className="customer-feedback__option-label">
                            {rating === option.value ? <CheckWitness /> : null}
                            {option.label}
                          </span>
                          <span className="customer-feedback__option-description">
                            {option.description}
                          </span>
                        </button>
                      ))}
                    </div>
                  </Fieldset>

                  {submitError && !(selected.requiresNote && !note.trim()) ? (
                    <ErrorState description={submitError} />
                  ) : null}

                  <div>
                    <Button
                      variant="primary"
                      onClick={() => void submit()}
                      busy={submitting}
                      busyLabel="Recording…"
                    >
                      Submit feedback
                    </Button>
                  </div>
                </Stack>
              </Section>
            ) : null}

            {receipt ? (
              <Section title="Recorded">
                <Card>
                  <Stack gap={2}>
                    <Cluster gap={2}>
                      <StatusBadge tone="success">Recorded</StatusBadge>
                    </Cluster>
                    <Text>
                      {labelFor(catalog, receipt.transition.disposition)}{" "}
                      recorded for {lead.businessName} on{" "}
                      {formatDateTime(receipt.transition.createdAt)}.
                    </Text>
                    {/* Confirmed from what the server actually did, so the
                        receipt cannot claim a control that was not created. */}
                    {receipt.reportId ? (
                      <Text size="sm">
                        {receipt.eligibilityHoldId
                          ? "A Lead Report was filed and an Eligibility Hold now withdraws this Lead from future batches."
                          : "A Lead Report was filed. This Lead stays eligible for future batches."}
                      </Text>
                    ) : null}
                    {receipt.qualityRating ? (
                      <Text size="sm">
                        Quality rated{" "}
                        {receipt.qualityRating.kind === "good"
                          ? "Good"
                          : "Poor"}
                        .
                      </Text>
                    ) : null}
                    <Text size="sm" tone="muted">
                      Reference <Mono>{receipt.transition.id}</Mono>
                    </Text>
                  </Stack>
                </Card>
              </Section>
            ) : null}

            <Section
              title="File a Lead Report"
              description="Use this when a quality rating is not enough — Noah reviews every report. Data and compliance dispositions above already file a report automatically. Upheld reports are credited to your Credit Wallet."
            >
              <Card>
                <Stack gap={4}>
                  <Field label="Reason" required>
                    <Select
                      value={reportReason}
                      onChange={(event) => {
                        setReportSaved("");
                        setReportError("");
                        setReportReason(event.currentTarget.value);
                      }}
                    >
                      <option value="invalid_phone">Invalid phone</option>
                      <option value="wrong_business_or_title">
                        Wrong business or title
                      </option>
                      <option value="wrong_state">Wrong state</option>
                      <option value="duplicate">Duplicate</option>
                      <option value="do_not_contact_or_legal">
                        Do not contact or legal
                      </option>
                      <option value="other">Other</option>
                    </Select>
                  </Field>
                  <Field
                    label="Details"
                    description={
                      reportReason === "other"
                        ? "Required for Other."
                        : "Optional context for Noah."
                    }
                    required={reportReason === "other"}
                  >
                    <Textarea
                      value={reportDetails}
                      onChange={(event) => {
                        setReportSaved("");
                        setReportError("");
                        setReportDetails(event.currentTarget.value);
                      }}
                    />
                  </Field>
                  {reportError ? (
                    <ErrorState description={reportError} />
                  ) : null}
                  {reportSaved ? (
                    <Text tone="success" role="status">
                      {reportSaved}
                    </Text>
                  ) : null}
                  <div>
                    <Button
                      variant="primary"
                      onClick={() => void submitReport()}
                      busy={reportBusy}
                      busyLabel="Filing…"
                    >
                      File Lead Report
                    </Button>
                  </div>
                </Stack>
              </Card>
            </Section>

            <Section
              title="Feedback history"
              description="Every answer you have given for this Lead, oldest first. Answers are added, never replaced."
            >
              {historyError ? (
                <ErrorState
                  title="Feedback history unavailable"
                  description={historyError}
                  onRetry={() => void loadHistory(lead.distributionEventId)}
                />
              ) : history.length ? (
                <ol className="customer-feedback__history">
                  {history.map((item) => (
                    <li key={item.id}>
                      <Card>
                        <Stack gap={1}>
                          <Heading level={3} size="sm">
                            {labelFor(catalog, item.disposition)}
                          </Heading>
                          <Text size="sm" tone="muted">
                            {formatDateTime(item.createdAt)}
                          </Text>
                          {item.note ? <Text size="sm">{item.note}</Text> : null}
                        </Stack>
                      </Card>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState
                  title="No feedback yet"
                  description="Your first answer for this Lead will appear here."
                />
              )}
            </Section>
          </>
        ) : null}

        <CustomerExclusionListsSection />
      </Stack>
    </Page>
  );
}
