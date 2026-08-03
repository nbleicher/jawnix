import type { Page, Route } from "@playwright/test";

export interface ActivityMockState {
  requests: string[];
}

function json(route: Route, value: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

const ACTORS = ["admin.one@example.com", "admin.two@example.com"];

export const ACTIVITY_ENTRIES = Array.from({ length: 51 }, (_, index) => {
  const customerEntry = index === 0;
  const entityType = customerEntry ? "customer" : "agency";
  const entityId = customerEntry ? "7" : String((index % 9) + 1);
  return {
    id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    action: customerEntry ? "customer_updated" : "agency_updated",
    entityType,
    entityId,
    entityHref: customerEntry
      ? "/app/admin/customers/7"
      : `/app/admin/agencies/${entityId}`,
    actor: customerEntry ? ACTORS[0] : ACTORS[index % ACTORS.length],
    reason: customerEntry
      ? "Correct the Customer name from the signed agreement."
      : `Agency review ${index}.`,
    details: customerEntry
      ? {
          before: { name: "Harbor Coverage" },
          after: { name: "Harbor Insurance" },
        }
      : {},
    recordedAt: new Date(
      Date.UTC(2026, 6, 29, 16, 0) - index * 60_000,
    ).toISOString(),
  };
});

export async function mockActivity(page: Page): Promise<ActivityMockState> {
  const state: ActivityMockState = { requests: [] };

  await page.route(/\/api\/admin\/activity(?:\/.*)?(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    state.requests.push(route.request().url());
    const parts = url.pathname.split("/").filter(Boolean);
    const activityIndex = parts.indexOf("activity");
    const pathType = parts[activityIndex + 1];
    const pathId = parts[activityIndex + 2];
    const actor = url.searchParams.get("actor");
    const query = url.searchParams.get("q")?.toLocaleLowerCase() ?? "";
    const action = url.searchParams.get("action");
    const entityType = pathType ?? url.searchParams.get("entityType");
    const entityId = pathId ?? url.searchParams.get("entityId");
    const dateFrom = url.searchParams.get("dateFrom");
    const dateTo = url.searchParams.get("dateTo");

    const filtered = ACTIVITY_ENTRIES.filter((entry) => {
      const day = entry.recordedAt.slice(0, 10);
      return (
        (!query || [
          entry.action,
          entry.entityType,
          entry.entityId,
          entry.actor,
          entry.reason,
        ].some((value) => value.toLocaleLowerCase().includes(query))) &&
        (!actor || entry.actor === actor) &&
        (!action || entry.action === action) &&
        (!entityType || entry.entityType === entityType) &&
        (!entityId || entry.entityId === entityId) &&
        (!dateFrom || day >= dateFrom) &&
        (!dateTo || day <= dateTo)
      );
    });
    const pageNumber = Math.max(1, Number(url.searchParams.get("page") ?? 1));
    const pageSize = 25;
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    const boundedPage = Math.min(pageNumber, pages);
    return json(route, {
      entries: filtered.slice(
        (boundedPage - 1) * pageSize,
        boundedPage * pageSize,
      ),
      page: boundedPage,
      pageSize,
      total: filtered.length,
      pages,
    });
  });

  return state;
}
