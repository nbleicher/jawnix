import { useState } from "react";

import { api } from "../auth/adminMFA";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { Field, Input } from "../../design-system/primitives/form";
import { Cluster, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import { LabelText, Text } from "../../design-system/primitives/typography";
import { errorMessage } from "./AdminCustomers";
import type {
  CustomerDetailsData,
  UserAccountRecord,
} from "./AdminCustomerDetails";

import "./AdminCustomerDetails.css";

interface CustomerCore {
  id: number;
  slug: string;
  name: string;
  agency_id: number | null;
  active: boolean;
}

interface ChangedActionProps {
  onChanged: () => Promise<void> | void;
}

export function useActionDialog(onChanged: ChangedActionProps["onChanged"]) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  function show() {
    setFailure("");
    setOpen(true);
  }

  async function run(request: () => Promise<unknown>) {
    setBusy(true);
    setFailure("");
    try {
      await request();
      await onChanged();
      setOpen(false);
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return { open, busy, failure, show, close: () => setOpen(false), run };
}

export function RenameCustomerAction({
  customer,
  onChanged,
}: ChangedActionProps & { customer: CustomerCore }) {
  const dialog = useActionDialog(onChanged);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    void dialog.run(() =>
      api(`/api/admin/customers/${customer.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: String(fields.get("name") ?? "").trim(),
          agency_id: customer.agency_id,
          active: customer.active,
          reason: String(fields.get("reason") ?? "").trim(),
        }),
      }),
    );
  }

  return (
    <>
      <Button onClick={dialog.show}>Rename Customer</Button>
      <Dialog
        open={dialog.open}
        onClose={dialog.close}
        title="Rename Customer"
        description="The Customer keeps its Agency, status, access, and permanent history."
      >
        <form onSubmit={submit}>
          <Stack gap={4}>
            {dialog.failure ? (
              <Text role="alert" tone="danger" size="sm">
                {dialog.failure}
              </Text>
            ) : null}
            <Field label="Customer name" required>
              <Input name="name" defaultValue={customer.name} autoFocus />
            </Field>
            <Field label="Reason" required>
              <Input name="reason" autoComplete="off" />
            </Field>
            <Cluster gap={3}>
              <Button type="submit" variant="primary" busy={dialog.busy}>
                Rename Customer
              </Button>
              <Button onClick={dialog.close}>Cancel</Button>
            </Cluster>
          </Stack>
        </form>
      </Dialog>
    </>
  );
}

export function SendPasswordResetAction({
  account,
  onChanged,
}: ChangedActionProps & { account: UserAccountRecord }) {
  const dialog = useActionDialog(onChanged);

  return (
    <>
      <Button onClick={dialog.show}>Send password reset</Button>
      <ConfirmDialog
        open={dialog.open}
        onClose={dialog.close}
        onConfirm={() =>
          void dialog.run(() =>
            api(
              `/api/admin/user-accounts/${account.auth_user_id}/send-password-reset`,
              { method: "POST" },
            ),
          )
        }
        title="Send password reset"
        consequence={`A reset email goes to ${account.email}. Nothing else changes.`}
        confirmLabel="Send password reset"
        destructive={false}
        busy={dialog.busy}
      >
        {dialog.failure ? (
          <Text role="alert" tone="danger" size="sm">
            {dialog.failure}
          </Text>
        ) : null}
      </ConfirmDialog>
    </>
  );
}

export function CustomerLifecycleAction({
  customer,
  onChanged,
}: ChangedActionProps & { customer: CustomerCore }) {
  const dialog = useActionDialog(onChanged);
  const [reason, setReason] = useState("");

  const label = customer.active ? "Deactivate Customer" : "Reactivate Customer";
  return (
    <>
      <Button
        onClick={() => {
          setReason("");
          dialog.show();
        }}
      >
        {label}
      </Button>
      <ConfirmDialog
        open={dialog.open}
        onClose={dialog.close}
        onConfirm={() =>
          void dialog.run(() =>
            api(`/api/admin/customers/${customer.id}`, {
              method: "PATCH",
              body: JSON.stringify({
                name: customer.name,
                agency_id: customer.agency_id,
                active: !customer.active,
                reason,
              }),
            }),
          )
        }
        title={label}
        consequence={
          customer.active
            ? "Access stops immediately. Licensed States, Agency membership, and the permanent history are kept."
            : "Access is restored. Nothing in the permanent history changes."
        }
        confirmLabel={customer.active ? "Deactivate" : "Reactivate"}
        destructive={customer.active}
        busy={dialog.busy}
      >
        <Stack gap={3}>
          {dialog.failure ? (
            <Text role="alert" tone="danger" size="sm">
              {dialog.failure}
            </Text>
          ) : null}
          <Field label="Reason" required>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              autoComplete="off"
            />
          </Field>
        </Stack>
      </ConfirmDialog>
    </>
  );
}

export function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt>
        <LabelText>{label}</LabelText>
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

export function CustomerFloatingCard({
  details,
  onClose,
  onChanged,
}: {
  details: CustomerDetailsData;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const { customer, user_account: account, invitation } = details;
  return (
    <Dialog open onClose={onClose} title={customer.name}>
      <Stack gap={4}>
        <StatusBadge tone={customer.status.tone}>
          {customer.status.label}
        </StatusBadge>
        <dl className="admin-customer-details__facts">
          <Fact label="Slug">{customer.slug}</Fact>
          <Fact label="Agency">{customer.agency || "No Agency"}</Fact>
          <Fact label="Licensed States">
            {customer.licensed_states.length
              ? customer.licensed_states.join(", ")
              : "None yet"}
          </Fact>
          <Fact label="User Account">
            {account ? (
              <Cluster gap={2}>
                <span>{account.email}</span>
                <StatusBadge tone={account.active ? "success" : "warning"}>
                  {account.active ? "Active" : "Inactive"}
                </StatusBadge>
              </Cluster>
            ) : (
              "No User Account"
            )}
          </Fact>
        </dl>
        {invitation ? (
          <Text size="sm" tone="muted">
            Invitation pending for {invitation.email}.
          </Text>
        ) : null}
        <Cluster gap={3}>
          <RenameCustomerAction customer={customer} onChanged={onChanged} />
          <CustomerLifecycleAction customer={customer} onChanged={onChanged} />
          {account && !invitation ? (
            <SendPasswordResetAction account={account} onChanged={onChanged} />
          ) : null}
          <ActionLink href={`/app/admin/customers/${customer.id}`}>
            Open full record
          </ActionLink>
        </Cluster>
      </Stack>
    </Dialog>
  );
}
