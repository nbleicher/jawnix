import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  WORKSPACE_ROOT,
  WORKSPACE_ROUTE_PATTERNS,
  WORKSPACE_ROUTES,
  workspaceRail,
} from "./scraperWorkspaceNav";

/**
 * The rail was defined six times, once per Scraper ticket, and the lists
 * disagreed: Overview offered eleven destinations, States five, and the other
 * four differed again. Moving between screens silently removed destinations, so
 * from States there was no route to Keywords, Pipeline, Throughput, Fleet,
 * Status or Campaign history at all.
 *
 * Nothing caught it because every test rendered one route and asserted that
 * route's own rail. No test compared two — so no test knew there was supposed to
 * be one. These tests are that comparison.
 */

const ROUTES_DIR = join(__dirname);

function scraperRouteFiles(): string[] {
  return readdirSync(ROUTES_DIR).filter(
    (name) => name.startsWith("Scraper") && name.endsWith(".tsx") && !name.includes(".test."),
  );
}

describe("the Scraper workspace rail", () => {
  it("is declared in exactly one place", () => {
    // The regression guard. A slice that hand-rolls its own rail fails here
    // rather than shipping a screen its neighbours cannot reach.
    const offenders = scraperRouteFiles().filter((name) => {
      const source = readFileSync(join(ROUTES_DIR, name), "utf8");
      return /const\s+\w*rail\w*\s*(:\s*TerminalDestination\[\])?\s*=\s*\[/i.test(source);
    });

    expect(offenders, "these files declare their own workspace rail").toEqual([]);
  });

  it("offers every routed destination from every screen", () => {
    // The bug in one assertion: the rail must not shrink depending on where you
    // are standing.
    for (const from of WORKSPACE_ROUTES) {
      const hrefs = workspaceRail(from).map((item) => item.href);
      for (const destination of WORKSPACE_ROUTES) {
        expect(hrefs, `${from} cannot reach ${destination}`).toContain(destination);
      }
    }
  });

  it("marks exactly one destination current, and only the right one", () => {
    for (const from of WORKSPACE_ROUTES) {
      const current = workspaceRail(from).filter((item) => item.current);
      expect(current).toHaveLength(1);
      expect(current[0]?.href).toBe(from);
    }
  });

  it("always offers a way out", () => {
    for (const from of WORKSPACE_ROUTES) {
      expect(workspaceRail(from).map((item) => item.label)).toContain("Exit to Acquisition");
    }
  });

  it("contains real routes rather than page-position anchors", () => {
    for (const from of WORKSPACE_ROUTES) {
      expect(workspaceRail(from).some((item) => item.href.startsWith("#")))
        .toBe(false);
    }
  });

  it("marks detail pages themselves current instead of their parent route", () => {
    const statePath = `${WORKSPACE_ROOT}/states/OH`;
    const stateRail = workspaceRail(statePath, {
      pageLabel: "OH coverage",
    });
    expect(stateRail.filter((item) => item.current)).toEqual([
      { label: "OH coverage", href: statePath, current: true },
    ]);
    expect(stateRail.find((item) => item.label === "States")?.current).toBeUndefined();

    const databasePath = `${WORKSPACE_ROOT}/database/states/OH`;
    const databaseRail = workspaceRail(databasePath, {
      pageLabel: "OH database",
    });
    expect(databaseRail.filter((item) => item.current)).toEqual([
      { label: "OH database", href: databasePath, current: true },
    ]);
    expect(databaseRail.some((item) => item.href.startsWith("#"))).toBe(false);
    expect(databaseRail.find((item) => item.label === "Database")?.current)
      .toBeUndefined();
  });

  it("covers every real workspace route, including parameterized details", () => {
    const routes = readFileSync(join(ROUTES_DIR, "..", "routes.tsx"), "utf8");
    const declared = [...routes.matchAll(/"acquisition\/scraper\/workspace([^"]*)"/g)].map(
      ([, tail]) => `${WORKSPACE_ROOT}${tail}`,
    );

    expect(new Set(WORKSPACE_ROUTE_PATTERNS)).toEqual(new Set(declared));
  });
});
