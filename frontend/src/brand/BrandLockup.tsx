import { useTheme } from "../design-system/theme/ThemeProvider";
import "./BrandLockup.css";

/**
 * Lockup A: the plate (scheme control) + JAWNIX.
 * Nothing else belongs in the brand row — no audience, no Support.
 */
export function BrandLockup() {
  const { scheme, toggleScheme } = useTheme();
  const next = scheme === "dark" ? "light paper" : "dark desk";

  return (
    <div className="jx-lockup">
      <button
        type="button"
        className="jx-lockup__plate"
        aria-pressed={scheme === "dark"}
        aria-label={`Switch to ${next}`}
        onClick={toggleScheme}
      >
        <PlateMark />
      </button>
      <span className="jx-lockup__wordmark">JAWNIX</span>
    </div>
  );
}

/** mark-sm geometry: 0.75M walls, bid hole r=6 at 52,24. */
function PlateMark() {
  return (
    <svg viewBox="0 0 116.8 52.8" aria-hidden="true">
      <g transform="translate(2.4 2.4)">
        <path
          className="jx-plate__fill"
          fillRule="evenodd"
          d="M0 0h112v48H0z M6 6h28v36H6z M58 24a6 6 0 1 1-12 0a6 6 0 1 1 12 0z"
        />
        <rect
          className="jx-plate__line"
          x="0"
          y="0"
          width="112"
          height="48"
          fill="none"
          strokeWidth="2.4"
        />
        <rect className="jx-plate__wash" x="6" y="6" width="28" height="36" />
        <rect
          className="jx-plate__ping"
          x="6"
          y="6"
          width="28"
          height="36"
          fill="none"
          strokeWidth="2.4"
        />
        <circle
          className="jx-plate__bid"
          cx="52"
          cy="24"
          r="6"
          fill="none"
          strokeWidth="2.4"
        />
      </g>
    </svg>
  );
}

/** Full plate for the static sign-in pane. Not a control. */
export function RoutingPlate({ label = "JAWNIX routing plate" }: { label?: string }) {
  return (
    <svg className="jx-routing-plate" viewBox="0 0 116.8 52.8" role="img" aria-label={label}>
      <g transform="translate(2.4 2.4)">
        <path
          className="jx-plate__fill"
          fillRule="evenodd"
          d="M0 0h112v48H0z M4 4h32v40H4z M60 24a8 8 0 1 1-16 0a8 8 0 1 1 16 0z"
        />
        <rect
          className="jx-plate__line"
          x="0"
          y="0"
          width="112"
          height="48"
          fill="none"
          strokeWidth="2.4"
        />
        <rect
          className="jx-plate__ping"
          x="4"
          y="4"
          width="32"
          height="40"
          fill="none"
          strokeWidth="2.4"
        />
        <circle
          className="jx-plate__bid"
          cx="52"
          cy="24"
          r="8"
          fill="none"
          strokeWidth="2.4"
        />
      </g>
    </svg>
  );
}
