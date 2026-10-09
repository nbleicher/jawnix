import { createContext, use, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * Light and dark are schemes of one system: paper and desk.
 * The J tile in the lockup is the control; routes do not pin a scheme.
 */
export type Scheme = "light" | "dark";

export const SCHEME_STORAGE_KEY = "jx-scheme";

interface ThemeContextValue {
  scheme: Scheme;
  setScheme: (scheme: Scheme) => void;
  toggleScheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export interface ThemeProviderProps {
  children: ReactNode;
  /** Tests pin a scheme. Production reads localStorage and defaults to light. */
  defaultScheme?: Scheme;
}

function readStoredScheme(): Scheme {
  try {
    return window.localStorage.getItem(SCHEME_STORAGE_KEY) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function ThemeProvider({
  children,
  defaultScheme,
}: ThemeProviderProps) {
  const [scheme, setSchemeState] = useState<Scheme>(
    () => defaultScheme ?? readStoredScheme(),
  );

  useEffect(() => {
    const root = document.documentElement;
    root.setAttribute("data-scheme", scheme);
    try {
      window.localStorage.setItem(SCHEME_STORAGE_KEY, scheme);
    } catch {
      // Private mode can refuse storage; the in-memory scheme still applies.
    }
  }, [scheme]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      scheme,
      setScheme: setSchemeState,
      toggleScheme: () =>
        setSchemeState((current) => (current === "dark" ? "light" : "dark")),
    }),
    [scheme],
  );

  return <ThemeContext value={value}>{children}</ThemeContext>;
}

export function useTheme(): ThemeContextValue {
  const context = use(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within a ThemeProvider.");
  }
  return context;
}
