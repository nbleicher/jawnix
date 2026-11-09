import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { Field, Input } from "../../design-system/primitives/form";
import { Section } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import {
  PasswordChangeError,
  changeProviderPassword,
  requestProviderPasswordReset,
} from "../auth/providerSession";

const MIN_LENGTH = 8;

interface FieldErrors {
  current?: string;
  next?: string;
  confirm?: string;
}

/** A reset link by email: for a forgotten password, or a sign-in too old to change it. */
function ResetLink({ email, lead }: { email: string; lead: string }) {
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [failure, setFailure] = useState("");

  async function send() {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      await requestProviderPasswordReset(email);
      setSent(true);
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : "Try again shortly.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account-password__reset">
      {sent ? (
        <p className="account-tab__message account-tab__message--success" role="status">
          {`We sent a link to ${email}. It expires in one hour.`}
        </p>
      ) : (
        <>
          <Text size="sm">{lead}</Text>
          <div>
            <Button variant="secondary" busy={busy} busyLabel="Sending…" onClick={() => void send()}>
              Email me a reset link
            </Button>
          </div>
        </>
      )}
      {failure ? (
        <p className="account-tab__message account-tab__message--error" role="alert">
          {failure}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Account's Password tab. The password lives with the sign-in provider, so
 * the change goes there with the current password; Jawnix never sees either.
 */
export function CustomerAccountPassword({ email }: { email: string }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [failure, setFailure] = useState<PasswordChangeError | null>(null);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);

  function edit(set: (value: string) => void) {
    return (value: string) => {
      setChanged(false);
      setFailure(null);
      setErrors({});
      set(value);
    };
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const found: FieldErrors = {};
    if (!current) found.current = "Enter your current password.";
    if (next.length < MIN_LENGTH) found.next = `Use at least ${MIN_LENGTH} characters.`;
    else if (next === current) found.next = "Choose a password different from your current one.";
    if (!found.next && confirm !== next) found.confirm = "The two new passwords do not match.";
    setErrors(found);
    if (Object.keys(found).length) return;
    setBusy(true);
    setFailure(null);
    try {
      await changeProviderPassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setChanged(true);
    } catch (caught) {
      if (caught instanceof PasswordChangeError && caught.reason === "wrong_password") {
        setErrors({ current: caught.message });
      } else {
        setFailure(
          caught instanceof PasswordChangeError
            ? caught
            : new PasswordChangeError("unavailable", "Your password could not be changed. Please try again."),
        );
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Section title="Change password" description="You stay signed in here. Other devices are signed out.">
        <form className="account-form account-password" onSubmit={(event) => void submit(event)} noValidate>
          <input type="text" name="username" autoComplete="username" value={email} readOnly hidden />
          <Field label="Current password" {...(errors.current ? { error: errors.current } : {})}>
            <Input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => edit(setCurrent)(event.currentTarget.value)}
            />
          </Field>
          <Field
            label="New password"
            description={`At least ${MIN_LENGTH} characters.`}
            {...(errors.next ? { error: errors.next } : {})}
          >
            <Input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => edit(setNext)(event.currentTarget.value)}
            />
          </Field>
          <Field label="Confirm new password" {...(errors.confirm ? { error: errors.confirm } : {})}>
            <Input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => edit(setConfirm)(event.currentTarget.value)}
            />
          </Field>
          {changed ? (
            <p className="account-tab__message account-tab__message--success" role="status">
              Password changed. Use it the next time you sign in.
            </p>
          ) : null}
          {failure ? (
            <p className="account-tab__message account-tab__message--error" role="alert">
              {failure.reason === "session"
                ? "For your security, changing your password needs a recent sign-in. Use a reset link instead."
                : failure.message}
            </p>
          ) : null}
          <div>
            <Button type="submit" variant="primary" busy={busy} busyLabel="Changing…">
              Change password
            </Button>
          </div>
        </form>
      </Section>
      <Section title="Forgot your password?">
        <ResetLink
          email={email}
          lead={`We can email a link to ${email} so you can set a new password without the current one.`}
        />
      </Section>
    </>
  );
}
