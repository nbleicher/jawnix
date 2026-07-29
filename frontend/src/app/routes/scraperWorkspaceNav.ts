import type { TerminalDestination } from "../../design-system/primitives/terminal";

/**
 * The one Scraper workspace rail.
 *
 * Every workspace screen was defining its own. Six routes, six different lists:
 * Overview offered eleven destinations, States offered five, and the other four
 * disagreed again — so moving between screens silently removed destinations, and
 * from States there was no route to Keywords, Pipeline, Throughput, Fleet, Status
 * or Campaign history at all. `ScraperCoverage` even declared its rail twice, once
 * at module level and once inline.
 *
 * Nothing caught it because every test rendered one route and asserted that
 * route's own rail. No test compared two, because no test knew there was supposed
 * to be one. It is the same shape as #51 and #59 each generating Alembic revision
 * 0024: five parallel slices independently inventing something that should exist
 * once.
 *
 * Anchor destinations (`#scraper-status` and friends) only resolve on the Overview
 * page, which is where those sections live. `sectionsOn` keeps them out of the
 * rail elsewhere rather than offering links that scroll nowhere.
 */

export const WORKSPACE_ROOT = "/app/admin/acquisition/scraper/workspace";

interface WorkspaceDestination {
  label: string;
  href: string;
  /** True for in-page anchors, which only exist on the Overview screen. */
  anchor?: boolean;
}

const DESTINATIONS: WorkspaceDestination[] = [
  { label: "Overview", href: WORKSPACE_ROOT },
  { label: "States", href: `${WORKSPACE_ROOT}/states` },
  { label: "Status", href: "#scraper-status", anchor: true },
  { label: "Pipeline", href: "#scraper-pipeline", anchor: true },
  { label: "Throughput", href: "#scraper-throughput", anchor: true },
  { label: "Fleet", href: "#scraper-fleet", anchor: true },
  { label: "Keywords", href: `${WORKSPACE_ROOT}/keywords` },
  { label: "Database", href: `${WORKSPACE_ROOT}/database` },
  { label: "Campaign history", href: `${WORKSPACE_ROOT}/history` },
  { label: "Runtime configuration", href: `${WORKSPACE_ROOT}/runtime` },
  { label: "Exit to Acquisition", href: "/app/admin/acquisition" },
];

/**
 * The rail for one workspace screen.
 *
 * @param currentHref  The screen's own path, marked `current` in the rail.
 * @param sectionsOn   Include the in-page anchors. Only the Overview screen
 *                     renders those sections, so only it should link to them.
 */
export function workspaceRail(
  currentHref: string,
  { sectionsOn = false }: { sectionsOn?: boolean } = {},
): TerminalDestination[] {
  return DESTINATIONS.filter((item) => sectionsOn || !item.anchor).map((item) => ({
    label: item.label,
    href: item.href,
    ...(item.href === currentHref ? { current: true } : {}),
  }));
}

/** Every routed destination, for tests that assert reachability. */
export const WORKSPACE_ROUTES: string[] = DESTINATIONS.filter(
  (item) => !item.anchor && item.href.startsWith(WORKSPACE_ROOT),
).map((item) => item.href);
