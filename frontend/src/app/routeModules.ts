import type { ComponentType } from "react";
import type { LoaderFunction } from "react-router";
import { prefetchRead } from "./routeReadCache";

const pages = {
  AdminMFA: () => import("./routes/AdminMFA"),
  AdminDestinations: () => import("./routes/AdminDestinations"),
  AdminAcquisition: () => import("./routes/AdminAcquisition"),
  AdminAcquisitionDetails: () => import("./routes/AdminAcquisitionDetails"),
  AdminSourcePerformance: () => import("./routes/AdminSourcePerformance"),
  AdminCustomers: () => import("./routes/AdminCustomers"),
  AdminCustomerDetails: () => import("./routes/AdminCustomerDetails"),
  AdminAgencies: () => import("./routes/AdminAgencies"),
  AdminFulfillment: () => import("./routes/AdminFulfillment"),
  AdminLeadReport: () => import("./routes/AdminLeadReport"),
  AdminActivity: () => import("./routes/AdminActivity"),
  ScraperWorkspace: () => import("./routes/ScraperWorkspace"),
  ScraperOverview: () => import("./routes/ScraperOverview"),
  ScraperKeywords: () => import("./routes/ScraperKeywords"),
  ScraperCampaignHistory: () => import("./routes/ScraperCampaignHistory"),
  ScraperRuntimeConfiguration: () => import("./routes/ScraperRuntimeConfiguration"),
  ScraperDatabase: () => import("./routes/ScraperDatabase"),
  ScraperCoverage: () => import("./routes/ScraperCoverage"),
  CustomerOverview: () => import("./routes/CustomerOverview"),
  CustomerAccount: () => import("./routes/CustomerAccount"),
  CustomerFeedback: () => import("./routes/CustomerFeedback"),
  CustomerRequests: () => import("./routes/CustomerRequests"),
  CustomerAuth: () => import("./routes/CustomerAuth"),
  DesignSystem: () => import("./routes/DesignSystem"),
};
type PageName = keyof typeof pages;
type PageModule = Record<string, unknown>;
const loaded = new Map<PageName, Promise<PageModule>>();

function loadPage(name: PageName): Promise<PageModule> {
  let result = loaded.get(name);
  if (!result) {
    result = pages[name]();
    loaded.set(name, result);
    void result.catch(() => { loaded.delete(name); });
  }
  return result;
}

export function lazyPage(name: PageName, component: string, loader?: string) {
  return async () => {
    const module = await loadPage(name);
    return {
      Component: module[component] as ComponentType,
      ...(loader ? { loader: module[loader] as LoaderFunction } : {}),
    };
  };
}

const destinations: Record<string, { page: PageName; reads?: string[] }> = {
  "/admin/overview": { page:"AdminDestinations", reads:["/api/admin/operations-overview"] },
  "/admin/fulfillment": { page:"AdminFulfillment", reads:["/api/admin/fulfillment", "/api/admin/pool-breakdown"] },
  "/admin/acquisition": { page:"AdminAcquisition", reads:["/api/admin/acquisition"] },
  "/admin/customers": { page:"AdminCustomers", reads:["/api/admin/customers/directory"] },
  "/admin/activity": { page:"AdminActivity", reads:["/api/admin/activity"] },
  "/overview": { page:"CustomerOverview" },
  "/requests": { page:"CustomerRequests", reads:["/api/me/batch-requests"] },
  "/feedback": { page:"CustomerFeedback" },
  "/account": { page:"CustomerAccount" },
};

export function prefetchDestination(href: string) {
  const path = href.replace(/^\/app(?=\/|$)/, "").split(/[?#]/, 1)[0] ?? "";
  const target = destinations[path];
  if (!target) return;
  void loadPage(target.page).catch(() => undefined);
  for (const read of target.reads ?? []) prefetchRead(read);
}
