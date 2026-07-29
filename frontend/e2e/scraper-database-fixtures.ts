interface DatabaseWorkspace {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  totals: { businesses: number; unique_phones: number } | null;
  states: Array<{
    state: string;
    businesses: number;
    unique_phones: number;
    niches: number;
  }>;
  browse: {
    records: Array<{
      title: string;
      phone: string | null;
      website: string | null;
      state: string | null;
      niche: string | null;
      last_seen: string;
    }>;
    search: string;
    state: string;
    page: number;
    page_size: number;
    total: number;
    pages: number;
    has_previous: boolean;
    has_next: boolean;
  } | null;
  stored_exports: Array<{ filename: string; size_label: string }>;
}

interface DatabaseStateDetail {
  service_state: "connected" | "unavailable";
  last_successful_at: string | null;
  idle_expires_in: number;
  state: string;
  totals: {
    state: string;
    businesses: number;
    unique_phones: number;
    niches: number;
  } | null;
  niches: Array<{
    key: string;
    label: string;
    businesses: number;
    unique_phones: number;
  }>;
}

export const DATABASE_WORKSPACE: DatabaseWorkspace = {
  service_state: "connected",
  last_successful_at: "2026-07-29T12:00:00Z",
  idle_expires_in: 900,
  totals: {
    businesses: 9_244_326,
    unique_phones: 2_305_025,
  },
  states: [
    {
      state: "OH",
      businesses: 136_150,
      unique_phones: 71_204,
      niches: 25,
    },
    {
      state: "PA",
      businesses: 161_863,
      unique_phones: 84_110,
      niches: 24,
    },
  ],
  browse: {
    records: [
      {
        title: "Buckeye Plumbing",
        phone: "(614) 555-0101",
        website: "https://buckeye.example",
        state: "OH",
        niche: "plumbers",
        last_seen: "Jul 28, 11:59",
      },
      {
        title: "Capital Electric",
        phone: "614-555-0101",
        website: null,
        state: "OH",
        niche: "electricians",
        last_seen: "Jul 28, 11:58",
      },
    ],
    search: "",
    state: "",
    page: 1,
    page_size: 50,
    total: 51,
    pages: 2,
    has_previous: false,
    has_next: true,
  },
  stored_exports: [
    { filename: "OH.csv", size_label: "42.5 KB" },
    { filename: "PA.csv", size_label: "38.0 KB" },
  ],
};

export const DATABASE_STATE: DatabaseStateDetail = {
  service_state: "connected",
  last_successful_at: "2026-07-29T12:00:00Z",
  idle_expires_in: 900,
  state: "OH",
  totals: {
    state: "OH",
    businesses: 136_150,
    unique_phones: 71_204,
    niches: 2,
  },
  niches: [
    {
      key: "plumbers",
      label: "plumbers",
      businesses: 80_000,
      unique_phones: 42_000,
    },
    {
      key: "__uncategorized__",
      label: "Uncategorized",
      businesses: 56_150,
      unique_phones: 29_204,
    },
  ],
};
