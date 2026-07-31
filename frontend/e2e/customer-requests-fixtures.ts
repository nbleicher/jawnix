import type { Page, Route } from "@playwright/test";

const SUBMITTED = "2026-07-25T12:00:00Z";
const APPROVED = "2026-07-25T13:30:00Z";

const WAITING_DESCRIPTION =
  "We are holding this request until enough matching leads are available. "
  + "Nothing has gone wrong and there is nothing you need to do — it moves on "
  + "automatically as soon as inventory covers the full quantity.";

/** Approved, then paused because inventory cannot cover the full quantity. */
export const WAITING_REQUEST = {
  id: "11111111-1111-4111-8111-111111111111",
  lead_count: 750,
  states: ["FL", "TX"],
  submitted_at: SUBMITTED,
  delivered_at: null,
  status: {
    label: "Preparing Batch",
    description: "We are preparing your leads for delivery.",
    tone: "warning",
  },
  milestones: {
    milestones: [
      {
        key: "submitted",
        label: "Submitted",
        description: "We have your request.",
        state: "complete",
        occurred_at: SUBMITTED,
      },
      {
        key: "under_review",
        label: "Under Review",
        description: "Jawnix is checking the quantity and states.",
        state: "complete",
        occurred_at: APPROVED,
      },
      {
        key: "preparing_batch",
        label: "Preparing Batch",
        description: WAITING_DESCRIPTION,
        state: "paused",
        occurred_at: null,
      },
      {
        key: "delivered",
        label: "Delivered",
        description: "Your Batch was emailed to you.",
        state: "upcoming",
        occurred_at: null,
      },
    ],
    current_key: "preparing_batch",
    pause: {
      kind: "inventory_wait",
      milestone_key: "preparing_batch",
      label: "Waiting for Inventory",
      description: WAITING_DESCRIPTION,
    },
    outcome: null,
  },
  can_cancel: true,
  next_action: null,
  receipt_href: "/app/requests?request=11111111-1111-4111-8111-111111111111",
} as const;

/** Stopped at review, with the one action that still makes sense. */
export const REJECTED_REQUEST = {
  id: "22222222-2222-4222-8222-222222222222",
  lead_count: 300,
  states: ["TX"],
  submitted_at: "2026-07-22T12:00:00Z",
  delivered_at: null,
  status: {
    label: "Not Approved",
    description: "This request was not approved.",
    tone: "danger",
  },
  milestones: {
    milestones: [
      {
        key: "submitted",
        label: "Submitted",
        description: "We have your request.",
        state: "complete",
        occurred_at: "2026-07-22T12:00:00Z",
      },
      {
        key: "under_review",
        label: "Under Review",
        description: "Jawnix is checking the quantity and states.",
        state: "stopped",
        occurred_at: null,
      },
      {
        key: "preparing_batch",
        label: "Preparing Batch",
        description: "We are selecting your leads.",
        state: "not_reached",
        occurred_at: null,
      },
      {
        key: "delivered",
        label: "Delivered",
        description: "Your Batch was emailed to you.",
        state: "not_reached",
        occurred_at: null,
      },
    ],
    current_key: null,
    pause: null,
    outcome: {
      kind: "rejected",
      milestone_key: "under_review",
      label: "Not Approved",
      description:
        "This request was not approved, so no leads were reserved for it. "
        + "You can submit a new request with a different quantity or scope.",
      tone: "danger",
      occurred_at: "2026-07-23T09:00:00Z",
    },
  },
  can_cancel: false,
  next_action: {
    kind: "request_batch",
    label: "Request another Batch",
    description: "Start a new request.",
    href: "/app/requests",
  },
  receipt_href: "/app/requests?request=22222222-2222-4222-8222-222222222222",
} as const;

export const DELIVERED_REQUEST = {
  ...WAITING_REQUEST,
  id: "44444444-4444-4444-8444-444444444444",
  delivered_at: "2026-07-27T16:00:00Z",
  status: {
    label: "Delivered",
    description: "Your Batch is ready.",
    tone: "success",
  },
  milestones: {
    milestones: WAITING_REQUEST.milestones.milestones.map((milestone) => ({
      ...milestone,
      state: "complete",
      occurred_at: milestone.occurred_at ?? "2026-07-27T16:00:00Z",
    })),
    current_key: "delivered",
    pause: null,
    outcome: null,
  },
  can_cancel: false,
  next_action: {
    kind: "submit_feedback",
    label: "Submit Feedback",
    description: "Tell us how the leads performed.",
    href: "/app/feedback",
  },
  receipt_href:
    "/app/requests?request=44444444-4444-4444-8444-444444444444",
} as const;

export const BATCH_REQUEST_WORKSPACE = {
  limits: {
    minimum_lead_count: 1,
    maximum_lead_count: 100_000,
    licensed_states: ["FL", "TX"],
  },
  blocker: null,
  requests: [WAITING_REQUEST, REJECTED_REQUEST],
} as const;

export const EMPTY_BATCH_REQUEST_WORKSPACE = {
  ...BATCH_REQUEST_WORKSPACE,
  requests: [],
} as const;

export const BLOCKED_BATCH_REQUEST_WORKSPACE = {
  limits: {
    minimum_lead_count: 1,
    maximum_lead_count: 100_000,
    licensed_states: [],
  },
  blocker: {
    reason: "no_licensed_states",
    label: "Add a Licensed State first",
    description:
      "A Batch Request covers the states you are licensed in. "
      + "Add at least one before requesting a Batch.",
    action: {
      kind: "add_licensed_states",
      label: "Add Licensed States",
      description: "Save the states you are licensed in.",
      href: "/app/account",
    },
  },
  requests: [],
} as const;

export interface BatchRequestMockOptions {
  workspace?: unknown;
  /** Successive aggregate reads, holding on the last value once exhausted. */
  workspaceSequence?: unknown[];
  /** Held open long enough that a second click lands while the first is busy. */
  submitDelayMs?: number;
}

export interface BatchRequestMockState {
  workspaceRequests: number;
  submissions: Array<Record<string, unknown>>;
  cancellations: string[];
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

/**
 * A backend that behaves like the real one on the point that matters: one
 * Batch Request per submission key, no matter how many times the key arrives.
 */
export async function mockBatchRequests(
  page: Page,
  options: BatchRequestMockOptions = {},
): Promise<BatchRequestMockState> {
  const state: BatchRequestMockState = {
    workspaceRequests: 0,
    submissions: [],
    cancellations: [],
  };
  const workspace = options.workspace ?? BATCH_REQUEST_WORKSPACE;
  const bySubmissionKey = new Map<string, unknown>();

  await page.route(/\/api\/me\/batch-requests\/[^/]+\/cancel$/, (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2) ?? "";
    state.cancellations.push(id);
    return json(route, {
      ...WAITING_REQUEST,
      id,
      status: {
        label: "Canceled",
        description: "This request was canceled.",
        tone: "neutral",
      },
      milestones: {
        milestones: WAITING_REQUEST.milestones.milestones.map((milestone) =>
          milestone.key === "preparing_batch"
            ? { ...milestone, state: "stopped" }
            : milestone.key === "delivered"
              ? { ...milestone, state: "not_reached" }
              : milestone,
        ),
        current_key: null,
        pause: null,
        outcome: {
          kind: "canceled",
          milestone_key: "preparing_batch",
          label: "Canceled",
          description:
            "This request was withdrawn before any leads were reserved. "
            + "You can submit a new request whenever you are ready.",
          tone: "neutral",
          occurred_at: "2026-07-28T15:00:00Z",
        },
      },
      can_cancel: false,
      next_action: {
        kind: "request_batch",
        label: "Request another Batch",
        description: "Start a new request.",
        href: "/app/requests",
      },
    });
  });

  await page.route(/\/api\/me\/batch-requests$/, async (route) => {
    if (route.request().method() !== "POST") {
      state.workspaceRequests += 1;
      const sequence = options.workspaceSequence;
      const response = sequence?.length
        ? sequence[Math.min(state.workspaceRequests - 1, sequence.length - 1)]
        : workspace;
      return json(route, response);
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    state.submissions.push(body);
    if (options.submitDelayMs) {
      await new Promise((resolve) => setTimeout(resolve, options.submitDelayMs));
    }
    const key = String(body.idempotency_key);
    const existing = bySubmissionKey.get(key);
    if (existing) {
      return json(route, { created: false, request: existing }, 200);
    }
    const created = {
      ...WAITING_REQUEST,
      id: "33333333-3333-4333-8333-333333333333",
      lead_count: Number(body.lead_count),
      states:
        body.state_mode === "all_saved"
          ? ["FL", "TX"]
          : (body.states as string[]),
      status: {
        label: "Submitted",
        description: "Your request is ready for review.",
        tone: "info",
      },
      milestones: {
        milestones: WAITING_REQUEST.milestones.milestones.map((milestone) =>
          milestone.key === "submitted"
            ? { ...milestone, state: "current" }
            : { ...milestone, state: "upcoming", occurred_at: null },
        ),
        current_key: "submitted",
        pause: null,
        outcome: null,
      },
      can_cancel: true,
      next_action: null,
      receipt_href: "/app/requests?request=33333333-3333-4333-8333-333333333333",
    };
    bySubmissionKey.set(key, created);
    return json(route, { created: true, request: created }, 201);
  });

  return state;
}
