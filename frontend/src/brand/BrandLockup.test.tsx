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

  it("toggles scheme from the JX stamp", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <BrandLockup />
      </ThemeProvider>,
    );

    const stamp = screen.getByRole("button", { name: "Switch to dark desk" });
    await user.click(stamp);

    expect(document.documentElement).toHaveAttribute("data-scheme", "dark");
    expect(
      screen.getByRole("button", { name: "Switch to light paper" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("renders the vermilion JX stamp with the letters knocked out", () => {
    render(
      <ThemeProvider>
        <BrandLockup />
      </ThemeProvider>,
    );

    const stamp = screen.getByRole("button", { name: "Switch to dark desk" });
    const path = stamp.querySelector("svg path");
    expect(path).toHaveAttribute("fill", "var(--jx-stamp-ground)");
    expect(path).toHaveAttribute("fill-rule", "evenodd");
  });
});
