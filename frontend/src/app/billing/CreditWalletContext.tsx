import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  hasProcessingPurchase,
  loadCreditWallet,
  type CreditWallet,
} from "./wallet";

const POLL_MS = 2_500;
/** Ceiling for the retry backoff, so an outage costs one request a minute. */
const MAX_RETRY_MS = 60_000;
/** A Stripe callback lands in seconds. Past this the checkout was abandoned
 *  and only Stripe's own expiry will settle it, so watching stops rather than
 *  polling for the rest of the session. A deliberate refresh starts it again. */
const MAX_POLLS = 120;

interface CreditWalletContextValue {
  /** Null until the first load finishes; then the wallet or null if unmapped. */
  wallet: CreditWallet | null;
  /** True once the first billing fetch has settled. */
  ready: boolean;
  refresh: () => Promise<CreditWallet | null>;
}

const CreditWalletContext = createContext<CreditWalletContextValue | null>(
  null,
);

export function CreditWalletProvider({ children }: { children: ReactNode }) {
  const [wallet, setWallet] = useState<CreditWallet | null>(null);
  const [ready, setReady] = useState(false);
  const [retryPending, setRetryPending] = useState(false);
  const [schedule, setSchedule] = useState({ polls: 0, failures: 0 });

  const load = useCallback(async () => {
    try {
      const next = await loadCreditWallet();
      setWallet(next);
      setRetryPending(false);
      setSchedule((current) => ({ ...current, failures: 0 }));
      return next;
    } catch {
      // A transient billing failure must not erase a known wallet or stop the
      // processing-purchase poll. Keep the last confirmed view and retry on
      // the next interval/manual refresh.
      setRetryPending(true);
      setSchedule((current) => ({
        ...current,
        failures: current.failures + 1,
      }));
      return null;
    } finally {
      setReady(true);
    }
  }, []);

  // Someone asking for the wallet is a fresh reason to watch it, so a manual
  // refresh restarts both the backoff and the poll budget.
  const refresh = useCallback(async () => {
    setSchedule({ polls: 0, failures: 0 });
    return load();
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep polling while a Credit Purchase is processing so the balance updates
  // when the Stripe webhook lands — without a manual reload. Failures back off
  // so an outage is not hammered every 2.5 seconds for the whole session.
  useEffect(() => {
    const processing =
      Boolean(wallet?.billingEnabled) &&
      Boolean(wallet && hasProcessingPurchase(wallet));
    if (!retryPending && !processing) return;
    if (schedule.polls >= MAX_POLLS) return;
    const delay = retryPending
      ? Math.min(
          POLL_MS * 2 ** Math.max(0, schedule.failures - 1),
          MAX_RETRY_MS,
        )
      : POLL_MS;
    const timer = window.setTimeout(() => {
      setSchedule((current) => ({ ...current, polls: current.polls + 1 }));
      void load();
    }, delay);
    return () => window.clearTimeout(timer);
  }, [wallet, load, retryPending, schedule]);

  return (
    <CreditWalletContext.Provider value={{ wallet, ready, refresh }}>
      {children}
    </CreditWalletContext.Provider>
  );
}

const DORMANT: CreditWalletContextValue = {
  wallet: null,
  ready: true,
  refresh: async () => null,
};

export function useCreditWallet(): CreditWalletContextValue {
  return useContext(CreditWalletContext) ?? DORMANT;
}

/** Wallet for a Billed Customer, or null when billing UI must stay hidden. */
export function useBilledWallet(): CreditWallet | null {
  const { wallet, ready } = useCreditWallet();
  if (!ready || wallet === null || !wallet.billingEnabled) return null;
  return wallet;
}
