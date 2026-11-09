import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  useHref,
  useLoaderData,
  useNavigate,
  useSearchParams,
} from "react-router";

import { ApplicationLink } from "../../design-system/primitives/ApplicationLink";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import type { Fact } from "../../design-system/primitives/facts";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input } from "../../design-system/primitives/form";
import { Page } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { Text } from "../../design-system/primitives/typography";
import { useBilledWallet, useCreditWallet } from "../billing/CreditWalletContext";
import { batchCostCents, formatBalanceRefusal, formatCents } from "../billing/money";
import { usePurchaseReturnPath } from "../billing/purchaseReturn";
import { startCreditPurchase } from "../billing/wallet";
import type { CreditWallet } from "../billing/wallet";
import { formatCount, formatRatePerThousand, formatStates } from "../customer/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { newSubmissionKey, submitBatchRequest } from "./batchRequests";
import type { BatchRequestWorkspace, RequestLimits } from "./batchRequests";

import "./CustomerNewRequest.css";

/**
 * New request (CP2): one page with quantity, appointed states, and file
 * splitting under Options, beside a live summary of the cost, the balance,
 * and any shortfall. Buying the shortfall returns here with the form filled
 * in. Supply stays hidden: nothing here says what is available.
 */

export const MAX_ROWS_PER_FILE = 100_000;

/** Where a submitted request lands: its batch page says what happens next. */
export interface SubmittedState {
  submitted: true;
  /** False when the same form had already created the batch. */
  created: boolean;
}

// --- The unfinished request a Stripe return carries back --------------------

/**
 * Buy credits sends the Customer to Stripe Checkout, which returns them here
 * with these query keys (rebuilt server-side from an allow-list in
 * `checkout_return.py`). `states` absent means every appointed state;
 * `rows_per_file` absent means one file.
 */
export interface RequestDraft {
  quantity: string;
  states: string[] | null;
  rowsPerFile: string | null;
}

export const REQUEST_DRAFT_KEYS = ["quantity", "states", "rows_per_file"] as const;

export function readRequestDraft(params: URLSearchParams): RequestDraft | null {
  const quantity = params.get("quantity");
  if (quantity === null || !/^\d{1,6}$/.test(quantity)) return null;
  const states = params.get("states");
  const rowsPerFile = params.get("rows_per_file");
  return {
    quantity,
    states: states
      ? states.split(",").map((state) => state.trim().toUpperCase()).filter(Boolean)
      : null,
    rowsPerFile: rowsPerFile && /^\d{1,6}$/.test(rowsPerFile) ? rowsPerFile : null,
  };
}

export function requestDraftQuery(draft: RequestDraft): string {
  const query = new URLSearchParams({ quantity: draft.quantity });
  if (draft.states) query.set("states", draft.states.join(","));
  if (draft.rowsPerFile) query.set("rows_per_file", draft.rowsPerFile);
  return query.toString().replace(/%2C/gi, ",");
}

/** A whole number typed with or without thousands separators, or null. */
export function parseCount(value: string): number | null {
  const compact = value.replace(/[\s,]/g, "");
  return /^\d{1,7}$/.test(compact) ? Number(compact) : null;
}

/** "10 files of 10,000 rows", "4 files of up to 15,000 rows", or "One file". */
export function filesPreview(leadCount: number, rowsPerFile: number): string {
  if (rowsPerFile >= leadCount) return "One file";
  const files = Math.ceil(leadCount / rowsPerFile);
  const upTo = leadCount % rowsPerFile === 0 ? "" : "up to ";
  return `${formatCount(files)} files of ${upTo}${formatCount(rowsPerFile)} rows`;
}

interface FormDefaults {
  quantity: string;
  states: string[];
  split: boolean;
  rowsPerFile: string;
}

/**
 * Where the form starts: a draft carried back from Stripe, or else every
 * appointed state and the file split of the Customer's latest batch.
 */
export function formDefaults(
  draft: RequestDraft | null,
  workspace: Pick<BatchRequestWorkspace, "limits" | "requests">,
): FormDefaults {
  const appointed = workspace.limits.licensed_states;
  if (draft) {
    const states = (draft.states ?? appointed).filter((state) => appointed.includes(state));
    return {
      quantity: draft.quantity,
      states: states.length ? states : appointed,
      split: draft.rowsPerFile !== null,
      rowsPerFile: draft.rowsPerFile ?? "",
    };
  }
  const latest = workspace.requests[0];
  const split = latest !== undefined && latest.rows_per_file < latest.lead_count;
  return {
    quantity: "",
    states: appointed,
    split,
    rowsPerFile: split ? String(latest.rows_per_file) : "",
  };
}

// --- The form ---------------------------------------------------------------

function RequestForm({
  workspace,
  draft,
}: {
  workspace: BatchRequestWorkspace;
  draft: RequestDraft | null;
}) {
  const limits: RequestLimits = workspace.limits;
  const billing: CreditWallet | null = useBilledWallet();
  const { refresh: refreshWallet } = useCreditWallet();
  const navigate = useNavigate();
  const formId = useId();
  const [defaults] = useState(() => formDefaults(draft, workspace));
  const [submissionKey] = useState(newSubmissionKey);
  const [quantity, setQuantity] = useState(defaults.quantity);
  const [chosen, setChosen] = useState<string[]>(defaults.states);
  const [split, setSplit] = useState(defaults.split);
  const [rowsPerFile, setRowsPerFile] = useState(defaults.rowsPerFile);
  const [optionsOpen, setOptionsOpen] = useState(false);
  const [quantityError, setQuantityError] = useState("");
  const [statesError, setStatesError] = useState("");
  const [rowsError, setRowsError] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [buying, setBuying] = useState(false);
  const quantityRef = useRef<HTMLInputElement>(null);
  const rowsRef = useRef<HTMLInputElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);

  // The chosen states, in the order the account lists them.
  const states = limits.licensed_states.filter((state) => chosen.includes(state));
  const leadCount = parseCount(quantity);
  // The quantity once it is one the backend accepts; the summary waits for it.
  const validCount =
    leadCount !== null
    && leadCount >= limits.minimum_lead_count
    && leadCount <= limits.maximum_lead_count
      ? leadCount
      : null;
  const rows = split ? parseCount(rowsPerFile) : null;
  const rate = billing?.leadRateCentsPerThousand ?? null;
  const costCents = rate !== null && validCount !== null ? batchCostCents(validCount, rate) : null;
  const shortCents =
    billing && costCents !== null
      ? Math.max(0, costCents - billing.availableBalanceCents)
      : 0;
  // Credits are bought in whole dollars.
  const buyDollars = Math.ceil(shortCents / 100);

  // While a request is being filled in, Buy credits returns here with it.
  const draftQuery =
    leadCount !== null && leadCount >= 1 && leadCount <= limits.maximum_lead_count
      ? requestDraftQuery({
          quantity: String(leadCount),
          states: states.length === limits.licensed_states.length ? null : states,
          rowsPerFile: split && rows !== null && rows >= 1 ? String(rows) : null,
        })
      : null;
  const draftHref = useHref({
    pathname: "/new-request",
    search: draftQuery ? `?${draftQuery}` : "",
  });
  usePurchaseReturnPath(draftQuery ? draftHref : null);

  useEffect(() => {
    if (failure) failureRef.current?.focus();
  }, [failure]);

  function validate(): boolean {
    let firstInvalid: HTMLElement | null = null;
    if (leadCount === null) {
      setQuantityError(
        quantity.trim() ? "Enter a whole number of leads." : "Enter how many leads you want.",
      );
      firstInvalid = quantityRef.current;
    } else if (validCount === null) {
      setQuantityError(
        `Enter between ${formatCount(limits.minimum_lead_count)} and ${formatCount(limits.maximum_lead_count)} leads.`,
      );
      firstInvalid = quantityRef.current;
    } else {
      setQuantityError("");
    }
    if (states.length === 0) {
      setStatesError("Choose at least one appointed state.");
    } else {
      setStatesError("");
    }
    if (split && (rows === null || rows < 1 || rows > MAX_ROWS_PER_FILE)) {
      setRowsError(`Enter between 1 and ${formatCount(MAX_ROWS_PER_FILE)} rows per file.`);
      setOptionsOpen(true);
      firstInvalid ??= rowsRef.current;
    } else {
      setRowsError("");
    }
    // After a render, so a field inside Options has been revealed first.
    const target = firstInvalid;
    window.requestAnimationFrame(() => target?.focus());
    return target === null && states.length > 0;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // The guarded re-entry and the submission key answer the same question at
    // two levels: this one stops a second request leaving the browser, the key
    // stops a second batch existing if one does.
    if (busy || shortCents > 0) return;
    if (!validate() || validCount === null) return;
    setBusy(true);
    setFailure("");
    try {
      const receipt = await submitBatchRequest({
        idempotency_key: submissionKey,
        lead_count: validCount,
        ...(rate !== null ? { expected_lead_rate_cents_per_thousand: rate } : {}),
        ...(split && rows !== null ? { rows_per_file: rows } : {}),
        state_mode: states.length === limits.licensed_states.length ? "all_saved" : "selected",
        states: states.length === limits.licensed_states.length ? [] : states,
      });
      void refreshWallet();
      const state: SubmittedState = { submitted: true, created: receipt.created };
      navigate(`/batches/${receipt.request.number}`, { state });
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? formatBalanceRefusal(caught.message)
          : "We could not submit this request. Please try again.",
      );
      // A refusal is exactly when the displayed balance is most likely to be
      // the stale number that let the request through, so read it again.
      void refreshWallet();
      setBusy(false);
    }
  }

  async function buyShortfall() {
    if (buying || buyDollars < 1) return;
    setBuying(true);
    setFailure("");
    try {
      const started = await startCreditPurchase(buyDollars, draftQuery ? draftHref : undefined);
      await refreshWallet();
      window.location.assign(started.checkoutUrl);
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "The Credit Purchase could not be started. Please try again.",
      );
      setBuying(false);
    }
  }

  function toggle(state: string) {
    setStatesError("");
    setChosen((current) =>
      current.includes(state)
        ? current.filter((entry) => entry !== state)
        : [...current, state],
    );
  }

  const splitRows = split && rows !== null && rows >= 1 ? rows : null;
  const optionsSummary = splitRows
    ? `split into files of ${formatCount(splitRows)} rows`
    : "one file";

  const summary: Fact[] = [
    { term: "Leads", description: validCount !== null ? formatCount(validCount) : "—" },
    { term: "States", description: states.length ? formatStates(states) : "—" },
  ];
  if (billing) {
    summary.push({
      term: "Rate",
      description: rate !== null ? formatRatePerThousand(rate) : "Not set yet",
    });
    summary.push({
      term: "Cost",
      description: <strong>{costCents !== null ? formatCents(costCents) : "—"}</strong>,
    });
    summary.push({
      term: "Your balance",
      description: formatCents(billing.availableBalanceCents),
    });
    if (shortCents > 0) {
      summary.push({
        term: "Short by",
        description: (
          <Text as="span" size="sm" tone="danger">
            {formatCents(shortCents)}
          </Text>
        ),
      });
    } else if (costCents !== null) {
      summary.push({
        term: "Balance after",
        description: formatCents(billing.availableBalanceCents - costCents),
      });
    }
  }

  return (
    <RecordColumns
      main={
        <form
          id={formId}
          className="customer-request"
          onSubmit={(event) => void submit(event)}
          noValidate
        >
          {failure ? (
            <div className="customer-request__failure" ref={failureRef} role="alert" tabIndex={-1}>
              <Text size="sm" tone="danger">
                {failure}
              </Text>
            </div>
          ) : null}

          <Field
            label="How many leads?"
            description={`Any number from ${formatCount(limits.minimum_lead_count)} to ${formatCount(limits.maximum_lead_count)}.`}
            {...(quantityError ? { error: quantityError } : {})}
          >
            <Input
              ref={quantityRef}
              inputMode="numeric"
              autoComplete="off"
              className="customer-request__quantity"
              value={quantity}
              onChange={(event) => {
                setQuantity(event.currentTarget.value);
                setQuantityError("");
              }}
            />
          </Field>

          <Fieldset
            legend="Appointed states"
            {...(statesError ? { error: statesError } : {})}
          >
            <div className="customer-request__states">
              {limits.licensed_states.map((state) => (
                <label key={state} className="customer-request__state">
                  <input
                    type="checkbox"
                    checked={chosen.includes(state)}
                    onChange={() => toggle(state)}
                  />
                  <span>{limits.state_names[state] ?? state}</span>
                </label>
              ))}
              <ApplicationLink href="/app/account" className="customer-request__edit">
                Edit appointed states
              </ApplicationLink>
            </div>
          </Fieldset>

          <details
            className="customer-request__options"
            open={optionsOpen}
            onToggle={(event) => setOptionsOpen(event.currentTarget.open)}
          >
            <summary>Options · {optionsSummary}</summary>
            <div className="customer-request__options-body">
              <Fieldset legend="Files">
                <label className="customer-request__choice">
                  <input
                    type="radio"
                    name="request-files"
                    checked={!split}
                    onChange={() => {
                      setSplit(false);
                      setRowsError("");
                    }}
                  />
                  <span>One file</span>
                </label>
                <label className="customer-request__choice">
                  <input
                    type="radio"
                    name="request-files"
                    checked={split}
                    onChange={() => setSplit(true)}
                  />
                  <span>Split into several files, in one zip</span>
                </label>
              </Fieldset>
              {split ? (
                <Field
                  label="Rows per file"
                  description={`Any number from 1 to ${formatCount(MAX_ROWS_PER_FILE)}. The last file holds the rest.`}
                  {...(rowsError ? { error: rowsError } : {})}
                >
                  <Input
                    ref={rowsRef}
                    inputMode="numeric"
                    autoComplete="off"
                    value={rowsPerFile}
                    onChange={(event) => {
                      setRowsPerFile(event.currentTarget.value);
                      setRowsError("");
                    }}
                  />
                </Field>
              ) : null}
              {validCount !== null && splitRows ? (
                <Text size="sm" tone="muted">
                  {`${formatCount(validCount)} leads in ${filesPreview(validCount, splitRows).toLowerCase()}.`}
                </Text>
              ) : null}
            </div>
          </details>
        </form>
      }
      aside={
        <Panel title="Summary" className="customer-request__summary">
          <FactList label="Summary" items={summary} />
          {shortCents > 0 ? (
            <div className="customer-request__buy">
              <Button
                variant="primary"
                fullWidth
                busy={buying}
                busyLabel="Opening Stripe…"
                onClick={() => void buyShortfall()}
              >
                {`Buy ${formatCents(buyDollars * 100)} in credits`}
              </Button>
              <Text size="xs" tone="muted">
                Credits come in whole dollars. Stripe brings you back here with
                this request filled in.
              </Text>
            </div>
          ) : null}
          <Button
            type="submit"
            form={formId}
            variant={shortCents > 0 ? "secondary" : "primary"}
            fullWidth
            disabled={shortCents > 0}
            busy={busy}
            busyLabel="Submitting…"
          >
            Submit request
          </Button>
          <Text size="xs" tone="muted">
            Noah reviews each request. We email you when your batch is ready to
            download.
          </Text>
        </Panel>
      }
    />
  );
}

// --- The route --------------------------------------------------------------

export function CustomerNewRequestRoute() {
  const workspace = useLoaderData<BatchRequestWorkspace>();
  const [searchParams, setSearchParams] = useSearchParams();
  useDocumentTitle("New request");

  // An unfinished request carried back from Stripe Checkout fills the form
  // once. Its keys then leave the address, after the purchase notice has
  // taken its own, so a reload starts clean.
  const [draft] = useState(() => readRequestDraft(searchParams));
  const carriesDraft = REQUEST_DRAFT_KEYS.some((key) => searchParams.has(key));
  const returningFromCheckout = searchParams.has("purchase");
  useEffect(() => {
    if (!carriesDraft || returningFromCheckout) return;
    const next = new URLSearchParams(searchParams);
    for (const key of REQUEST_DRAFT_KEYS) next.delete(key);
    setSearchParams(next, { replace: true });
  }, [carriesDraft, returningFromCheckout, searchParams, setSearchParams]);

  return (
    <Page
      title="New request"
      width="data"
      density="data"
      description="Choose how many leads and which appointed states. Cost updates as you type."
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
        <RequestForm workspace={workspace} draft={draft} />
      )}
    </Page>
  );
}
