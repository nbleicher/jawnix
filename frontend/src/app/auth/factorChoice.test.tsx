import { describe, expect, it } from "vitest";

import { defaultFactorId } from "./factorChoice";

describe("defaultFactorId", () => {
  it("prefers the primary authenticator", () => {
    expect(
      defaultFactorId([
        { id: "backup", name: "Jawnix backup" },
        { id: "primary", name: "Jawnix primary" },
      ]),
    ).toBe("primary");
  });

  it("treats a single replacement as the de-facto primary", () => {
    expect(
      defaultFactorId([
        { id: "backup", name: "Jawnix backup" },
        { id: "replacement", name: "Jawnix replacement" },
      ]),
    ).toBe("replacement");
  });

  it("falls back to the most recently used factor after a double replacement", () => {
    expect(
      defaultFactorId([
        {
          id: "first-replacement",
          name: "Jawnix replacement",
          lastUsedAt: "2026-07-01T00:00:00Z",
        },
        {
          id: "backup",
          name: "Jawnix backup",
          lastUsedAt: "2026-07-20T00:00:00Z",
        },
        { id: "second-replacement", name: "Jawnix replacement", lastUsedAt: null },
      ]),
    ).toBe("backup");
  });

  it("falls back to the first factor when nothing has been used", () => {
    expect(
      defaultFactorId([
        { id: "first-replacement", name: "Jawnix replacement" },
        { id: "second-replacement", name: "Jawnix replacement" },
      ]),
    ).toBe("first-replacement");
  });
});
