import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ThemeProvider, useRouteTheme, useTheme } from "./ThemeProvider";
import type { Theme } from "./ThemeProvider";

function ThemeProbe() {
  const { theme, setTheme } = useTheme();
  return (
    <>
      <span data-testid="current">{theme}</span>
      <button type="button" onClick={() => setTheme("terminal")}>
        Go terminal
      </button>
    </>
  );
}

function RouteThemeProbe({ theme }: { theme: Theme }) {
  useRouteTheme(theme);
  return <ThemeProbe />;
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("defaults to the jawnix product theme", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("current")).toHaveTextContent("jawnix");
  });

  it("projects the active theme onto the document element so CSS tokens swap", () => {
    render(
      <ThemeProvider defaultTheme="terminal">
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(document.documentElement).toHaveAttribute("data-theme", "terminal");
  });

  it("accepts the Opaline customer theme", () => {
    render(
      <ThemeProvider defaultTheme="opaline">
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("current")).toHaveTextContent("opaline");
    expect(document.documentElement).toHaveAttribute("data-theme", "opaline");
  });

  it("switches theme at runtime", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Go terminal" }));

    expect(screen.getByTestId("current")).toHaveTextContent("terminal");
    expect(document.documentElement).toHaveAttribute("data-theme", "terminal");
  });

  it("throws a directed error when used outside a provider", () => {
    // React logs the boundary error; silence it for this expected throw.
    const consoleError = console.error;
    console.error = () => {};
    expect(() => render(<ThemeProbe />)).toThrow(/useTheme must be used within a ThemeProvider/);
    console.error = consoleError;
  });
});

describe("useRouteTheme", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  it("applies the theme the route declares", () => {
    render(
      <ThemeProvider>
        <RouteThemeProbe theme="terminal" />
      </ThemeProvider>,
    );

    expect(document.documentElement).toHaveAttribute("data-theme", "terminal");
  });

  it("follows the declaration when the route changes", () => {
    const { rerender } = render(
      <ThemeProvider>
        <RouteThemeProbe theme="terminal" />
      </ThemeProvider>,
    );
    expect(document.documentElement).toHaveAttribute("data-theme", "terminal");

    rerender(
      <ThemeProvider>
        <RouteThemeProbe theme="jawnix" />
      </ThemeProvider>,
    );

    expect(document.documentElement).toHaveAttribute("data-theme", "jawnix");
  });

  it("does not fight a deliberate change within the same route", async () => {
    // Regression guard: the previous implementation re-asserted the route theme
    // on every render, so any other control that set the theme was reverted
    // before the user saw it.
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <RouteThemeProbe theme="jawnix" />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Go terminal" }));

    expect(screen.getByTestId("current")).toHaveTextContent("terminal");
    expect(document.documentElement).toHaveAttribute("data-theme", "terminal");
  });
});
