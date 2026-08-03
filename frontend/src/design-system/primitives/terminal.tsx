import type { ReactNode } from "react";

import "./terminal.css";

export interface TerminalDestination {
  label: string;
  href: string;
  /** Marks the destination the reader is currently on. */
  current?: boolean;
}

export interface TerminalWorkspaceProps {
  status: string;
  /** Reflects the status in the masthead indicator. Words carry the meaning;
   *  tone only reinforces them, so an offline frame never reads as healthy. */
  tone?: "online" | "warning" | "offline";
  /** Names the frame for assistive technology. Overridden when a page hosts a
   *  workspace that is not Scraper Operations, so two frames never collide. */
  label?: string;
  /** Rail contents. Defaults to the original Scraper Operations pair so the
   *  routes that predate multi-destination rails keep working unchanged. */
  destinations?: TerminalDestination[];
  children: ReactNode;
}

const SCRAPER_DESTINATIONS: TerminalDestination[] = [
  {
    label: "Scraper Operations",
    href: "/app/admin/acquisition/scraper",
    current: true,
  },
  { label: "Exit to Acquisition", href: "/app/admin/acquisition" },
];

/**
 * GMS/OPS frame for native acquisition screens, inheriting the Opaline app
 * theme while retaining a compact operational identity.
 *
 * The rail only links to working destinations, so an outage or an early
 * foundation never exposes partial controls.
 */
export function TerminalWorkspace({
  status,
  tone = "online",
  label = "Scraper Operations workspace",
  destinations = SCRAPER_DESTINATIONS,
  children,
}: TerminalWorkspaceProps) {
  return (
    <section className="jx-terminal" aria-label={label}>
      <div className="jx-terminal__masthead">
        <span className="jx-terminal__identity">GMS / OPS</span>
        <span className={`jx-terminal__status jx-terminal__status--${tone}`}>
          {status}
        </span>
      </div>
      <div className="jx-terminal__grid">
        <nav className="jx-terminal__rail" aria-label={`${label} sections`}>
          <span className="jx-terminal__label">Workspace</span>
          <ul className="jx-terminal__rail-list">
            {destinations.map((destination) => (
              <li key={destination.href}>
                <a
                  href={destination.href}
                  {...(destination.current ? { "aria-current": "page" } : {})}
                >
                  {destination.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="jx-terminal__content">{children}</div>
      </div>
    </section>
  );
}
