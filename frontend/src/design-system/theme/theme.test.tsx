import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  SCHEME_STORAGE_KEY,
  ThemeProvider,
  useTheme,
} from "./ThemeProvider";

function ThemeProbe() {
  const { theme, scheme, setScheme, toggleScheme } = useTheme();
  return (
    <>
      <span data-testid="theme">{theme}</span>
      <span data-testid="scheme">{scheme}</span>
      <button type="button" onClick={() => setScheme("dark")}>
        Go dark
      </button>
      <button type="button" onClick={toggleScheme}>
        Toggle scheme
      </button>
    </>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.removeAttribute("data-scheme");
    window.localStorage.removeItem(SCHEME_STORAGE_KEY);
  });

  it("defaults to match + light", () => {
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("theme")).toHaveTextContent("match");
    expect(screen.getByTestId("scheme")).toHaveTextContent("light");
    expect(document.documentElement).toHaveAttribute("data-theme", "match");
    expect(document.documentElement).toHaveAttribute("data-scheme", "light");
  });

  it("restores a stored dark scheme", () => {
    window.localStorage.setItem(SCHEME_STORAGE_KEY, "dark");
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId("scheme")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveAttribute("data-scheme", "dark");
  });

  it("persists a scheme change", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Go dark" }));

    expect(screen.getByTestId("scheme")).toHaveTextContent("dark");
    expect(document.documentElement).toHaveAttribute("data-scheme", "dark");
    expect(window.localStorage.getItem(SCHEME_STORAGE_KEY)).toBe("dark");
  });

  it("toggles light and dark", async () => {
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <ThemeProbe />
      </ThemeProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Toggle scheme" }));
    expect(screen.getByTestId("scheme")).toHaveTextContent("dark");

    await user.click(screen.getByRole("button", { name: "Toggle scheme" }));
    expect(screen.getByTestId("scheme")).toHaveTextContent("light");
  });

  it("throws a directed error when used outside a provider", () => {
    const consoleError = console.error;
    console.error = () => {};
    expect(() => render(<ThemeProbe />)).toThrow(
      /useTheme must be used within a ThemeProvider/,
    );
    console.error = consoleError;
  });
});
