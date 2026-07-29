export const KEYWORD_WORKSPACE = {
  current: ["plumbers", "electricians"],
  version: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  ai_enabled: true,
  idle_expires_in: 900,
  rollover: {
    enabled: false,
    state: "off",
    label: "Off",
    detail: "Manual keyword batches",
    percent_complete: 60,
    posted_jobs: null,
    expected_jobs: null,
    last_status: "generated",
    last_event: "Jul 28 · 12:00 UTC",
  },
  winners: [
    {
      rank: 1,
      keyword: "plumbers",
      phone_businesses: 2480,
      businesses: 4000,
      posted_cells: 1000,
      phones_per_cell: 2.48,
      phone_rate: 0.62,
      last_used: "Jul 28",
    },
    {
      rank: 2,
      keyword: "roof repair",
      phone_businesses: 1200,
      businesses: 2000,
      posted_cells: 600,
      phones_per_cell: 2,
      phone_rate: 0.6,
      last_used: "Jul 27",
    },
  ],
};

export const KEYWORD_PREVIEW = {
  proposed: ["Plumbers", "roofers"],
  added: ["roofers"],
  removed: ["electricians"],
  unchanged: ["Plumbers"],
  expected_version: KEYWORD_WORKSPACE.version,
  review_token: "signed-keyword-review",
};

export const KEYWORD_GENERATION = {
  generation_id: "33333333-3333-4333-8333-333333333333",
  mode: "broad",
  seed_keyword: null,
  keywords: Array.from(
    { length: 25 },
    (_, index) => `Unused Service ${index + 1}`,
  ),
  excluded_count: 7,
  notice:
    "Draft ready. Review below; nothing has been saved or enqueued. 7 candidates were filtered.",
};
