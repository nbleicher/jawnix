export const READY_REQUEST_ID = "44444444-4444-4444-8444-444444444444";
export const EXPIRING_REQUEST_ID = "55555555-5555-4555-8555-555555555555";
export const WAITING_REQUEST_ID = "11111111-1111-4111-8111-111111111111";
export const FEEDBACK_REQUEST_ID = "66666666-6666-4666-8666-666666666666";

export const BATCH_READY_ITEM = {
  id: `batch-ready:${READY_REQUEST_ID}`,
  kind: "batch_ready",
  title: "Your Batch is ready",
  description: "Download the 750-lead Batch Artifact.",
  tone: "info",
  action: {
    kind: "download_artifact",
    label: "Download CSV",
    description: "Download this Batch Artifact while it is live.",
    href: `/api/me/batch-requests/${READY_REQUEST_ID}/artifact`,
  },
} as const;

export const ARTIFACT_EXPIRING_ITEM = {
  id: `artifact-expiring:${EXPIRING_REQUEST_ID}`,
  kind: "artifact_expiring",
  title: "Batch Artifact expires soon",
  description: "Download the 500-lead Batch Artifact within 3 days.",
  tone: "warning",
  action: {
    kind: "download_artifact",
    label: "Download CSV",
    description: "Download this Batch Artifact before it expires.",
    href: `/api/me/batch-requests/${EXPIRING_REQUEST_ID}/artifact`,
  },
} as const;

export const WAITING_INVENTORY_ITEM = {
  id: `waiting-inventory:${WAITING_REQUEST_ID}`,
  kind: "waiting_inventory",
  title: "Batch Request is waiting for inventory",
  description: "Review or cancel your 750-lead request for FL, TX.",
  tone: "warning",
  action: {
    kind: "review_request",
    label: "Review request",
    description: "Open this Batch Request's detail page.",
    href: `/app/requests?request=${WAITING_REQUEST_ID}`,
  },
} as const;

export const FEEDBACK_NUDGE_ITEM = {
  id: `feedback-nudge:${FEEDBACK_REQUEST_ID}`,
  kind: "feedback_nudge",
  title: "How did this Batch perform?",
  description: "Share one lead outcome from this 1,000-lead Batch.",
  tone: "info",
  action: {
    kind: "submit_feedback",
    label: "Give feedback",
    description: "Record one Lead Disposition or Quality Rating.",
    href: `/app/feedback?request=${FEEDBACK_REQUEST_ID}`,
  },
} as const;

export const SETUP_PROBLEM_ITEM = {
  id: "setup-problem:no-licensed-states",
  kind: "setup_problem",
  title: "Add Licensed States",
  description: "Add at least one Licensed State before requesting a Batch.",
  tone: "warning",
  action: {
    kind: "add_licensed_states",
    label: "Open Account",
    description: "Add the states where you are licensed.",
    href: "/app/account",
  },
} as const;

export const CUSTOMER_OVERVIEW = {
  items: [
    ARTIFACT_EXPIRING_ITEM,
    BATCH_READY_ITEM,
    WAITING_INVENTORY_ITEM,
    FEEDBACK_NUDGE_ITEM,
    SETUP_PROBLEM_ITEM,
  ],
} as const;

export const EMPTY_CUSTOMER_OVERVIEW = { items: [] } as const;
