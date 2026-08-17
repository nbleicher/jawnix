import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DesignSystemRoute } from "./DesignSystem";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import type { GallerySection } from "./gallery/types";

/**
 * The gallery discovers its sections by glob rather than by import, so the
 * failure mode is silence: a broken glob or a malformed section makes the page
 * render with nothing in it. An empty gallery would still pass the axe sweep —
 * there is nothing left to violate — so the accessibility gate cannot catch this.
 * These tests are that gate.
 */

const modules = import.meta.glob<{ default: GallerySection }>("./gallery/*.gallery.tsx", {
  eager: true,
});

/** Accessible names of the gallery's own section regions, in document order.
 *  Filtered to declared titles so nested regions from primitives are excluded. */
function sectionTitlesInOrder(): string[] {
  const declared = new Set(Object.values(modules).map((m) => m.default.title));
  return screen
    .getAllByRole("region")
    .map((region) => region.getAttribute("aria-label") ?? "")
    .filter((name) => declared.has(name));
}

function renderGallery() {
  return render(
    <ThemeProvider>
      <DesignSystemRoute />
    </ThemeProvider>,
  );
}

describe("design-system gallery", () => {
  it("discovers every gallery file", () => {
    // Guards the glob pattern itself. If it stops matching, this is the only
    // thing that notices.
    expect(Object.keys(modules).length).toBeGreaterThanOrEqual(7);
  });

  it("renders one region per discovered section", () => {
    renderGallery();

    // Match by declared title rather than counting every region: primitives such
    // as AuthPanel render their own nested regions, so a raw count is wrong.
    // Compared against the discovered set rather than a hardcoded list, so adding
    // a section needs no edit here — but losing one fails.
    expect(sectionTitlesInOrder()).toHaveLength(Object.keys(modules).length);
  });

  it("gives every section a heading matching its declared title", () => {
    renderGallery();

    for (const module of Object.values(modules)) {
      const { title } = module.default;
      expect(screen.getByRole("region", { name: title })).toBeInTheDocument();
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    }
  });

  it("orders sections by their declared order", () => {
    renderGallery();

    const expected = Object.values(modules)
      .map((m) => m.default)
      .sort((a, b) => a.order - b.order || a.title.localeCompare(b.title))
      .map((s) => s.title);

    expect(sectionTitlesInOrder()).toEqual(expected);
  });

  it("keeps the single page heading, so sections do not compete with it", () => {
    renderGallery();

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("renders every discovered component under the dark Match scheme", async () => {
    const user = userEvent.setup();
    renderGallery();

    await user.click(screen.getByRole("button", { name: "Switch to dark desk" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "match");
    expect(document.documentElement).toHaveAttribute("data-scheme", "dark");
    expect(sectionTitlesInOrder()).toHaveLength(Object.keys(modules).length);
  });
});
