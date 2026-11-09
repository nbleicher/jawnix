import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useHref, useLocation } from "react-router";

/**
 * Where Stripe Checkout brings the Customer back to after Buy credits.
 *
 * By default that is the page the purchase started on. A page holding work
 * the URL does not carry — the unfinished request form — registers a fuller
 * path while it is mounted, so the Customer comes back to that work rather
 * than an empty form. The backend rebuilds the path from an allow-list, so
 * nothing registered here can send anyone off the portal.
 */

interface PurchaseReturnContextValue {
  registered: string | null;
  register: (path: string | null) => void;
}

const PurchaseReturnContext = createContext<PurchaseReturnContextValue | null>(
  null,
);

export function PurchaseReturnProvider({ children }: { children: ReactNode }) {
  const [registered, register] = useState<string | null>(null);
  return (
    <PurchaseReturnContext.Provider value={{ registered, register }}>
      {children}
    </PurchaseReturnContext.Provider>
  );
}

/** While mounted, a Credit Purchase returns to `path` instead of the bare page. */
export function usePurchaseReturnPath(path: string | null) {
  const register = useContext(PurchaseReturnContext)?.register;
  useEffect(() => {
    if (!register) return;
    register(path);
    return () => register(null);
  }, [register, path]);
}

/** The portal path a Credit Purchase started now should return to. */
export function usePurchaseReturnTo(): string {
  const registered = useContext(PurchaseReturnContext)?.registered ?? null;
  const location = useLocation();
  const current = useHref({ pathname: location.pathname, search: location.search });
  return registered ?? current;
}
