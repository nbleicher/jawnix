import "./TapeStrip.css";

export interface TapeEntry {
  label: string;
  value?: string;
}

/**
 * Terse factual phrases — no adjectives, no marketing. The tape quotes no
 * figures: the header already shows the balance, and a quoted "AVAIL" or a
 * per-lead rate read as supply and broke the per-1,000 rule (V5, V6).
 */
export const TAPE_ENTRIES: TapeEntry[] = [
  { label: "JAWNIX" },
  { label: "LEAD LEDGER" },
  { label: "SETTLEMENT DAILY" },
  { label: "EVERY ROW RECONCILES" },
];

function TapeBlock({ entries }: { entries: TapeEntry[] }) {
  return (
    <span className="jx-tape__block">
      {entries.map((entry, index) => (
        <span className="jx-tape__entry" key={`${entry.label}-${index}`}>
          {index > 0 ? (
            <span className="jx-tape__separator" aria-hidden="true">
              ▪
            </span>
          ) : null}
          <span className="jx-tape__label">{entry.label}</span>
          {entry.value ? (
            <span className="jx-tape__value">{entry.value}</span>
          ) : null}
        </span>
      ))}
    </span>
  );
}

/**
 * The ticker tape: furniture, not content. It scrolls like a stock ticker —
 * slow and calm — and is wholly decorative, so the entire strip is
 * aria-hidden and screen readers never hear it (nor its duplicate block).
 * Phones hide it (V8).
 *
 * Motion contract: a CSS translateX loop over a doubled block; paused on
 * hover/focus-within; collapsed to a static strip under reduced motion.
 */
export function TapeStrip() {
  const entries = TAPE_ENTRIES;

  return (
    <div className="jx-tape" aria-hidden="true">
      <div className="jx-tape__track">
        <TapeBlock entries={entries} />
        <TapeBlock entries={entries} />
      </div>
    </div>
  );
}
