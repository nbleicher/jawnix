import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeProvider, SCHEME_STORAGE_KEY } from "../design-system/theme/ThemeProvider";
import { BrandLockup } from "./BrandLockup";

describe("BrandLockup", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SCHEME_STORAGE_KEY);
    document.documentElement.removeAttribute("data-scheme");
  });

  it("shows JAWNIX and no audience word", () => {
    render(
      <ThemeProvider>
        <BrandLockup />
      </ThemeProvider>,
    );

    expect(screen.getByText("JAWNIX")).toBeVisible();
    expect(screen.queryByText("Customer")).not.toBeInTheDocument();
    expect(screen.queryByText("Administration")).not.toBeInTheDocument();
  });

  it("toggles scheme from the J tile", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <BrandLockup />
      </ThemeProvider>,
    );

    const tile = screen.getByRole("button", { name: "Switch to dark desk" });
    await user.click(tile);

    expect(document.documentElement).toHaveAttribute("data-scheme", "dark");
    expect(
      screen.getByRole("button", { name: "Switch to light paper" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("renders the J tile as the currentColor scheme indicator", () => {
    render(
      <ThemeProvider>
        <BrandLockup />
      </ThemeProvider>,
    );

    const tile = screen.getByRole("button", { name: "Switch to dark desk" });
    const path = tile.querySelector("svg path");
    expect(path).toHaveAttribute("fill", "currentColor");
    expect(path).toHaveAttribute("fill-rule", "evenodd");
  });
});
