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
          current="billing"
          items={[
            { key: "profile", href: "/app/account", label: "Profile" },
            { key: "billing", href: "/app/account/billing", label: "Billing" },
          ]}
        />
      </MemoryRouter>,
    );

    const row = screen.getByRole("navigation", { name: "Account" });
    const [profile, billing] = within(row).getAllByRole("link");
    expect(profile).not.toHaveAttribute("aria-current");
    expect(billing).toHaveAttribute("aria-current", "page");
    expect(billing).toHaveTextContent("Billing");
  });
});
