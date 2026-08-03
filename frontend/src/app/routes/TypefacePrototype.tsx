/**
 * PROTOTYPE — three display-face candidates across the same sign-in,
 * Customer Overview, and data-dense administration table. Switch with
 * `?variant=fraunces|space-grotesk|bricolage-grotesque`.
 */
import { useEffect } from "react";
import { useSearchParams } from "react-router";

import "@fontsource-variable/fraunces";
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/bricolage-grotesque";

import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import "./TypefacePrototype.css";

const CANDIDATES = [
  {
    key: "fraunces",
    label: "Fraunces",
    note: "Editorial warmth · optical-size serif · expressive numerals",
    family: '"Fraunces Variable", Georgia, serif',
  },
  {
    key: "space-grotesk",
    label: "Space Grotesk",
    note: "Technical clarity · compact grotesk · disciplined numerals",
    family: '"Space Grotesk Variable", ui-sans-serif, sans-serif',
  },
  {
    key: "bricolage-grotesque",
    label: "Bricolage Grotesque",
    note: "Confident character · geometric grotesk · lively numerals",
    family: '"Bricolage Grotesque Variable", ui-sans-serif, sans-serif',
  },
] as const;

type Candidate = (typeof CANDIDATES)[number];

function SignInMockup() {
  return (
    <section className="type-prototype__frame type-prototype__frame--signin" aria-label="Sign-in mockup">
      <div className="type-prototype__signin-brand">Jawnix</div>
      <div className="type-prototype__auth-card">
        <p className="type-prototype__eyebrow">Customer portal</p>
        <h2>Welcome back.</h2>
        <p className="type-prototype__muted">
          Sign in to review requests, deliveries, and account details.
        </p>
        <label>
          <span>Email address</span>
          <input type="email" value="alex@harborinsurance.com" readOnly />
        </label>
        <label>
          <span>Password</span>
          <input type="password" value="opaline-preview" readOnly />
        </label>
        <button type="button">Sign in</button>
      </div>
      <div className="type-prototype__orb" aria-hidden="true" />
    </section>
  );
}

function OverviewMockup() {
  return (
    <section className="type-prototype__frame" aria-label="Customer Overview mockup">
      <header className="type-prototype__shell-bar">
        <span className="type-prototype__wordmark">Jawnix</span>
        <span>Customer</span>
        <span className="type-prototype__shell-action">Sign out</span>
      </header>
      <div className="type-prototype__shell-body">
        <nav aria-label="Mock customer navigation">
          <strong>◆ Overview</strong>
          <span>▤ Requests</span>
          <span>✎ Feedback</span>
          <span>◑ Account</span>
        </nav>
        <main>
          <p className="type-prototype__eyebrow">Friday, July 31</p>
          <h2>Overview</h2>
          <p className="type-prototype__muted">Three things need your attention.</p>
          <div className="type-prototype__metric-row">
            <div>
              <strong>3</strong>
              <span>open items</span>
            </div>
            <div>
              <strong>19,842</strong>
              <span>leads delivered</span>
            </div>
            <div>
              <strong>6 days</strong>
              <span>until next expiry</span>
            </div>
          </div>
          <article className="type-prototype__attention type-prototype__special">
            <div>
              <span className="type-prototype__status">Ready</span>
              <h3>Batch 1,284 is ready to download</h3>
              <p>9,842 verified leads · FL, GA, TX · expires August 6</p>
            </div>
            <button type="button">Download CSV</button>
          </article>
          <article className="type-prototype__attention">
            <div>
              <span className="type-prototype__status type-prototype__status--warning">Attention</span>
              <h3>Confirm two Setup Problems</h3>
              <p>One Licensed State and one account contact need review.</p>
            </div>
            <button type="button" className="type-prototype__secondary">Review account</button>
          </article>
        </main>
      </div>
    </section>
  );
}

const CUSTOMERS = [
  ["Harbor Insurance", "Atlas Group", "FL, GA, TX", "Active", "19,842", "Jul 31, 2026"],
  ["Northstar Benefits", "Independent", "AZ, CO, NV", "Attention", "8,416", "Jul 30, 2026"],
  ["Pine Street Agency", "Cobalt Partners", "NC, SC", "Active", "12,006", "Jul 29, 2026"],
  ["Summit Coverage", "Atlas Group", "CA, OR, WA", "Invited", "—", "Jul 28, 2026"],
  ["Union Standard", "Independent", "IL, IN, OH", "Active", "25,190", "Jul 27, 2026"],
  ["Westlake Advisors", "Cobalt Partners", "NY, NJ, PA", "Problem", "4,775", "Jul 27, 2026"],
];

function AdminTableMockup() {
  return (
    <section className="type-prototype__frame" aria-label="Data-dense administration table mockup">
      <header className="type-prototype__shell-bar">
        <span className="type-prototype__wordmark">Jawnix</span>
        <span>Administration</span>
        <span className="type-prototype__shell-action">NB</span>
      </header>
      <div className="type-prototype__admin">
        <div className="type-prototype__admin-heading">
          <div>
            <p className="type-prototype__eyebrow">Customer operations</p>
            <h2>Customers <span>127</span></h2>
            <p className="type-prototype__muted">Durable parties and their current account standing.</p>
          </div>
          <button type="button">Create Customer</button>
        </div>
        <div className="type-prototype__filters" aria-label="Mock filters">
          <span>Search customers…</span>
          <span>All statuses</span>
          <span>All agencies</span>
          <strong>127 results</strong>
        </div>
        <div className="type-prototype__table-wrap">
          <table>
            <thead>
              <tr>
                {['Customer', 'Agency', 'Licensed States', 'Standing', 'Delivered', 'Last activity'].map((heading) => (
                  <th key={heading} scope="col">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {CUSTOMERS.map((row) => (
                <tr key={row[0]}>
                  {row.map((cell, index) => (
                    <td key={`${row[0]}-${index}`}>
                      {index === 0 ? <strong>{cell}</strong> : cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <footer className="type-prototype__table-footer">
          <span>Rows 1–6 of 127</span>
          <span>01 / 22</span>
        </footer>
      </div>
    </section>
  );
}

function PrototypeSwitcher({ candidate }: { candidate: Candidate }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const index = CANDIDATES.findIndex((item) => item.key === candidate.key);

  function move(delta: number) {
    const next = CANDIDATES[(index + delta + CANDIDATES.length) % CANDIDATES.length]!;
    const updated = new URLSearchParams(searchParams);
    updated.set("variant", next.key);
    setSearchParams(updated, { replace: true });
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  return (
    <div className="type-prototype__switcher" aria-label="Typeface candidate switcher">
      <button type="button" onClick={() => move(-1)} aria-label="Previous typeface">←</button>
      <div aria-live="polite">
        <strong>{index + 1} / {CANDIDATES.length} — {candidate.label}</strong>
        <span>{candidate.note}</span>
      </div>
      <button type="button" onClick={() => move(1)} aria-label="Next typeface">→</button>
    </div>
  );
}

export function TypefacePrototypeRoute() {
  useRouteTheme("opaline", "jawnix");
  const [searchParams] = useSearchParams();
  const candidate = CANDIDATES.find((item) => item.key === searchParams.get("variant")) ?? CANDIDATES[0];

  return (
    <div
      className="type-prototype"
      style={{ "--type-prototype-display": candidate.family } as React.CSSProperties}
    >
      <header className="type-prototype__intro">
        <div>
          <p className="type-prototype__eyebrow">Prototype · issue #116</p>
          <h1>{candidate.label}</h1>
          <p>{candidate.note}. Interface copy stays neutral; only headings and data-bearing numerals change.</p>
        </div>
        <div className="type-prototype__sample" aria-label="Numeral sample">0123456789</div>
      </header>
      <SignInMockup />
      <OverviewMockup />
      <AdminTableMockup />
      <PrototypeSwitcher candidate={candidate} />
    </div>
  );
}
