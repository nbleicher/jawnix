import { afterEach, describe, expect, it, vi } from "vitest";

function response(status: number, body: unknown = {}) {
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
  };
}

/** A fresh module per test: the gate cache is module state. */
async function freshLoader() {
  vi.resetModules();
  return (await import("./adminMFA")).adminAccessLoader;
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
  vi.useRealTimers();
});

describe("adminAccessLoader", () => {
  it("reuses a passed check briefly instead of checking on every revalidation", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() =>
      Promise.resolve(response(200, { ok: true })),
    );
    vi.stubGlobal("fetch", fetchMock);
    const loader = await freshLoader();

    await expect(loader()).resolves.toBeNull();
    await expect(loader()).resolves.toBeNull();
    await expect(loader()).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    vi.setSystemTime(Date.now() + 31_000);
    await expect(loader()).resolves.toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never caches a refusal: a dropped assurance still redirects every time", async () => {
    const fetchMock = vi.fn((path: unknown) =>
      Promise.resolve(
        String(path) === "/api/auth/admin-mfa/access"
          ? response(401, { detail: "MFA required" })
          : response(200, { next: "/app/admin/mfa/challenge" }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const loader = await freshLoader();

    for (let attempt = 0; attempt < 2; attempt += 1) {
      const thrown = (await thrownBy(loader())) as Response;
      expect(thrown.headers.get("Location")).toBe("/admin/mfa/challenge");
    }
    const accessChecks = fetchMock.mock.calls.filter(
      ([path]) => String(path) === "/api/auth/admin-mfa/access",
    );
    expect(accessChecks).toHaveLength(2);
  });
});
