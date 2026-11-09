/**
 * Customer-side numbers and times (V5): full numbers, money with cents, rates
 * per 1,000, and local dates that carry the year only when it differs.
 */

import { formatCents } from "../billing/money";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
  );
}

/** "1 batch", "2 batches", with the count as a full number. */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString("en-US")} ${count === 1 ? one : many}`;
}

/** "50,000", never 50K. */
export function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

/** "$5.00 per 1,000". */
export function formatRatePerThousand(centsPerThousand: number): string {
  return `${formatCents(centsPerThousand)} per 1,000`;
}

/** "FL, TX". */
export function formatStates(states: string[]): string {
  return states.join(", ");
}

/** "Nov 9", or "Nov 9, 2025" when the year differs. Local time. */
export function formatDay(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const month = date.toLocaleString("en-US", { month: "short" });
  const year =
    date.getFullYear() === now.getFullYear() ? "" : `, ${date.getFullYear()}`;
  return `${month} ${date.getDate()}${year}`;
}

/** "09:41". Local time. */
export function formatClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** "09:41" today, "Nov 8 09:41" before then. Local time. */
export function formatStepTime(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return sameDay(date, now)
    ? formatClock(value)
    : `${formatDay(value, now)} ${formatClock(value)}`;
}

/** "today 09:41", "yesterday 16:02", or "Nov 6 09:41". Local time. */
export function formatSubmitted(value: string, now: Date = new Date()): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (sameDay(date, now)) return `today ${formatClock(value)}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return `yesterday ${formatClock(value)}`;
  return `${formatDay(value, now)} ${formatClock(value)}`;
}

/** Whole local calendar days from `now` until `value`: 0 is today. */
export function calendarDaysUntil(value: string, now: Date = new Date()): number {
  const date = new Date(value);
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((end.getTime() - start.getTime()) / 86_400_000);
}

/** "today", "tomorrow", "in 3 days". */
export function formatExpiresIn(value: string, now: Date = new Date()): string {
  const days = Math.max(0, calendarDaysUntil(value, now));
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}
