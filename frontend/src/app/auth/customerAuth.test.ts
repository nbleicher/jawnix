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

function loaderArgs(path = "/overview") {
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

  it("hands an authenticated principal without a Customer profile to the admin shell", async () => {
    // The administrator identity intentionally has no Customer profile, and
    // since cutover the site root lands on this loader.
    mockOverviewStatus(404);
    const thrown = (await thrownBy(
      customerAccessLoader(loaderArgs()),
    )) as Response;
    expect(thrown.headers.get("Location")).toBe("/admin/overview");
  });

  it("returns the overview payload for a Customer", async () => {
    mockOverviewStatus(200, { first_name: "Casey" });
    await expect(customerAccessLoader(loaderArgs())).resolves.toMatchObject({
      first_name: "Casey",
    });
  });
});
