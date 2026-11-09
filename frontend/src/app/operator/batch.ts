import { useCallback, useEffect, useState } from "react";

import { api } from "../auth/adminMFA";
import type { RequestDetail } from "../routes/AdminFulfillment";
import type { BatchAvailability, RecordLink } from "./format";

/**
 * The Batch page contract: GET /api/admin/batches/{number or id}, which is
 * the Batch Request detail plus what the operator reads around it.
 */

export interface BatchProgressStep {
  key: string;
  label: string;
  done: boolean;
  at: string | null;
}

export interface BatchRecentItem {
  id: string;
  number: number;
  href: string;
  leadCount: number;
  stage: string;
  createdAt: string;
  deliveredAt: string | null;
}

export interface BatchCost {
  billed: boolean;
  rateCentsPerThousand?: number | null;
  amountCents?: number | null;
  hold?: "active" | "captured" | "released" | null;
  availableBalanceCents?: number;
}

export interface BatchFill extends BatchAvailability {
  needed: number;
  olderOpen: {
    count: number;
    first: { number: number; customer: string; stage: string } | null;
  } | null;
}

export interface BatchDetail extends Omit<RequestDetail, "activityTimeline"> {
  number: number;
  href: string;
  stage: string;
  needsDecision: boolean;
  database: string;
  customerRecord: RecordLink;
  agencyRecord: RecordLink | null;
  fill: BatchFill;
  cost: BatchCost;
  progress: BatchProgressStep[];
  recentBatches: BatchRecentItem[];
  openLeadReports: number;
  rowsPerFile?: number;
}

export function loadBatch(reference: string | number): Promise<BatchDetail> {
  return api<BatchDetail>(`/api/admin/batches/${encodeURIComponent(String(reference))}`);
}

/** One Batch read for a side panel, re-read after each action. */
export function useBatch(reference: string | number | null) {
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (reference === null) return;
    let current = true;
    setError("");
    loadBatch(reference)
      .then((value) => {
        if (current) setBatch(value);
      })
      .catch((caught: unknown) => {
        if (!current) return;
        setBatch(null);
        setError(caught instanceof Error ? caught.message : "This Batch could not be read.");
      });
    return () => {
      current = false;
    };
  }, [reference, version]);

  const reload = useCallback(() => setVersion((value) => value + 1), []);
  // A read for the previous row must not show under the next row's title.
  const loaded =
    batch !== null &&
    (String(batch.number) === String(reference) || batch.id === String(reference));
  return { batch: loaded ? batch : null, error, reload };
}
