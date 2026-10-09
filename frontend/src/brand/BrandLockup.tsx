import { useTheme } from "../design-system/theme/ThemeProvider";
import "./BrandLockup.css";

/** Block-letter JX geometry on a 16×16 grid: the J stem and foot at x2–7,
 *  the X at x9–15. Combined with the square as evenodd subpaths, the letters
 *  knock out of the vermilion ground and show the surface beneath — paper in
 *  the light scheme, the warm desk in the dark scheme. */
const JX_STAMP_PATH =
  "M0 0H16V16H0Z " +
  "M2 11h3V3h2v10H2Z " +
  "M9 3h2l1 2.6 1-2.6h2l-2.1 5 2.1 5h-2l-1-2.6-1 2.6h-2l2.1-5Z";

/**
 * The wordmark-only lockup: the JX stamp (scheme control) + JAWNIX.
 * Nothing else belongs in the brand row — no audience, no Support.
 *
 * The vermilion hanko is the scheme toggle's vehicle and the one accent mark
 * allowed per view: a vermilion square with the JX letters knocked out to the
 * ground beneath. The aria-labels announce the action; the stamp itself never
 * animates.
 */
export function BrandLockup() {
  const { scheme, toggleScheme } = useTheme();
  const next = scheme === "dark" ? "light paper" : "dark desk";

  return (
    <div className="jx-lockup">
      <button
        type="button"
        className="jx-lockup__stamp"
        aria-pressed={scheme === "dark"}
        aria-label={`Switch to ${next}`}
        onClick={toggleScheme}
      >
        <JxStamp />
      </button>
      <span className="jx-lockup__wordmark">JAWNIX</span>
    </div>
  );
}

/** The vermilion JX square: letters knocked out of the stamp ground
 *  (fill-rule evenodd), rotated −3° by the CSS like a hanko impression. */
function JxStamp() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path
        fill="var(--jx-stamp-ground)"
        fillRule="evenodd"
        d={JX_STAMP_PATH}
      />
    </svg>
  );
}

/** The JX stamp at rest, for the static sign-in pane. Not a control. */
export function RoutingPlate({ label = "JAWNIX" }: { label?: string }) {
  return (
    <svg className="jx-routing-plate" viewBox="0 0 16 16" role="img" aria-label={label}>
      <path
        fill="var(--jx-stamp-ground)"
        fillRule="evenodd"
        d={JX_STAMP_PATH}
      />
    </svg>
  );
}
