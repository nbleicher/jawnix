import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
  AppointedStatesPicker,
  matchesState,
  selectionSummary,
  stateNames,
} from "./AppointedStatesPicker";
import type { StateOption } from "./AppointedStatesPicker";

// The backend's order (jawnix/states.py): DC sits between DE and FL.
const OPTIONS: StateOption[] = [
  ["AL", "Alabama"], ["AK", "Alaska"], ["AZ", "Arizona"], ["AR", "Arkansas"],
  ["CA", "California"], ["CO", "Colorado"], ["CT", "Connecticut"],
  ["DE", "Delaware"], ["DC", "District of Columbia"], ["FL", "Florida"],
  ["GA", "Georgia"], ["HI", "Hawaii"], ["ID", "Idaho"], ["IL", "Illinois"],
  ["IN", "Indiana"], ["IA", "Iowa"], ["KS", "Kansas"], ["KY", "Kentucky"],
  ["LA", "Louisiana"], ["ME", "Maine"], ["MD", "Maryland"],
  ["MA", "Massachusetts"], ["MI", "Michigan"], ["MN", "Minnesota"],
  ["MS", "Mississippi"], ["MO", "Missouri"], ["MT", "Montana"],
  ["NE", "Nebraska"], ["NV", "Nevada"], ["NH", "New Hampshire"],
  ["NJ", "New Jersey"], ["NM", "New Mexico"], ["NY", "New York"],
  ["NC", "North Carolina"], ["ND", "North Dakota"], ["OH", "Ohio"],
  ["OK", "Oklahoma"], ["OR", "Oregon"], ["PA", "Pennsylvania"],
  ["RI", "Rhode Island"], ["SC", "South Carolina"], ["SD", "South Dakota"],
  ["TN", "Tennessee"], ["TX", "Texas"], ["UT", "Utah"], ["VT", "Vermont"],
  ["VA", "Virginia"], ["WA", "Washington"], ["WV", "West Virginia"],
  ["WI", "Wisconsin"], ["WY", "Wyoming"],
].map(([code, name]) => ({ code: code!, name: name! }));
const NAMES = OPTIONS.filter((option) => option.code !== "DC").map((option) => option.name);

function Harness({ initial = ["FL", "TX"] }: { initial?: string[] }) {
  const [selected, setSelected] = useState(initial);
  return (
    <form data-testid="form" onSubmit={() => { throw new Error("submitted"); }}>
      <AppointedStatesPicker options={OPTIONS} selected={selected} onChange={setSelected} />
    </form>
  );
}

describe("AppointedStatesPicker (V10)", () => {
  it("lists the 50 states in alphabetical order for a grid of 10 rows, DC apart", () => {
    render(<Harness />);

    const grid = document.querySelector<HTMLElement>(".appointed-states__grid")!;
    const states = within(grid).getAllByRole("checkbox");
    expect(states).toHaveLength(50);
    expect(states.map((box) => box.parentElement?.textContent)).toEqual(NAMES);
    // Filled down each column: 10 rows of 5, or 25 rows of 2 on phones.
    expect(grid.style.getPropertyValue("--appointed-rows")).toBe("10");
    expect(grid.style.getPropertyValue("--appointed-rows-phone")).toBe("25");
    expect(within(grid).queryByRole("checkbox", { name: /District of Columbia/ })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Also appointed in District of Columbia" })).not.toBeChecked();
    expect(screen.getByRole("group", { name: "Appointed states" })).toBeVisible();
    expect(screen.getByText("2 selected · Florida, Texas")).toBeVisible();
  });

  it("narrows by name or code, keeps hidden choices, and picks a lone match on Enter", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const search = screen.getByRole("searchbox", { name: "Find a state" });

    await user.type(search, "tx");
    expect(search).toHaveValue("tx");
    expect(screen.getAllByRole("checkbox", { name: /^(?!Also)/ })).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: "Texas" })).toBeChecked();

    await user.clear(search);
    await user.type(search, "georgia{Enter}");
    expect(search).toHaveValue("");
    expect(screen.getByRole("checkbox", { name: "Georgia" })).toBeChecked();
    expect(screen.getByText("3 selected · Florida, Georgia, Texas")).toBeVisible();

    await user.type(search, "new{Enter}");
    expect(search).toHaveValue("new");
    expect(screen.getByRole("checkbox", { name: "New York" })).not.toBeChecked();

    await user.clear(search);
    await user.type(search, "zz");
    expect(screen.getByRole("status")).toHaveTextContent("No state matches “zz”.");
  });

  it("selects District of Columbia on its own checkbox", async () => {
    const user = userEvent.setup();
    render(<Harness initial={[]} />);

    expect(screen.getByText("None selected")).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "Also appointed in District of Columbia" }));
    expect(screen.getByText("1 selected · District of Columbia")).toBeVisible();
  });
});

describe("appointed-state words", () => {
  it("names states in full and shortens a long selection", () => {
    expect(stateNames(OPTIONS, ["TX", "DC", "FL"])).toEqual(["District of Columbia", "Florida", "Texas"]);
    const many = OPTIONS.slice(0, 9).map((option) => option.code);
    expect(selectionSummary(OPTIONS, many)).toBe(
      "9 selected · Alabama, Alaska, Arizona, Arkansas, California, Colorado and 3 more",
    );
  });

  it("matches any part of a name, or the exact code", () => {
    const texas = { code: "TX", name: "Texas" };
    expect(matchesState(texas, " tex ")).toBe(true);
    expect(matchesState(texas, "TX")).toBe(true);
    expect(matchesState(texas, "x")).toBe(true);
    expect(matchesState({ code: "NY", name: "New York" }, "ny")).toBe(true);
    expect(matchesState({ code: "NY", name: "New York" }, "y")).toBe(true);
    expect(matchesState({ code: "OH", name: "Ohio" }, "oh")).toBe(true);
    expect(matchesState({ code: "OH", name: "Ohio" }, "h")).toBe(true);
    expect(matchesState({ code: "OH", name: "Ohio" }, "ky")).toBe(false);
  });
});
