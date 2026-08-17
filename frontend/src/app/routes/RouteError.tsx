import { isRouteErrorResponse, useNavigate, useRouteError } from "react-router";

import { BrandLockup } from "../../brand/BrandLockup";
import { ErrorState } from "../../design-system/primitives/feedback";
import { Page } from "../../design-system/primitives/layout";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "../shell/AppShell.css";

/**
 * Route-level error element.
 *
 * Translates thrown responses and unexpected faults into plain language with a
 * recovery action. Raw messages are logged, never rendered — a backend detail
 * on screen is both unhelpful and a disclosure risk.
 */
export function RouteError() {
  const error = useRouteError();
  const navigate = useNavigate();

  const notFound = isRouteErrorResponse(error) && error.status === 404;
  const title = notFound ? "Page not found" : "Something went wrong";
  const description = notFound
    ? "That page does not exist, or it moved. The Overview has your current work."
    : "JAWNIX could not load this page. Retrying usually resolves it; if it keeps happening, contact an administrator.";

  useDocumentTitle(title);

  if (!isRouteErrorResponse(error)) {
    console.error("Unhandled route error", error);
  }

  return (
    <div className="jx-error-frame">
      <header className="jx-shell__banner">
        <BrandLockup />
      </header>
      <Page title={title}>
        <ErrorState
          title={title}
          description={description}
          retryLabel={notFound ? "Go to Overview" : "Try again"}
          onRetry={() => {
            if (notFound) {
              void navigate("/overview", { replace: true });
              return;
            }
            void navigate(0);
          }}
        />
      </Page>
    </div>
  );
}
