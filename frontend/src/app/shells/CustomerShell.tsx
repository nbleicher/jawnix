import { AppShell } from "../shell/AppShell";
import { CreditWalletProvider } from "../billing/CreditWalletContext";
import { CreditWalletWidget } from "../billing/CreditWalletWidget";
import { PurchaseReturnNotice } from "../billing/PurchaseReturnNotice";
import { PurchaseReturnProvider } from "../billing/purchaseReturn";
import { CustomerSessionActions } from "../customer/CustomerSessionActions";
import { customerDestinations } from "../customer/navigation";

import "./CustomerShell.css";

export function CustomerShell() {
  return (
    <CreditWalletProvider>
      <PurchaseReturnProvider>
        {/* The Customer frame's phone header (V8) hangs off this class. */}
        <div className="customer-shell">
          <AppShell
            audience="Customer"
            destinations={customerDestinations()}
            notice={<PurchaseReturnNotice />}
            headerActions={
              <>
                <CreditWalletWidget />
                <span className="customer-shell__session">
                  <CustomerSessionActions />
                </span>
              </>
            }
          />
        </div>
      </PurchaseReturnProvider>
    </CreditWalletProvider>
  );
}
