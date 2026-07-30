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
 * Routed destinations are shared. In-page destinations are deliberately supplied
 * by the screen that owns their target, so Keywords keeps its editor/rollover/
 * winners shortcuts, Database keeps its browse/export shortcuts, and state detail
 * screens keep their page-local shortcuts without leaking dead anchors elsewhere.
 */

export const WORKSPACE_ROOT = "/app/admin/acquisition/scraper/workspace";

interface WorkspaceRailOptions {
  /** Add an exact routed item for a detail page beneath a shared parent route. */
  pageLabel?: string;
  /** In-page destinations whose targets are rendered by this screen. */
  sections?: readonly TerminalDestination[];
}

const DESTINATIONS: TerminalDestination[] = [
  { label: "Overview", href: WORKSPACE_ROOT },
  { label: "States", href: `${WORKSPACE_ROOT}/states` },
  { label: "Keywords", href: `${WORKSPACE_ROOT}/keywords` },
  { label: "Database", href: `${WORKSPACE_ROOT}/database` },
  { label: "Campaign history", href: `${WORKSPACE_ROOT}/history` },
  { label: "Runtime configuration", href: `${WORKSPACE_ROOT}/runtime` },
  { label: "Exit to Acquisition", href: "/app/admin/acquisition" },
];

export const WORKSPACE_SECTIONS = {
  overview: [
    { label: "Status", href: "#scraper-status" },
    { label: "Pipeline", href: "#scraper-pipeline" },
    { label: "Throughput", href: "#scraper-throughput" },
    { label: "Fleet", href: "#scraper-fleet" },
  ],
  keywords: [
    { label: "Keyword editor", href: "#keyword-editor" },
    { label: "Automatic rollover", href: "#keyword-rollover" },
    { label: "Winner rankings", href: "#keyword-winners" },
  ],
  database: [
    { label: "State exports", href: "#database-states" },
    { label: "Browse records", href: "#database-browse" },
    { label: "Stored exports", href: "#stored-exports" },
  ],
  databaseState: [
    { label: "Niches", href: "#state-niches" },
  ],
  stateCoverage: [
    { label: "State keywords", href: "#state-keywords" },
    { label: "Grid cells", href: "#state-grid" },
  ],
} as const;

/**
 * The rail for one workspace screen.
 *
 * Local items are inserted after the routed destination that owns the current
 * path. Detail pages receive an exact item of their own, which prevents their
 * parent ("States" or "Database") from being announced as the current page.
 */
export function workspaceRail(
  currentHref: string,
  {
    pageLabel,
    sections = [],
  }: WorkspaceRailOptions = {},
): TerminalDestination[] {
  const exit = DESTINATIONS.at(-1);
  const routes = DESTINATIONS.slice(0, -1);
  const ownerIndex = routes.reduce(
    (found, item, index) =>
      currentHref === item.href || currentHref.startsWith(`${item.href}/`)
        ? index
        : found,
    -1,
  );
  const local: TerminalDestination[] = [
    ...(pageLabel ? [{ label: pageLabel, href: currentHref }] : []),
    ...sections,
  ];
  const items = [...routes];
  items.splice(ownerIndex + 1, 0, ...local);
  if (exit) items.push(exit);

  return items.map((item) => ({
    label: item.label,
    href: item.href,
    ...(item.href === currentHref ? { current: true } : {}),
  }));
}

/** Every routed destination, for tests that assert reachability. */
export const WORKSPACE_ROUTES: string[] = DESTINATIONS.filter(
  (item) => item.href.startsWith(WORKSPACE_ROOT),
).map((item) => item.href);

/** Router templates include detail pages whose concrete state is only known at runtime. */
export const WORKSPACE_ROUTE_PATTERNS = [
  ...WORKSPACE_ROUTES,
  `${WORKSPACE_ROOT}/states/:state`,
  `${WORKSPACE_ROOT}/database/states/:state`,
];
