import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { Tabs } from "./tabs";

describe("Tabs (V7)", () => {
  it("is a named row of links with the current one marked", () => {
    render(
      <MemoryRouter>
        <Tabs
          label="Account"
          items={[
            { to: "/account", label: "Profile", current: false },
            { to: "/account/billing", label: "Billing", current: true },
          ]}
        />
      </MemoryRouter>,
    );

    const row = screen.getByRole("navigation", { name: "Account" });
    const [profile, billing] = within(row).getAllByRole("link");
    expect(profile).toHaveAttribute("href", "/account");
    expect(profile).not.toHaveAttribute("aria-current");
    expect(billing).toHaveAttribute("href", "/account/billing");
    expect(billing).toHaveAttribute("aria-current", "page");
  });
});
