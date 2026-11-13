import { useState } from "react";
import type { ReactNode } from "react";
import { useRevalidator } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { Field, Textarea } from "../../design-system/primitives/form";
import { Cluster, Stack } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";

/**
 * Consequential operator actions, each behind a confirmation that states its
 * consequence and collects a reason for Activity.
 *
 * Shared by the Batch page, the side panels on Today and All batches, and the
 * Fulfillment, Inventory Conflict, and Lead Report screens. Every action list
 * comes from the server, so a screen can only offer what the domain accepts.
 */

export interface ControlAction {
  name: string;
  label: string;
  consequence: string;
  destructive: boolean;
  requiresReason: boolean;
  /** True only for a Lead Correction, which needs a typed title or state on top
   *  of the reason. The screen reads this rather than checking for `correct`,
   *  so the override surface follows the domain instead of a name. */
  requiresOverride?: boolean;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The action could not be completed.";
}

/** Open Batch Requests, the ones whose Telegram message can still matter. */
export const RENOTIFY_STATUSES = new Set([
  "pending",
  "approved",
  "waiting_inventory",
  "processing",
  "generated",
  "failed",
]);

export interface ControlRequest {
  url: string;
  method: "POST" | "PUT" | "DELETE";
  body: Record<string, unknown>;
}

/** Builds the call one control action makes. Callers own it because the two
 *  families disagree on the wire: a Lead Report decision records the operator's
 *  words as `note`, while a Lead-level suppression or correction takes a
 *  `reason`. Guessing that from the action name here would hide the split. */
export type ControlResolver = (
  action: ControlAction,
  reason: string,
  values: Record<string, string>,
) => ControlRequest;

export interface ControlExtras {
  values: Record<string, string>;
  set: (name: string, value: string) => void;
}

interface ControlActionBarProps<Result> {
  actions: ControlAction[];
  resolve: ControlResolver;
  /** Rendered when the record has nothing left to decide. */
  settled: string;
  /** Inputs an action needs beyond the reason, rendered above it. A Lead
   *  Correction uses this to sit the evidence next to the override being
   *  typed, so the operator sees what they are overriding while they type. */
  extras?: (action: ControlAction, control: ControlExtras) => ReactNode;
  /** Checked before anything is sent. Returns the message to show, or "". */
  validate?: (action: ControlAction, values: Record<string, string>) => string;
  onConfirmed?: ((result: Result) => void) | undefined;
  refresh?: (() => void) | undefined;
  /** The one commitment action drawn as the ink slab, e.g. "approve". */
  primaryAction?: string;
  /** Values an action's extra inputs start with, e.g. the proposed Niche a
   *  confirmation may correct. Reasons are never pre-filled. */
  initialValues?: (action: ControlAction) => Record<string, string>;
}

/**
 * Renders exactly the control actions the server offered, each behind its own
 * confirmation stating its own consequence.
 *
 * Three decisions on a Lead Report are three buttons, not one control with a
 * mode: Dismissed, Corrected, and Suppressed do different things to the Lead
 * and a chooser would let one be taken while reading another's consequence.
 */
export function ControlActionBar<Result = unknown>({
  actions,
  resolve,
  settled,
  extras,
  validate,
  onConfirmed,
  refresh,
  primaryAction,
  initialValues,
}: ControlActionBarProps<Result>) {
  const [pending, setPending] = useState<ControlAction | null>(null);
  const [reason, setReason] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const revalidator = useRevalidator();

  function open(action: ControlAction) {
    setPending(action);
    // Nothing is pre-filled unless the caller says so: an override must be
    // typed, never defaulted into agreement with the evidence it replaces.
    setReason("");
    setValues(initialValues?.(action) ?? {});
    setError("");
  }

  function close() {
    setPending(null);
    setReason("");
    setValues({});
  }

  async function confirm() {
    if (!pending) return;
    if (pending.requiresReason && !reason.trim()) {
      setError("A reason is required.");
      return;
    }
    const invalid = validate?.(pending, values) ?? "";
    if (invalid) {
      setError(invalid);
      return;
    }
    const request = resolve(pending, reason.trim(), values);
    setBusy(true);
    setError("");
    try {
      const result = await api<Result>(request.url, {
        method: request.method,
        body: JSON.stringify(request.body),
      });
      close();
      if (onConfirmed) onConfirmed(result);
      else revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
      // A 409 means this report was already decided somewhere else. Re-read it
      // so the screen stops offering a decision that has already been taken.
      if (refresh) refresh();
      else revalidator.revalidate();
    } finally {
      setBusy(false);
    }
  }

  // A refused action re-reads the record, which can empty `actions` while the
  // dialog is still open. Unmounting here would destroy the refusal message in
  // the same tick it was written, so the operator would never learn why — the
  // faster the re-read, the less chance they have of reading it. Keep the open
  // dialog mounted; the action buttons still disappear, which is the part that
  // must not survive.
  if (!actions.length && pending === null) {
    return (
      <Text size="sm" tone="muted">
        {settled}
      </Text>
    );
  }

  return (
    <>
      {actions.length ? (
        <Cluster gap={2}>
          {actions.map((action) => (
            <Button
              key={action.name}
              variant={
                action.destructive
                  ? "danger"
                  : action.name === primaryAction
                    ? "primary"
                    : "secondary"
              }
              onClick={() => open(action)}
            >
              {action.label}
            </Button>
          ))}
        </Cluster>
      ) : (
        <Text size="sm" tone="muted">
          {settled}
        </Text>
      )}
      <ConfirmDialog
        open={pending !== null}
        onClose={close}
        onConfirm={() => void confirm()}
        title={pending?.label ?? ""}
        consequence={pending?.consequence ?? ""}
        confirmLabel={pending?.label ?? "Confirm"}
        destructive={pending?.destructive ?? false}
        busy={busy}
      >
        <Stack gap={4}>
          {pending && extras
            ? extras(pending, {
                values,
                set: (name, value) =>
                  setValues((current) => ({ ...current, [name]: value })),
              })
            : null}
          <Field
            label="Reason"
            description="Recorded in Activity so this decision can be explained later."
            required={pending?.requiresReason ?? true}
          >
            <Textarea
              name="reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          {error ? (
            <Text size="sm" tone="danger" role="alert">
              {error}
            </Text>
          ) : null}
        </Stack>
      </ConfirmDialog>
    </>
  );
}

/** Lead-level controls. They change the Lead's eligibility; they never touch
 *  the Lead Report's text or the Distribution Event that was delivered. */
export function leadControlRequest(leadId: number): ControlResolver {
  return (action, reason, values) => {
    if (action.requiresOverride) {
      const title = (values.title ?? "").trim();
      const state = (values.state ?? "").trim();
      return {
        url: `/api/admin/leads/${leadId}/correction`,
        method: "PUT",
        // Only what was typed is sent: an empty field keeps that value.
        body: { reason, ...(title ? { title } : {}), ...(state ? { state } : {}) },
      };
    }
    if (action.name === "remove-correction") {
      return {
        url: `/api/admin/leads/${leadId}/correction`,
        method: "DELETE",
        body: { reason },
      };
    }
    return {
      url: `/api/admin/leads/${leadId}/suppression`,
      method: action.name === "restore" ? "DELETE" : "PUT",
      body: { reason },
    };
  };
}

/** Adapts the Batch Request and Inventory Conflict surfaces, which all POST
 *  `{reason}` to a per-action endpoint, onto the one confirm flow. */
export function postReason(
  endpoint: (action: ControlAction) => string,
): ControlResolver {
  return (action, reason) => ({
    url: endpoint(action),
    method: "POST",
    body: { reason },
  });
}

export function requestEndpoint(id: string) {
  return (action: ControlAction) =>
    action.name === "regenerate"
      ? `/api/admin/requests/${id}/artifact/regenerate`
      : `/api/admin/requests/${id}/${action.name}`;
}

export function conflictEndpoint(id: string) {
  return (action: ControlAction) =>
    `/api/admin/inventory-conflicts/${id}/${action.name}`;
}

const RENOTIFY_CONSEQUENCE =
  "Queues a Telegram post for this Batch Request, or an edit of its existing message. The Batch Request itself does not change.";

/**
 * Re-posts or refreshes a Batch Request's Telegram message (B5).
 *
 * Deliberately outside the server's `actions` array: it repairs the Telegram
 * surface and never moves the Batch Request's status, so it cannot be a
 * transition the domain table offers or refuses.
 */
export function RenotifyAction({
  requestId,
  onQueued,
}: {
  requestId: string;
  onQueued?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState(false);

  function show() {
    setOpen(true);
    setReason("");
    setError("");
  }

  function close() {
    setOpen(false);
    setReason("");
  }

  async function confirm() {
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api(`/api/admin/requests/${requestId}/notify`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      close();
      setQueued(true);
      onQueued?.();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap={2} align="flex-start">
      <Button variant="secondary" onClick={show}>
        Re-notify in Telegram
      </Button>
      <Text size="sm" tone="muted" role="status">
        {queued ? "Telegram re-notification queued." : ""}
      </Text>
      <ConfirmDialog
        open={open}
        onClose={close}
        onConfirm={() => void confirm()}
        title="Re-notify in Telegram"
        consequence={RENOTIFY_CONSEQUENCE}
        confirmLabel="Re-notify"
        destructive={false}
        busy={busy}
      >
        <Stack gap={4}>
          <Field
            label="Reason"
            description="Recorded in Activity so this decision can be explained later."
            required
          >
            <Textarea
              name="reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          {error ? (
            <Text size="sm" tone="danger" role="alert">
              {error}
            </Text>
          ) : null}
        </Stack>
      </ConfirmDialog>
    </Stack>
  );
}

