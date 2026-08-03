import { Outlet } from "react-router";

import { ErrorBoundary } from "../../design-system/primitives/feedback";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import "./MFA.css";

export function MFAShell() {
  useRouteTheme("opaline");

  return (
    <div className="jx-mfa-shell">
      <a className="jx-shell__skip-link" href="#jx-main">
        Skip to main content
      </a>
      <header className="jx-mfa-shell__banner">
        <span className="jx-mfa-shell__wordmark">Jawnix</span>
        <span className="jx-mfa-shell__audience">Administrator verification</span>
      </header>
      <main className="jx-mfa-shell__main" id="jx-main" tabIndex={-1}>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
