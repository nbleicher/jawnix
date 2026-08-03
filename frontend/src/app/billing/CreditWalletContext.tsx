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

  const refresh = useCallback(async () => {
    try {
      const next = await loadCreditWallet();
      setWallet(next);
      return next;
    } catch {
      setWallet(null);
      return null;
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Keep polling while a Credit Purchase is processing so the balance updates
  // when the Stripe webhook lands — without a manual reload.
  useEffect(() => {
    if (!wallet?.billingEnabled || !hasProcessingPurchase(wallet)) return;
    const interval = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => window.clearInterval(interval);
  }, [wallet, refresh]);

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
