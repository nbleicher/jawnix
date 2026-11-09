import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { Field, Input } from "../../design-system/primitives/form";
import { Cluster, Stack } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import { useBilledWallet, useCreditWallet } from "./CreditWalletContext";
import { formatCents } from "./money";
import { usePurchaseReturnTo } from "./purchaseReturn";
import { hasProcessingPurchase, startCreditPurchase } from "./wallet";

import "./CreditWalletWidget.css";

export function PurchaseDialog({
  open,
  onClose,
  returnTo: explicitReturnTo,
}: {
  open: boolean;
  onClose: () => void;
  /** The portal path Checkout returns to; defaults to the current page. */
  returnTo?: string | undefined;
}) {
  const { refresh } = useCreditWallet();
  // Checkout comes back here, with any unfinished request form filled in.
  const pageReturnTo = usePurchaseReturnTo();
  const returnTo = explicitReturnTo ?? pageReturnTo;
  const [amount, setAmount] = useState("25");
  const [fieldError, setFieldError] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const formId = useId();
  const continueRef = useRef<HTMLButtonElement>(null);

  // Default focus lands on "Continue to Stripe": nothing is charged until
  // Stripe confirms, so forward focus is correct here. Dialog's own effects
  // run first (child-before-parent), so the modal is open when this fires.
  useEffect(() => {
    if (open) continueRef.current?.focus();
  }, [open]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const trimmed = amount.trim();
    if (!/^\d+$/.test(trimmed) || Number(trimmed) < 1) {
      setFieldError("Enter a whole-dollar amount of at least $1.");
      setFailure("");
      return;
    }
    setBusy(true);
    setFieldError("");
    setFailure("");
    try {
      const started = await startCreditPurchase(Number(trimmed), returnTo);
      await refresh();
      window.location.assign(started.checkoutUrl);
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "The Credit Purchase could not be started. Please try again.",
      );
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Buy credits"
      description="Top up your Credit Wallet with any whole-dollar amount. You will complete payment on Stripe Checkout; the balance updates when payment is confirmed."
      footer={
        <Cluster>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            busy={busy}
            busyLabel="Redirecting…"
            ref={continueRef}
          >
            Continue to Stripe
          </Button>
        </Cluster>
      }
    >
      <form id={formId} onSubmit={(event) => void submit(event)} noValidate>
        <Stack gap={4}>
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          <Field
            label="Amount in dollars"
            description="Whole dollars only. The minimum Credit Purchase is $1."
            required
            {...(fieldError ? { error: fieldError } : {})}
          >
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={amount}
              className="credit-purchase-amount"
              onChange={(event) => {
                setAmount(event.currentTarget.value);
                setFieldError("");
                setFailure("");
              }}
            />
          </Field>
        </Stack>
      </form>
    </Dialog>
  );
}

/**
 * Persistent Credit Wallet chrome for Billed Customers: the balance available
 * for new requests and a Buy credits action. Free Customers never render this.
 */
export function CreditWalletWidget() {
  const wallet = useBilledWallet();
  const [dialogOpen, setDialogOpen] = useState(false);

  if (!wallet) return null;

  const processing = hasProcessingPurchase(wallet);

  return (
    <>
      <div className="credit-wallet-widget" aria-label="Credit Wallet summary">
        <div className="credit-wallet-widget__balance">
          <span className="credit-wallet-widget__label">
            Balance
            {processing ? (
              <span className="credit-wallet-widget__processing">
                <span className="credit-wallet-widget__processing-mark" aria-hidden="true" />
                <span className="credit-wallet-widget__processing-text">Processing</span>
              </span>
            ) : null}
          </span>
          <span className="credit-wallet-widget__amount">
            {formatCents(wallet.availableBalanceCents)}
          </span>
        </div>
        <Button variant="primary" onClick={() => setDialogOpen(true)}>
          Buy credits
        </Button>
      </div>
      <PurchaseDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </>
  );
}
