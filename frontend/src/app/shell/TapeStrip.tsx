import type { CreditWallet } from "../billing/wallet";
import { formatCents, formatLeadRate } from "../billing/money";
import { useBilledWallet } from "../billing/CreditWalletContext";
import "./TapeStrip.css";

export interface TapeEntry {
  label: string;
  /** A real figure from the Credit Wallet. Never invented: entries without a
   *  source figure carry no value at all. */
  value?: string;
}

/** Terse factual phrases — no adjectives, no marketing. */
const GENERIC_ENTRIES: TapeEntry[] = [
  { label: "JAWNIX" },
  { label: "LEAD LEDGER" },
  { label: "SETTLEMENT DAILY" },
  { label: "EVERY ROW RECONCILES" },
];

/**
 * The tape's content model. Billed Customers see their real figures quoted
 * between the brand entries; every other shell (and any shell where the
 * wallet context is absent or not Billed) gets the generic set only.
 */
export function buildTapeEntries(wallet: CreditWallet | null): TapeEntry[] {
  if (!wallet) return GENERIC_ENTRIES;
  const [brand, ...rest] = GENERIC_ENTRIES;
  const figures: TapeEntry[] = [
    { label: "AVAIL", value: formatCents(wallet.availableBalanceCents) },
  ];
  if (wallet.leadRateCentsPerThousand != null) {
    figures.push({
      label: "RATE",
      value: formatLeadRate(wallet.leadRateCentsPerThousand).replace(
        " per lead",
        "",
      ),
    });
  }
  return brand ? [brand, ...figures, ...rest] : [...figures, ...rest];
}

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
 * slow and calm — and is wholly decorative: the figures it quotes are already
 * exposed accessibly by the Credit Wallet chrome, so the entire strip is
 * aria-hidden and screen readers never hear it (nor its duplicate block).
 *
 * Motion contract: a CSS translateX loop over a doubled block; paused on
 * hover/focus-within; collapsed to a static strip under reduced motion.
 */
export function TapeStrip() {
  const wallet = useBilledWallet();
  const entries = buildTapeEntries(wallet);

  return (
    <div className="jx-tape" aria-hidden="true">
      <div className="jx-tape__track">
        <TapeBlock entries={entries} />
        <TapeBlock entries={entries} />
      </div>
    </div>
  );
}
