import { formatRequestRef } from "../routes/batchRequests";

/** Anything that names one batch: its id, and its number once it has one. */
export interface BatchIdentity {
  id: string;
  /** "Batch #1042", shared with the operator. */
  number?: number | null;
}

/**
 * The one way the Customer side names a batch: "#1042".
 *
 * Every visible batch name goes through here, so no screen prints an internal
 * reference on its own. A payload without a number falls back to the short reference.
 */
export function batchLabel(batch: BatchIdentity): string {
  return typeof batch.number === "number"
    ? `#${batch.number}`
    : formatRequestRef(batch.id);
}

/** "Batch #1042", for accessible names and sentences. */
export function batchName(batch: BatchIdentity): string {
  return `Batch ${batchLabel(batch)}`;
}
