type CellStatus = "posted" | "reserved" | "failed" | "uncovered";

interface CoverageFeed<T> {
  state: "ok" | "unavailable";
  refresh_seconds: number;
  fetched_at: string | null;
  data: T | null;
}

interface StateKeywordActivity {
  keyword: string;
  businesses: number;
  posted_cells: number;
  total_cells: number;
  coverage: number;
  empty_rate: number;
  last_enqueued: string | null;
}

interface StateGridCoverage {
  cells: {
    index: number;
    cell: string;
    status: CellStatus;
  }[];
  posted: number;
  reserved: number;
  failed: number;
  uncovered: number;
}

interface StateCoverageSnapshot {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  states: CoverageFeed<
    {
      state: string;
      businesses: number;
      posted_cells: number;
      total_cells: number;
      active_keywords: number;
      coverage: number;
      status: "covered" | "partial" | "uncovered";
    }[]
  >;
}

interface StateCoverageDetail {
  state: string;
  service_state: "connected" | "degraded" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  keywords: CoverageFeed<StateKeywordActivity[]>;
  cells: CoverageFeed<StateGridCoverage>;
}

export const coverageKeywords: StateKeywordActivity[] = [
  {
    keyword: "24 Hour Pharmacy",
    businesses: 124,
    posted_cells: 110,
    total_cells: 220,
    coverage: 50,
    empty_rate: 0.125,
    last_enqueued: "Jul 28, 11:59",
  },
  {
    keyword: "Abatement Service",
    businesses: 38,
    posted_cells: 0,
    total_cells: 220,
    coverage: 0,
    empty_rate: 0.75,
    last_enqueued: null,
  },
];

export const coverageGrid: StateGridCoverage = {
  posted: 1,
  reserved: 1,
  failed: 1,
  uncovered: 1,
  cells: [
    {
      index: 1,
      cell: "40.000000,-80.000000",
      status: "posted",
    },
    {
      index: 2,
      cell: "40.000000,-79.750000",
      status: "reserved",
    },
    {
      index: 3,
      cell: "40.000000,-79.500000",
      status: "failed",
    },
    {
      index: 4,
      cell: "40.000000,-79.250000",
      status: "uncovered",
    },
  ],
};

export function coverageFeed<T>(
  data: T | null,
  refreshSeconds: number,
  state: "ok" | "unavailable" = data === null ? "unavailable" : "ok",
): CoverageFeed<T> {
  return {
    state,
    refresh_seconds: refreshSeconds,
    fetched_at: data === null ? null : "2026-07-29T12:00:00Z",
    data,
  };
}

export function stateCoverageSnapshot(
  refreshSeconds = 20,
): StateCoverageSnapshot {
  return {
    service_state: "connected",
    last_successful_at: "2026-07-29T12:00:00Z",
    idle_expires_in: 900,
    states: coverageFeed(
      [
        {
          state: "PA",
          businesses: 161_863,
          posted_cells: 110,
          total_cells: 220,
          active_keywords: 25,
          coverage: 50,
          status: "partial",
        },
        {
          state: "OH",
          businesses: 136_150,
          posted_cells: 240,
          total_cells: 240,
          active_keywords: 25,
          coverage: 100,
          status: "covered",
        },
      ],
      refreshSeconds,
    ),
  };
}

export function stateCoverageDetail(
  refreshSeconds = { keywords: 10, cells: 15 },
): StateCoverageDetail {
  return {
    state: "PA",
    service_state: "connected",
    last_successful_at: "2026-07-29T12:00:00Z",
    idle_expires_in: 900,
    keywords: coverageFeed(coverageKeywords, refreshSeconds.keywords),
    cells: coverageFeed(coverageGrid, refreshSeconds.cells),
  };
}
