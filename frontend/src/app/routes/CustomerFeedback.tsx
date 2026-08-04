import { useState } from "react";
import { useLoaderData } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { DetailList } from "../../design-system/primitives/detail";
import { EmptyState, ErrorState } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input, Textarea } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Heading, Text } from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";
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
    const error = new Error("request failed") as Error & { status?: number };
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

  function resetEntry() {
    setSelected(null);
    setNote("");
    setRating(null);
    setSubmitError("");
    setReceipt(null);
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
    } catch {
      setSubmitError(
        "Your feedback could not be recorded. Nothing was saved — try again.",
      );
    } finally {
      setSubmitting(false);
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
                              {formatPhone(result.phone)} · Batch {result.batchId}
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
                      description: formatDateTime(lead.deliveredAt),
                    },
                    {
                      term: "Batch",
                      description: lead.batchId ?? "Not part of a batch",
                    },
                  ]}
                />
              </Card>
            </Section>

            <Section
              title="What happened?"
              description="Choose the closest answer. You can add another answer later; nothing is overwritten."
            >
              <Stack gap={5}>
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
                              aria-pressed={isSelected}
                              onClick={() => {
                                setSelected(option);
                                setSubmitError("");
                                setReceipt(null);
                              }}
                            >
                              <span className="customer-feedback__option-label">
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
                      Reference {receipt.transition.id}
                    </Text>
                  </Stack>
                </Card>
              </Section>
            ) : null}

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
