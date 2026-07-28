import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Link,
  useLoaderData,
  useNavigate,
} from "react-router";

import {
  exchangeJawnixSession,
  routerPath,
} from "../auth/customerAuth";
import type {
  InvitationLoaderData,
  SignInLoaderData,
} from "../auth/customerAuth";
import {
  signInWithPassword,
  updateProviderPassword,
} from "../auth/providerSession";
import { Button } from "../../design-system/primitives/Button";
import { AuthPanel } from "../../design-system/primitives/auth";
import { Field, Input } from "../../design-system/primitives/form";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./CustomerAuth.css";

const SIGN_IN_ERROR =
  "We could not sign you in. Check your details or ask your administrator for help.";
const INVITATION_ERROR =
  "This invitation cannot be used. Ask your administrator for a new invitation, or sign in if you already set your password.";

function AuthRouteFrame({ children }: { children: ReactNode }) {
  useRouteTheme("jawnix");

  return (
    <div className="jx-auth-route">
      <a className="jx-auth-route__skip-link" href="#jx-auth-main">
        Skip to main content
      </a>
      <header className="jx-auth-route__banner">
        <span className="jx-auth-route__brand">Jawnix</span>
      </header>
      <main className="jx-auth-route__main" id="jx-auth-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

function FormError({
  children,
  errorRef,
}: {
  children: ReactNode;
  errorRef: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      className="jx-auth-form__error"
      ref={errorRef}
      role="alert"
      tabIndex={-1}
    >
      {children}
    </div>
  );
}

export function SignInRoute() {
  useDocumentTitle("Sign in");
  const { next } = useLoaderData() as SignInLoaderData;
  const navigate = useNavigate();
  const errorRef = useRef<HTMLDivElement>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const provider = await signInWithPassword(email.trim(), password);
      const session = await exchangeJawnixSession(provider.access_token, next);
      navigate(routerPath(session.next), { replace: true });
    } catch {
      setError(SIGN_IN_ERROR);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthRouteFrame>
      <AuthPanel
        title="Sign in"
        description="Use the email address and password for your Customer account."
      >
        <form className="jx-auth-form" onSubmit={submit} noValidate>
          {error ? <FormError errorRef={errorRef}>{error}</FormError> : null}
          <Field label="Email address" required>
            <Input
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              required
            />
          </Field>
          <Field label="Password" required>
            <Input
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              required
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            fullWidth
            busy={busy}
            busyLabel="Signing in…"
          >
            Sign in
          </Button>
        </form>
      </AuthPanel>
    </AuthRouteFrame>
  );
}

function InvitationRecovery({ focus = false }: { focus?: boolean }) {
  const recoveryRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focus) recoveryRef.current?.focus();
  }, [focus]);

  return (
    <AuthRouteFrame>
      <div
        className="jx-auth-recovery"
        ref={recoveryRef}
        role="alert"
        tabIndex={-1}
      >
        <AuthPanel
          title="Request a new invitation"
          description={INVITATION_ERROR}
          footer={
            <Link className="jx-auth-route__link" to="/sign-in">
              Go to sign in
            </Link>
          }
        >
          <p>Your administrator can send a replacement without needing your password.</p>
        </AuthPanel>
      </div>
    </AuthRouteFrame>
  );
}

export function AcceptInvitationRoute() {
  useDocumentTitle("Accept invitation");
  const { canAccept } = useLoaderData() as InvitationLoaderData;
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [confirmationError, setConfirmationError] = useState("");
  const [invitationFailed, setInvitationFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!canAccept || invitationFailed) {
    return <InvitationRecovery focus={invitationFailed} />;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (password.length < 8) {
      setPasswordError("Choose a password with at least 8 characters.");
      setConfirmationError("");
      return;
    }
    if (password !== confirmation) {
      setPasswordError("");
      setConfirmationError("The passwords do not match.");
      return;
    }
    setPasswordError("");
    setConfirmationError("");
    setBusy(true);
    try {
      const provider = await updateProviderPassword(password);
      const session = await exchangeJawnixSession(
        provider.access_token,
        "/app/overview",
      );
      navigate(routerPath(session.next), { replace: true });
    } catch {
      setInvitationFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthRouteFrame>
      <AuthPanel
        title="Create your password"
        description="Finish accepting your invitation. Your administrator cannot see or set this password."
      >
        <form className="jx-auth-form" onSubmit={submit} noValidate>
          <Field
            label="Password"
            description="Use at least 8 characters."
            error={passwordError}
            required
          >
            <Input
              name="password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              required
            />
          </Field>
          <Field
            label="Confirm password"
            error={confirmationError}
            required
          >
            <Input
              name="password_confirmation"
              type="password"
              autoComplete="new-password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.currentTarget.value)}
              required
            />
          </Field>
          <Button
            type="submit"
            variant="primary"
            fullWidth
            busy={busy}
            busyLabel="Creating password…"
          >
            Create password
          </Button>
        </form>
      </AuthPanel>
    </AuthRouteFrame>
  );
}
