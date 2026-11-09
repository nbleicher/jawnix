import type { ComponentType } from "react";
import type { LoaderFunction, ShouldRevalidateFunction } from "react-router";
import { prefetchRead } from "./routeReadCache";

const pages = {
  AdminMFA: () => import("./routes/AdminMFA"),
  AdminToday: () => import("./routes/AdminToday"),
  AdminBatches: () => import("./routes/AdminBatches"),
  AdminAcquisition: () => import("./routes/AdminAcquisition"),
  AdminAcquisitionDetails: () => import("./routes/AdminAcquisitionDetails"),
  AdminSourcePerformance: () => import("./routes/AdminSourcePerformance"),
  AdminCustomers: () => import("./routes/AdminCustomers"),
  AdminCustomerDetails: () => import("./routes/AdminCustomerDetails"),
  AdminAgencies: () => import("./routes/AdminAgencies"),
  AdminFulfillment: () => import("./routes/AdminFulfillment"),
  AdminInventory: () => import("./routes/AdminInventory"),
  AdminInventoryUploads: () => import("./routes/AdminInventoryUploads"),
  AdminInventoryExclusions: () => import("./routes/AdminInventoryExclusions"),
  AdminLeadLookup: () => import("./routes/AdminLeadLookup"),
  AdminLeadReport: () => import("./routes/AdminLeadReport"),
  AdminActivity: () => import("./routes/AdminActivity"),
  ScraperWorkspace: () => import("./routes/ScraperWorkspace"),
  ScraperOverview: () => import("./routes/ScraperOverview"),
  ScraperKeywords: () => import("./routes/ScraperKeywords"),
  ScraperCampaignHistory: () => import("./routes/ScraperCampaignHistory"),
  ScraperRuntimeConfiguration: () => import("./routes/ScraperRuntimeConfiguration"),
  ScraperDatabase: () => import("./routes/ScraperDatabase"),
  ScraperCoverage: () => import("./routes/ScraperCoverage"),
  CustomerHome: () => import("./routes/CustomerHome"),
  CustomerAccount: () => import("./routes/CustomerAccount"),
  CustomerFeedback: () => import("./routes/CustomerFeedback"),
  CustomerRequests: () => import("./routes/CustomerRequests"),
  CustomerAuth: () => import("./routes/CustomerAuth"),
  CustomerSignup: () => import("./routes/CustomerSignup"),
  DesignSystem: () => import("./routes/DesignSystem"),
  ManagedAdmin: () => import("./routes/ManagedAdmin"),
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

export function lazyPage(name: PageName, component: string, loader?: string, shouldRevalidate?: string) {
  return async () => {
    const module = await loadPage(name);
    return {
      Component: module[component] as ComponentType,
      ...(loader ? { loader: module[loader] as LoaderFunction } : {}),
      ...(shouldRevalidate
        ? { shouldRevalidate: module[shouldRevalidate] as ShouldRevalidateFunction }
        : {}),
    };
  };
}

const destinations: Record<string, { page: PageName; reads?: string[] }> = {
  "/admin/today": { page:"AdminToday" },
  "/admin/batches": { page:"AdminBatches" },
  "/admin/inventory": { page:"AdminInventory", reads:["/api/admin/inventory"] },
  "/admin/acquisition": { page:"AdminAcquisition", reads:["/api/admin/acquisition"] },
  "/admin/customers": { page:"AdminCustomers", reads:["/api/admin/customers/directory"] },
  "/admin/activity": { page:"AdminActivity", reads:["/api/admin/activity"] },
  "/home": { page:"CustomerHome" },
  "/requests": { page:"CustomerRequests", reads:["/api/me/batch-requests"] },
  "/feedback": { page:"CustomerFeedback" },
  "/account": { page:"CustomerAccount" },
  "/manager/uploads": { page:"ManagedAdmin" },
  "/manager/customers": { page:"ManagedAdmin" },
};

export function prefetchDestination(href: string) {
  const path = href.replace(/^\/app(?=\/|$)/, "").split(/[?#]/, 1)[0] ?? "";
  const target = destinations[path];
  if (!target) return;
  void loadPage(target.page).catch(() => undefined);
  for (const read of target.reads ?? []) prefetchRead(read);
}
