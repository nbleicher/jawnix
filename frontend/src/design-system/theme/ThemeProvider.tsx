import { createContext, use, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * `jawnix` is the main product language; `terminal` is the dark GMS/OPS
 * acquisition workspace. Both resolve the same token contract in tokens.css.
 */
export type Theme = "jawnix" | "terminal";

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export interface ThemeProviderProps {
  children: ReactNode;
  defaultTheme?: Theme;
}

export function ThemeProvider({ children, defaultTheme = "jawnix" }: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(defaultTheme);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const value = useMemo(() => ({ theme, setTheme }), [theme]);

  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const context = use(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider.");
  }
  return context;
}

/**
 * Declares the theme a route requires.
 *
 * Theme is a property of the surface, not a user preference: the Customer
 * portal is always the product language and Acquisition is always the terminal
 * workspace. Expressing that as a declaration keeps the policy in one place —
 * an earlier version scattered the same `useEffect` across each shell *and*
 * offered a manual toggle, which the effect silently reverted on the next
 * render.
 */
export function useRouteTheme(theme: Theme): void {
  const { setTheme } = useTheme();

  useEffect(() => {
    setTheme(theme);
  }, [theme, setTheme]);
}
