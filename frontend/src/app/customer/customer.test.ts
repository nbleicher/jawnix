import { describe, expect, it } from "vitest";
import type { ShouldRevalidateFunctionArgs } from "react-router";

import { batchLabel, batchName } from "./batchLabel";
import {
  calendarDaysUntil,
  formatDay,
  formatExpiresIn,
  formatRatePerThousand,
  formatStepTime,
  formatSubmitted,
  plural,
} from "./format";
import {
  customerHomePath,
  customerShouldRevalidate,
  localMonthStart,
  overviewRedirectLoader,
} from "./home";
import { customerDestinations } from "./navigation";

const ID = "7c3d19ab-1111-4111-8111-111111111111";
const NOW = new Date(2026, 10, 9, 9, 41);

function local(hours: number, minutes: number, day = 9, month = 10, year = 2026) {
  return new Date(year, month, day, hours, minutes).toISOString();
}

describe("batchLabel", () => {
  it("names a batch by its number once it has one", () => {
    expect(batchLabel({ id: ID, number: 1042 })).toBe("#1042");
    expect(batchName({ id: ID, number: 1042 })).toBe("Batch #1042");
  });

  it("falls back to the short reference until batch numbers exist", () => {
    expect(batchLabel({ id: ID })).toBe("REQ-7C3D19AB");
    expect(batchLabel({ id: ID, number: null })).toBe("REQ-7C3D19AB");
  });
});

describe("customer formatting (V5)", () => {
  it("writes days without the year unless it differs", () => {
    expect(formatDay(local(8, 0, 13, 9), NOW)).toBe("Oct 13");
    expect(formatDay(local(8, 0, 30, 11, 2025), NOW)).toBe("Dec 30, 2025");
  });

  it("writes step times as a clock today and with the day before", () => {
    expect(formatStepTime(local(9, 5), NOW)).toBe("09:05");
    expect(formatStepTime(local(16, 2, 8), NOW)).toBe("Nov 8 16:02");
  });

  it("says when a batch was submitted in plain words", () => {
    expect(formatSubmitted(local(9, 41), NOW)).toBe("today 09:41");
    expect(formatSubmitted(local(16, 2, 8), NOW)).toBe("yesterday 16:02");
    expect(formatSubmitted(local(7, 30, 2), NOW)).toBe("Nov 2 07:30");
  });

  it("counts local calendar days until a file expires", () => {
    expect(calendarDaysUntil(local(23, 0), NOW)).toBe(0);
    expect(formatExpiresIn(local(23, 0), NOW)).toBe("today");
    expect(formatExpiresIn(local(1, 0, 10), NOW)).toBe("tomorrow");
    expect(formatExpiresIn(local(8, 0, 12), NOW)).toBe("in 3 days");
  });

  it("writes rates per 1,000 and counts in full", () => {
    expect(formatRatePerThousand(500)).toBe("$5.00 per 1,000");
    expect(plural(82_500, "lead")).toBe("82,500 leads");
    expect(plural(1, "batch", "batches")).toBe("1 batch");
  });
});

describe("the Home read", () => {
  it("asks for this month as the Customer's own calendar has it", () => {
    expect(localMonthStart(NOW)).toEqual(new Date(2026, 10, 1));
    expect(customerHomePath(NOW)).toBe(
      `/api/me/overview?month_start=${encodeURIComponent(new Date(2026, 10, 1).toISOString())}`,
    );
  });

  function revalidates(from: string, to: string, byDefault = false): boolean {
    return customerShouldRevalidate({
      currentUrl: new URL(`https://jawnix.test${from}`),
      nextUrl: new URL(`https://jawnix.test${to}`),
      defaultShouldRevalidate: byDefault,
    } as ShouldRevalidateFunctionArgs);
  }

  it("is read again on arrival at Home and nowhere else", () => {
    expect(revalidates("/app/batches", "/app/home")).toBe(true);
    expect(revalidates("/batches", "/home")).toBe(true);
    expect(revalidates("/app/home", "/app/account")).toBe(false);
    expect(revalidates("/app/batches", "/app/account")).toBe(false);
    expect(revalidates("/app/home", "/app/home")).toBe(false);
    expect(revalidates("/app/account", "/app/batches", true)).toBe(true);
  });

  it("sends old Overview links to Home with their query", () => {
    const response = overviewRedirectLoader({
      request: new Request("https://jawnix.test/app/overview?purchase=success"),
      params: {},
    } as Parameters<typeof overviewRedirectLoader>[0]);
    expect(response.status).toBe(302);
    expect(response.headers.get("Location")).toBe("/home?purchase=success");
  });
});

describe("customer navigation", () => {
  it("names the four destinations in order, each with a page of its own", () => {
    expect(customerDestinations().map((item) => [item.label, item.to])).toEqual([
      ["Home", "/home"],
      ["New request", "/new-request"],
      ["Batches", "/batches"],
      ["Account", "/account"],
    ]);
    // A batch page belongs to Batches by its path, so no custom matching.
    expect(customerDestinations().every((item) => item.match === undefined)).toBe(true);
  });
});
