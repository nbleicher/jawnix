import { afterEach, expect, it, vi } from "vitest";
import { clearReadPrefetch, prefetchRead, readResponse } from "./routeReadCache";

afterEach(() => { clearReadPrefetch(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("reuses an in-flight prefetch once and then reads fresh data", async () => {
  let resolve!: (value: Response) => void;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementationOnce(() => new Promise((done) => { resolve = done; })).mockResolvedValue(new Response("fresh"));
  prefetchRead("/api/admin/fulfillment");
  const reading = readResponse("/api/admin/fulfillment");
  resolve(new Response("prefetched"));
  expect(await (await reading).text()).toBe("prefetched");
  expect(await (await readResponse("/api/admin/fulfillment")).text()).toBe("fresh");
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("does not serve an expired or invalidated prefetch", async () => {
  vi.useFakeTimers();
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("data"));
  prefetchRead("/api/admin/fulfillment");
  await vi.advanceTimersByTimeAsync(3_001);
  await readResponse("/api/admin/fulfillment");
  prefetchRead("/api/admin/fulfillment");
  clearReadPrefetch();
  await readResponse("/api/admin/fulfillment");
  expect(fetch).toHaveBeenCalledTimes(4);
});

it("never prefetches authentication or privileged scraper endpoints", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  prefetchRead("/api/auth/admin-mfa/access");
  prefetchRead("/api/admin/scraper/monitoring");
  expect(fetch).not.toHaveBeenCalled();
});
