import type { ReactNode } from "react";

import "./terminal.css";

export interface TerminalWorkspaceProps {
  status: string;
  children: ReactNode;
}

/**
 * GMS/OPS frame for native Scraper screens.
 *
 * The rail only links to working destinations. Later Scraper slices extend it
 * as their routes land, so an outage or an early foundation never exposes
 * partial controls.
 */
export function TerminalWorkspace({ status, children }: TerminalWorkspaceProps) {
  return (
    <section className="jx-terminal" aria-label="Scraper Operations terminal">
      <div className="jx-terminal__masthead">
        <span className="jx-terminal__identity">GMS / OPS</span>
        <span className="jx-terminal__status">{status}</span>
      </div>
      <div className="jx-terminal__grid">
        <nav className="jx-terminal__rail" aria-label="Scraper Operations">
          <span className="jx-terminal__label">Workspace</span>
          <ul className="jx-terminal__rail-list">
            <li>
              <a href="#scraper-access">Secure access</a>
            </li>
            <li>
              <a href="/app/admin/acquisition">Exit to Acquisition</a>
            </li>
          </ul>
        </nav>
        <div className="jx-terminal__content">{children}</div>
      </div>
    </section>
  );
}
