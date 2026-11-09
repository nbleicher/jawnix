import { useState } from "react";

import { Button, ButtonLink } from "../../design-system/primitives/Button";
import type { ButtonVariant } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { formatCents } from "../billing/money";
import {
  cancelBatchRequest,
  requestNewCopy,
} from "../routes/batchRequests";
import type { BatchRequest } from "../routes/batchRequests";
import { batchName } from "./batchLabel";
import type { BatchIdentity } from "./batchLabel";
import { batchAction } from "./batchStage";

/**
 * The actions a batch offers the Customer: Download, Get a new copy, Cancel.
 * Batches, the batch page, and Home share them, so each reads and behaves
 * the same everywhere. Results come back through `onResult`, so a table row
 * keeps its height and the page says what happened in one place.
 */

export interface BatchActionResult {
  /** The batch as the backend now has it, when the action changed it. */
  batch?: BatchRequest;
  /** What to tell the Customer. */
  message: string;
  failed: boolean;
}

type OnResult = (result: BatchActionResult) => void;

function failure(caught: unknown, fallback: string): BatchActionResult {
  return {
    message: caught instanceof Error ? caught.message : fallback,
    failed: true,
  };
}

export function DownloadButton({
  batch,
  variant = "secondary",
}: {
  batch: BatchIdentity & { artifact: { download_href: string | null } | null };
  variant?: Exclude<ButtonVariant, "danger">;
}) {
  const href = batch.artifact?.download_href;
  if (!href) return null;
  return (
    <ButtonLink href={href} variant={variant} aria-label={`Download ${batchName(batch)}`}>
      Download
    </ButtonLink>
  );
}

export function NewCopyButton({
  batch,
  onResult,
  variant = "secondary",
}: {
  batch: BatchIdentity;
  onResult: OnResult;
  variant?: ButtonVariant;
}) {
  const [busy, setBusy] = useState(false);

  async function ask() {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await requestNewCopy(batch.id);
      onResult({
        batch: updated,
        message: `A new copy of ${batchName(batch)} is on the way. We email you when it is ready, usually within a few minutes.`,
        failed: false,
      });
    } catch (caught) {
      onResult(failure(caught, "We could not ask for a new copy. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant={variant}
      busy={busy}
      busyLabel="Asking…"
      onClick={() => void ask()}
      aria-label={busy ? undefined : `Get a new copy of ${batchName(batch)}`}
    >
      Get a new copy
    </Button>
  );
}

export function CancelBatchButton({
  batch,
  onResult,
  variant = "secondary",
  label = "Cancel",
}: {
  batch: BatchRequest;
  onResult: OnResult;
  variant?: ButtonVariant;
  label?: string;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  async function cancel() {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await cancelBatchRequest(batch.id);
      setConfirming(false);
      onResult({ batch: updated, message: `${batchName(batch)} was canceled.`, failed: false });
    } catch (caught) {
      setConfirming(false);
      onResult(failure(caught, "This batch could not be canceled. Please try again."));
    } finally {
      setBusy(false);
    }
  }

  const held =
    batch.cost?.state === "held" && batch.cost.amount_cents !== null
      ? formatCents(batch.cost.amount_cents)
      : null;
  return (
    <>
      <Button
        variant={variant}
        onClick={() => setConfirming(true)}
        aria-label={`Cancel ${batchName(batch)}`}
      >
        {label}
      </Button>
      <ConfirmDialog
        open={confirming}
        onClose={() => {
          if (!busy) setConfirming(false);
        }}
        onConfirm={() => void cancel()}
        title={`Cancel ${batchName(batch)}?`}
        consequence={
          held
            ? `Nothing has been delivered yet. Canceling releases the ${held} held from your balance and cannot be undone.`
            : "Nothing has been delivered yet. Canceling cannot be undone."
        }
        confirmLabel="Cancel batch"
        cancelLabel="Keep batch"
        busy={busy}
      />
    </>
  );
}

/** The one action a batch's row offers, or nothing (V3). */
export function BatchRowAction({
  batch,
  onResult,
}: {
  batch: BatchRequest;
  onResult: OnResult;
}) {
  switch (batchAction(batch)) {
    case "cancel":
      return <CancelBatchButton batch={batch} onResult={onResult} />;
    case "download":
      return <DownloadButton batch={batch} />;
    case "new_copy":
      return <NewCopyButton batch={batch} onResult={onResult} />;
    default:
      return null;
  }
}
