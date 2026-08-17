import { Outlet } from "react-router";

import { BrandLockup } from "../../brand/BrandLockup";
import { ErrorBoundary } from "../../design-system/primitives/feedback";
import "./MFA.css";

export function MFAShell() {
  return (
    <div className="jx-mfa-shell">
      <a className="jx-shell__skip-link" href="#jx-main">
        Skip to main content
      </a>
      <header className="jx-mfa-shell__banner">
        <BrandLockup />
      </header>
      <main className="jx-mfa-shell__main" id="jx-main" tabIndex={-1}>
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
