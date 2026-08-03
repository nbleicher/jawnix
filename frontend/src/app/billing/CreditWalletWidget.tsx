import { useId, useState, type FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { Field, Input } from "../../design-system/primitives/form";
import { Cluster, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { Text } from "../../design-system/primitives/typography";
import { useBilledWallet, useCreditWallet } from "./CreditWalletContext";
import { formatCents } from "./money";
import { hasProcessingPurchase, startCreditPurchase } from "./wallet";

import "./CreditWalletWidget.css";

function PurchaseDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { refresh } = useCreditWallet();
  const [amount, setAmount] = useState("25");
  const [fieldError, setFieldError] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const formId = useId();

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
      const started = await startCreditPurchase(Number(trimmed));
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
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            busy={busy}
            busyLabel="Redirecting…"
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
              autoFocus
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
 * Persistent Credit Wallet chrome for Billed Customers: available balance and
 * a Buy credits action. Free Customers never render this.
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
          <span className="credit-wallet-widget__label">Available</span>
          <span className="credit-wallet-widget__amount">
            {formatCents(wallet.availableBalanceCents)}
          </span>
          {processing ? (
            <StatusBadge tone="info">Processing</StatusBadge>
          ) : null}
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
