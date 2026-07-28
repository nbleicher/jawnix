export const CUSTOMER_OVERVIEW = {
  first_name: "Customer",
  licensed_states: ["FL", "TX"],
  current_request: {
    id: "11111111-1111-4111-8111-111111111111",
    lead_count: 750,
    states: ["FL", "TX"],
    submitted_at: "2026-07-25T12:00:00Z",
    delivered_at: null,
    status: {
      label: "Preparing Batch",
      description:
        "We are waiting for enough matching leads. There is nothing you need to do.",
      tone: "warning",
    },
  },
  recent_deliveries: [
    {
      request_id: "22222222-2222-4222-8222-222222222222",
      lead_count: 500,
      states: ["TX"],
      delivered_at: "2026-07-20T12:00:00Z",
    },
  ],
  next_action: {
    kind: "review_request",
    label: "Review Request",
    description: "See the latest progress for your current Batch Request.",
    href: "/app/requests",
  },
  primary_actions: [
    {
      kind: "request_batch",
      label: "Request a Batch",
      description: "Choose a quantity and Licensed States for your next Batch.",
      href: "/app/requests",
    },
    {
      kind: "submit_feedback",
      label: "Submit Lead Feedback",
      description: "Share the result of a delivered lead.",
      href: "/app/feedback",
    },
  ],
} as const;

export const EMPTY_CUSTOMER_OVERVIEW = {
  ...CUSTOMER_OVERVIEW,
  licensed_states: [],
  current_request: null,
  recent_deliveries: [],
  next_action: {
    kind: "add_licensed_states",
    label: "Add Licensed States",
    description: "Add at least one Licensed State before requesting a Batch.",
    href: "/app/account",
  },
} as const;
