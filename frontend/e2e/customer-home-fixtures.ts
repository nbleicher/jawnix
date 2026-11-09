import type { MockCreditWallet } from "./customer-billing-fixtures";

/**
 * Home fixtures (`/api/me/overview`), matching the approved renders (V9):
 * Northstar Insurance, #1042 in progress (25,000 FL leads, $125.00 held at
 * $5.00 per 1,000), a $412.50 balance. The exported fixtures are dated from
 * the run, so the expiring file always expires in three days; visual
 * baselines use `homeAt(RENDER_DAY)` with the browser clock fixed to it.
 */

const DAY_MS = 86_400_000;

/** The approved renders' day, for visual baselines that must not drift. */
export const RENDER_DAY = new Date(2026, 10, 9, 10, 0);

export const IN_PROGRESS_BATCH_ID = "42424242-4242-4242-8242-424242424242";
export const EXPIRING_BATCH_ID = "24242424-2424-4242-8242-242424242424";
export const EXPIRED_BATCH_ID = "17171717-1717-4717-8717-171717171717";
export const READY_BATCH_ID = "38383838-3838-4838-8838-383838383838";

/** Home as it reads on `now`, in the browser's local time. */
export function homeAt(now: Date) {
  const localDay = (daysFromToday: number, hours: number, minutes = 0) =>
    new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate() + daysFromToday,
      hours,
      minutes,
    ).toISOString();
  const monthStart = () =>
    new Date(now.getFullYear(), now.getMonth(), 1).toISOString();

  function deliveredBatch(
    id: string,
    number: number,
    leadCount: number,
    states: string[],
    deliveredDaysAgo: number,
    costCents: number,
  ) {
    const deliveredAt = localDay(-deliveredDaysAgo, 8, 12);
    const expiresAt = new Date(Date.parse(deliveredAt) + 30 * DAY_MS).toISOString();
    const live = deliveredDaysAgo < 30;
    return {
      id,
      number,
      lead_count: leadCount,
      states,
      submitted_at: localDay(-deliveredDaysAgo - 1, 14, 20),
      delivered_at: deliveredAt,
      cost_cents: costCents,
      href: `/app/requests?request=${id}`,
      file_available: live,
      file_expires_at: expiresAt,
      download_href: live ? `/api/me/batch-requests/${id}/artifact` : null,
    };
  }

  const READY_BATCH = deliveredBatch(READY_BATCH_ID, 1038, 50_000, ["FL", "TX"], 2, 25_000);
  const EXPIRING_BATCH = deliveredBatch(EXPIRING_BATCH_ID, 1024, 50_000, ["FL"], 27, 25_000);
  const EXPIRED_BATCH = deliveredBatch(EXPIRED_BATCH_ID, 1017, 50_000, ["FL", "TX"], 38, 25_000);

  const IN_PROGRESS = {
    batch: {
      id: IN_PROGRESS_BATCH_ID,
      number: 1042,
      lead_count: 25_000,
      states: ["FL"],
      submitted_at: localDay(0, 9, 41),
      delivered_at: null,
      cost_cents: 12_500,
      href: `/app/requests?request=${IN_PROGRESS_BATCH_ID}`,
      file_available: false,
      file_expires_at: null,
      download_href: null,
    },
    steps: [
      { key: "submitted", label: "Submitted", state: "complete", occurred_at: localDay(0, 9, 41) },
      { key: "approval", label: "Waiting for approval", state: "current", occurred_at: null },
      { key: "preparing", label: "Preparing your leads", state: "upcoming", occurred_at: null },
      { key: "ready", label: "Ready to download", state: "upcoming", occurred_at: null },
    ],
    note: "Noah reviews each request, then we deliver the full batch at once and email you.",
    held_cents: 12_500,
    can_cancel: true,
  };

  const CUSTOMER_HOME = {
    customer_name: "Northstar Insurance",
    appointed_states: ["FL", "TX"],
    can_request: true,
    billing: {
      available_balance_cents: 41_250,
      held_cents: 12_500,
      held_for: [{ id: IN_PROGRESS_BATCH_ID, number: 1042 }],
      lead_rate_cents_per_thousand: 500,
      covers_leads: 82_500,
      spent_this_month_cents: 75_000,
    },
    month: { starts_at: monthStart(), delivered_leads: 150_000, delivered_batches: 2 },
    alerts: [
      { kind: "file_expiring", batch: EXPIRING_BATCH, problem: null, reference_lead_count: null },
    ],
    in_progress: [IN_PROGRESS],
    recent_batches: [
      READY_BATCH,
      deliveredBatch("31313131-3131-4131-8131-313131313131", 1031, 100_000, ["TX"], 8, 50_000),
      EXPIRING_BATCH,
      EXPIRED_BATCH,
    ],
    batch_count: 24,
  };

  /** A Customer who has not requested anything yet. */
  const NEW_CUSTOMER_HOME = {
    ...CUSTOMER_HOME,
    billing: {
      available_balance_cents: 0,
      held_cents: 0,
      held_for: [],
      lead_rate_cents_per_thousand: 500,
      covers_leads: 0,
      spent_this_month_cents: 0,
    },
    month: { starts_at: monthStart(), delivered_leads: 0, delivered_batches: 0 },
    alerts: [
      { kind: "low_balance", batch: null, problem: null, reference_lead_count: null },
    ],
    in_progress: [],
    recent_batches: [],
    batch_count: 0,
  };

  /** Setup not finished: no appointed states yet. */
  const SETUP_INCOMPLETE_HOME = {
    ...NEW_CUSTOMER_HOME,
    appointed_states: [],
    can_request: false,
    billing: null,
    alerts: [
      { kind: "setup_incomplete", batch: null, problem: "no_appointed_states", reference_lead_count: null },
    ],
  };

  return {
    READY_BATCH,
    EXPIRING_BATCH,
    EXPIRED_BATCH,
    IN_PROGRESS,
    CUSTOMER_HOME,
    NEW_CUSTOMER_HOME,
    SETUP_INCOMPLETE_HOME,
  };
}

const today = homeAt(new Date());
export const READY_BATCH = today.READY_BATCH;
export const EXPIRING_BATCH = today.EXPIRING_BATCH;
export const EXPIRED_BATCH = today.EXPIRED_BATCH;
export const IN_PROGRESS = today.IN_PROGRESS;
export const CUSTOMER_HOME = today.CUSTOMER_HOME;
/** A Customer who has not requested anything yet. */
export const NEW_CUSTOMER_HOME = today.NEW_CUSTOMER_HOME;
/** Setup not finished: no appointed states yet. */
export const SETUP_INCOMPLETE_HOME = today.SETUP_INCOMPLETE_HOME;

/** The header wallet that agrees with CUSTOMER_HOME. */
export const NORTHSTAR_WALLET: MockCreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 53_750,
  activeHoldsCents: 12_500,
  availableBalanceCents: 41_250,
  purchases: [],
  ledger: [],
};
