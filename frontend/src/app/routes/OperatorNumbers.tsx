import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { formatCents } from "../billing/money";

/**
 * The operator's eight headline numbers (Overview now, Today next).
 *
 * Mirrors `OperatorNumbers` in jawnix/operator_numbers.py. The usable DB1 and
 * DB2 totals are background snapshots, so each carries its own as-of time.
 */

export interface UsableInventory {
  usable: number;
  asOf: string;
}

export interface DecisionKind {
  kind: string;
  label: string;
  count: number;
  oldestAt: string | null;
}

export interface OperatorNumbersData {
  generatedAt: string;
  windowDays: number;
  inventory: { db1: UsableInventory | null; db2: UsableInventory | null };
  openBatches: {
    total: number;
    awaitingApproval: number;
    shortOfInventory: number;
  };
  delivered: { leads: number; batches: number; customers: number };
  customers: { active: number; withSetupProblems: number };
  creditBalance: { availableCents: number; billedCustomers: number };
  creditsUsed: { cents: number; priorCents: number };
  decisions: {
    waiting: number;
    oldestAt: string | null;
    kinds: DecisionKind[];
  };
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function plural(count: number, one: string, many: string = `${one}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/** "06:00" today, "Nov 8 06:00" otherwise, with the year only when it
 *  differs (V5). Local time. */
export function formatAsOf(value: string, now: Date): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) return time;
  const month = date.toLocaleString("en-US", { month: "short" });
  const year =
    date.getFullYear() === now.getFullYear() ? "" : `, ${date.getFullYear()}`;
  return `${month} ${date.getDate()}${year} ${time}`;
}

/** Ages as 20m, 2h, 3d (V5). */
export function formatAge(value: string, now: Date): string {
  const minutes = Math.max(
    0,
    Math.floor((now.getTime() - new Date(value).getTime()) / 60_000),
  );
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** "Up 12% on the prior 30 days", against the same-length window before. */
export function formatCreditsChange(
  cents: number,
  priorCents: number,
  windowDays: number,
): string {
  const window = `the prior ${windowDays} days`;
  if (priorCents <= 0) {
    return cents > 0 ? `None used in ${window}` : `None used in ${window} either`;
  }
  const change = Math.round(((cents - priorCents) / priorCents) * 100);
  if (change === 0) return `Level with ${window}`;
  return `${change > 0 ? "Up" : "Down"} ${Math.abs(change)}% on ${window}`;
}

function usableTile(
  label: string,
  source: string,
  snapshot: UsableInventory | null,
  now: Date,
) {
  return (
    <Kpi
      label={label}
      value={snapshot ? snapshot.usable.toLocaleString() : "Not counted"}
      meta={
        snapshot
          ? `${source} · as of ${formatAsOf(snapshot.asOf, now)}`
          : `${source} · not counted yet`
      }
    />
  );
}

export function OperatorNumbers({
  numbers,
  now = new Date(),
}: {
  numbers: OperatorNumbersData;
  /** Injected by tests so as-of times and ages are deterministic. */
  now?: Date;
}) {
  const days = numbers.windowDays;
  const { openBatches, delivered, customers, creditBalance, creditsUsed, decisions } =
    numbers;
  return (
    <KpiRow label="Key numbers">
      {usableTile("DB1 usable", "Operator leads", numbers.inventory.db1, now)}
      {usableTile("DB2 usable", "CSV uploads", numbers.inventory.db2, now)}
      <Kpi
        label="Open batches"
        value={openBatches.total.toLocaleString()}
        meta={`${openBatches.awaitingApproval.toLocaleString()} awaiting approval · ${openBatches.shortOfInventory.toLocaleString()} short`}
      />
      <Kpi
        label={`Delivered · ${days} days`}
        value={delivered.leads.toLocaleString()}
        meta={`${plural(delivered.batches, "batch", "batches")} to ${plural(delivered.customers, "customer")}`}
      />
      <Kpi
        label="Active customers"
        value={customers.active.toLocaleString()}
        meta={`${customers.withSetupProblems.toLocaleString()} with setup problems`}
      />
      <Kpi
        label="Credit balance"
        value={formatCents(creditBalance.availableCents)}
        meta={plural(creditBalance.billedCustomers, "billed customer")}
      />
      <Kpi
        label={`Credits used · ${days} days`}
        value={formatCents(creditsUsed.cents)}
        meta={formatCreditsChange(creditsUsed.cents, creditsUsed.priorCents, days)}
      />
      <Kpi
        label="Decisions waiting"
        value={decisions.waiting.toLocaleString()}
        meta={
          decisions.oldestAt
            ? `Oldest ${formatAge(decisions.oldestAt, now)}`
            : "Nothing waiting"
        }
        alert={decisions.waiting > 0}
      />
    </KpiRow>
  );
}
