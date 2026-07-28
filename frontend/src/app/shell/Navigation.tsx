import { NavLink } from "react-router";
import type { ReactNode } from "react";

import "./Navigation.css";
import { cx } from "../../design-system/primitives/cx";

export interface NavigationDestination {
  to: string;
  label: string;
  /** Decorative glyph. Never the only label — every destination keeps its text
   *  so the bar is readable at any width and by screen readers. */
  icon?: ReactNode;
}

export interface NavigationProps {
  /** Names the landmark, e.g. "Customer" or "Administration". */
  label: string;
  destinations: NavigationDestination[];
}

/**
 * Primary navigation.
 *
 * One landmark serves both form factors: CSS moves it between a mobile bottom
 * bar and a desktop sidebar. Rendering it twice would put duplicate navigation
 * landmarks in the accessibility tree and let the two copies drift.
 */
export function Navigation({ label, destinations }: NavigationProps) {
  return (
    <nav className="jx-nav" aria-label={label}>
      <ul className="jx-nav__list">
        {destinations.map((destination) => (
          <li key={destination.to} className="jx-nav__item">
            <NavLink
              to={destination.to}
              // `end` is deliberately off: a nested route such as
              // /app/requests/1234 still belongs to Requests. React Router
              // matches on path segments, so /app/requests-archive does not.
              className={({ isActive }) => cx("jx-nav__link", isActive ? "jx-nav__link--active" : null)}
            >
              {destination.icon ? (
                <span className="jx-nav__icon" aria-hidden="true">
                  {destination.icon}
                </span>
              ) : null}
              <span className="jx-nav__label">{destination.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
