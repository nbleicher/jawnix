import { afterEach, describe, expect, it, vi } from "vitest";

import { customerAccessLoader } from "./customerAuth";

function mockOverviewStatus(status: number, body: unknown = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: status < 400,
        status,
        json: () => Promise.resolve(body),
      }),
    ),
  );
}

function loaderArgs(path = "/home") {
  return {
    request: new Request(`https://jawnix.test/app${path}`),
    params: {},
  } as Parameters<typeof customerAccessLoader>[0];
}

async function thrownBy(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the loader to throw");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("customerAccessLoader", () => {
  it("redirects an unauthenticated visitor to sign-in with the requested path", async () => {
    mockOverviewStatus(401);
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs("/requests")),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe(
      "/sign-in?next=%2Fapp%2Frequests",
    );
  });

  it("keeps an emailed link's query through sign-in", async () => {
    mockOverviewStatus(401);
    const id = "11111111-1111-4111-8111-111111111111";
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs(`/requests?request=${id}`)),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe(
      `/sign-in?next=${encodeURIComponent(`/app/requests?request=${id}`)}`,
    );
  });

  it("drops a query too long for the session exchange but keeps the page", async () => {
    mockOverviewStatus(401);
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs(`/requests?states=${"TX,".repeat(80)}`)),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe(
      "/sign-in?next=%2Fapp%2Frequests",
    );
  });

  it("hands an authenticated principal without a Customer profile to the admin shell", async () => {
    // The administrator identity intentionally has no Customer profile, and
    // since cutover the site root lands on this loader.
    mockOverviewStatus(404);
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs()),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe("/admin/overview");
  });

  it("returns Home for a Customer, read with the local month start", async () => {
    mockOverviewStatus(200, { customer_name: "Northstar Insurance" });
    await expect(customerAccessLoader(loaderArgs())).resolves.toMatchObject({
      customer_name: "Northstar Insurance",
    });
    const [path] = vi.mocked(fetch).mock.calls[0] as [string];
    expect(path).toMatch(/^\/api\/me\/overview\?month_start=/);
    const start = new Date(
      decodeURIComponent(path.split("month_start=")[1] ?? ""),
    );
    expect(start.getDate()).toBe(1);
    expect(start.getHours()).toBe(0);
  });

  it("keeps an old Overview link's path through sign-in", async () => {
    mockOverviewStatus(401);
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs("/home?purchase=success")),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe(
      `/sign-in?next=${encodeURIComponent("/app/home?purchase=success")}`,
    );
  });
});
