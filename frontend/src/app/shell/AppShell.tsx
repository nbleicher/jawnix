import { Outlet, useNavigation } from "react-router";

import { BrandLockup } from "../../brand/BrandLockup";
import { ErrorBoundary } from "../../design-system/primitives/feedback";
import { Navigation } from "./Navigation";
import type { ReactNode } from "react";
import type { NavigationDestination } from "./Navigation";
import { RouteAnnouncer } from "./RouteAnnouncer";
import "./AppShell.css";

export interface AppShellProps {
  /** Names the navigation landmark and identifies the surface in the header. */
  audience: string;
  destinations: NavigationDestination[];
  headerActions?: ReactNode;
}

/**
 * The persistent frame around every routed screen.
 *
 * Owns the landmark structure (banner / navigation / main), the skip link, the
 * route-change announcement, and the global pending indicator, so no individual
 * route has to re-implement them.
 */
export function AppShell({ audience, destinations, headerActions }: AppShellProps) {
  const navigation = useNavigation();
  const isNavigating = navigation.state !== "idle";

  return (
    <div className="jx-shell">
      {/* First focusable element on the page (WCAG 2.4.1). */}
      <a className="jx-shell__skip-link" href="#jx-main">
        Skip to main content
      </a>

      <header className="jx-shell__banner">
        <BrandLockup />
        {headerActions ? (
          <div className="jx-shell__header-actions">{headerActions}</div>
        ) : null}
      </header>

      {/* Route transitions are progress, not an error; a determinate bar would
          lie about duration, so this is a non-announcing visual hint paired with
          the announcer below. */}
      <div
        className="jx-shell__progress"
        data-active={isNavigating ? "true" : undefined}
        aria-hidden="true"
      />

      <div className="jx-shell__body">
        <Navigation label={audience} destinations={destinations} />

        <main className="jx-shell__main" id="jx-main" tabIndex={-1}>
          <div className="jx-shell__content">
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
      </div>

      <RouteAnnouncer />
    </div>
  );
}
