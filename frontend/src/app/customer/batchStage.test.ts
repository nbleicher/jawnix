import { describe, expect, it } from "vitest";

import type { BatchRequest } from "../routes/batchRequests";
import { batchAction, formatBatchCost, formatFiles, isMoving, stageBadge } from "./batchStage";

const NOW = new Date(2026, 10, 9, 10, 0);

function batch(overrides: Partial<BatchRequest> = {}): BatchRequest {
  return {
    id: "10171017-1017-4017-8017-101710171017",
    number: 1017,
    lead_count: 50_000,
    rows_per_file: 10_000,
    states: ["FL", "TX"],
    submitted_at: new Date(2026, 9, 1, 14, 20).toISOString(),
    delivered_at: new Date(2026, 9, 2, 8, 5).toISOString(),
    status: { label: "Delivered", description: "", tone: "success" },
    milestones: { milestones: [], current_key: null, pause: null, outcome: null },
    can_cancel: false,
    next_action: null,
    href: "/app/batches/1017",
    receipt_href: "/app/batches/1017",
    artifact: {
      filename: "batch.zip",
      row_count: 50_000,
      parts: [],
      columns: ["phone", "title", "state"],
      expires_at: new Date(2026, 10, 1, 8, 5).toISOString(),
      available: false,
      download_href: null,
    },
    stage: "file_expired",
    can_request_copy: true,
    cost: { rate_cents_per_thousand: 500, amount_cents: 25_000, state: "charged", at: null },
    history: [],
    ...overrides,
  };
}

describe("batch stages (V4)", () => {
  it("reads each stage as a badge: red to act, black in progress or done, grey closed", () => {
    expect(stageBadge(batch({ stage: "waiting_approval" }))).toEqual({ label: "Waiting for approval", tone: "info" });
    expect(stageBadge(batch({ stage: "ready" }))).toEqual({ label: "Ready", tone: "success" });
    expect(stageBadge(batch({ stage: "not_approved" }))).toEqual({ label: "Not approved", tone: "danger" });
    expect(stageBadge(batch({ stage: "canceled" }))).toEqual({ label: "Canceled", tone: "neutral" });
    expect(stageBadge(batch({ stage: "copy_on_the_way" }))).toEqual({ label: "New copy on the way", tone: "info" });
  });

  it("names the day an expiring file goes, and an expired one on its page", () => {
    const expiring = batch({
      stage: "expiring",
      artifact: { ...batch().artifact!, expires_at: new Date(2026, 10, 12, 15, 30).toISOString() },
    });
    expect(stageBadge(expiring, { now: NOW })).toEqual({ label: "Expires Nov 12", tone: "danger" });
    expect(stageBadge(batch(), { now: NOW })).toEqual({ label: "File expired", tone: "neutral" });
    expect(stageBadge(batch(), { now: NOW, withDate: true }).label).toBe("File expired Nov 1");
  });

  it("listens only to batches that move on their own", () => {
    expect(isMoving(batch({ stage: "preparing" }))).toBe(true);
    expect(isMoving(batch({ stage: "copy_on_the_way" }))).toBe(true);
    expect(isMoving(batch({ stage: "ready" }))).toBe(false);
  });
});

describe("one action per row (V3)", () => {
  it("offers cancel, then download, then a new copy, or nothing", () => {
    expect(batchAction(batch({ can_cancel: true }))).toBe("cancel");
    expect(
      batchAction(batch({ can_request_copy: false, artifact: { ...batch().artifact!, download_href: "/file" } })),
    ).toBe("download");
    expect(batchAction(batch())).toBe("new_copy");
    expect(batchAction(batch({ can_request_copy: false }))).toBeNull();
  });
});

describe("money and files in Customer words (V5)", () => {
  it("writes what a batch costs and where the money is", () => {
    expect(formatBatchCost(batch().cost)).toBe("$250.00");
    expect(formatBatchCost({ ...batch().cost!, state: "held", amount_cents: 12_500 })).toBe("$125.00 held");
    expect(formatBatchCost({ ...batch().cost!, state: "released" })).toBe("$0.00");
    expect(formatBatchCost(null)).toBe("—");
  });

  it("describes the files a batch is delivered in", () => {
    expect(formatFiles(50_000, 10_000)).toBe("5 CSV files of 10,000 rows, in one zip");
    expect(formatFiles(50_000, 15_000)).toBe("4 CSV files of up to 15,000 rows, in one zip");
    expect(formatFiles(750, 750)).toBe("One CSV file");
  });
});
