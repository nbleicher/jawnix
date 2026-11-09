import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useLoaderData, useNavigate } from "react-router";

import { exchangeJawnixSession, routerPath } from "../auth/customerAuth";
import type {
  InvitationLoaderData,
  PasswordResetLoaderData,
  SignInLoaderData,
} from "../auth/customerAuth";
import {
  signInWithPassword,
  updateProviderPassword,
  MFARequired,
  verifyProviderSignIn,
  requestProviderPasswordReset,
  EmailVerificationRequired,
  resendVerificationEmail,
} from "../auth/providerSession";
import { BrandLockup, RoutingPlate } from "../../brand/BrandLockup";
import { Button } from "../../design-system/primitives/Button";
import { AuthPanel } from "../../design-system/primitives/auth";
import { Field, Input } from "../../design-system/primitives/form";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import "./CustomerAuth.css";

const SIGN_IN_ERROR =
  "We could not sign you in. Check your details, or email noah@jawnix.com — Noah answers it.";
const INVITATION_ERROR =
  "This invitation cannot be used. Email noah@jawnix.com and I'll send a replacement, or sign in if you already set your password.";
const RESET_LINK_ERROR =
  "Password links work once and expire after an hour. Ask for a new one from sign-in.";

export function AuthRouteFrame({ children }: { children: ReactNode }) {
  return (
    <div className="jx-auth-route">
      <a className="jx-auth-route__skip-link" href="#jx-auth-main">
        Skip to main content
      </a>
      <header className="jx-auth-route__banner">
        <BrandLockup />
      </header>
      <div className="jx-auth-route__plate" aria-hidden="true">
        <RoutingPlate />
      </div>
      <main className="jx-auth-route__main" id="jx-auth-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

export function FormError({
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
  const { next, passwordUpdated } = useLoaderData() as SignInLoaderData;
  const navigate = useNavigate();
  const errorRef = useRef<HTMLDivElement>(null);
  const resetHintId = useId();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mfa, setMfa] = useState(false);
  const [recovery, setRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [resetSent, setResetSent] = useState(false);
  const [unverified, setUnverified] = useState(false);
  const [verificationSent, setVerificationSent] = useState(false);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const provider = mfa
        ? await verifyProviderSignIn(code.trim(), recovery)
        : await signInWithPassword(email.trim(), password);
      const session = await exchangeJawnixSession(provider.access_token, next);
      navigate(routerPath(session.next), { replace: true });
    } catch (caught) {
      if (caught instanceof MFARequired) {
        setMfa(true);
        setPassword("");
      } else if (caught instanceof EmailVerificationRequired) {
        setUnverified(true);
        setError("Verify your email before signing in. Check your inbox for the verification link.");
      } else
        setError(
          mfa
            ? "That code could not be verified. Check it and try again."
            : SIGN_IN_ERROR,
        );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthRouteFrame>
      <AuthPanel
        title={mfa ? "Verify your sign-in" : "Sign in"}
        description={
          mfa
            ? "Enter an authenticator code or one unused recovery code."
            : "Use the email address and password for your Jawnix account."
        }
        footer={!mfa ? <Link className="jx-auth-route__link" to="/sign-up">Create an account</Link> : undefined}
      >
        <form className="jx-auth-form" onSubmit={submit} noValidate>
          {passwordUpdated && !mfa ? (
            <p className="jx-auth-form__notice" role="status">
              Your password was changed. Sign in with your new password.
            </p>
          ) : null}
          {error ? <FormError errorRef={errorRef}>{error}</FormError> : null}
          {unverified ? <Button type="button" disabled={busy || !email.trim()} onClick={async () => {
            setBusy(true);
            try { await resendVerificationEmail(email.trim()); setVerificationSent(true); }
            catch { setError("Verification email is temporarily unavailable. Try again shortly."); }
            finally { setBusy(false); }
          }}>Resend verification email</Button> : null}
          {verificationSent ? <p role="status">If your account needs verification, a new link is on its way.</p> : null}
          {resetSent ? (
            <p role="status">
              If that address has a Jawnix account, a link to choose a new
              password is on its way.
            </p>
          ) : null}
          {mfa ? (
            <>
              <Field
                label={
                  recovery ? "Recovery code" : "Six-digit authenticator code"
                }
                required
              >
                <Input
                  name="code"
                  value={code}
                  onChange={(event) => setCode(event.currentTarget.value)}
                  autoComplete="one-time-code"
                  inputMode={recovery ? "text" : "numeric"}
                  required
                  autoFocus
                />
              </Field>
              <Button
                type="button"
                onClick={() => {
                  setRecovery(!recovery);
                  setCode("");
                }}
              >
                {recovery ? "Use authenticator" : "Use recovery code"}
              </Button>
            </>
          ) : (
            <>
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
            </>
          )}
          <Button
            type="submit"
            variant="primary"
            fullWidth
            busy={busy}
            busyLabel="Signing in…"
          >
            {mfa ? "Verify and sign in" : "Sign in"}
          </Button>
          {!mfa ? (
            <>
            <Button
              type="button"
              disabled={busy || !email.trim()}
              {...(!email.trim() ? { "aria-describedby": resetHintId } : {})}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await requestProviderPasswordReset(email.trim());
                  setResetSent(true);
                } catch {
                  setError("Password recovery is temporarily unavailable.");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Set or reset password
            </Button>
            {/* A disabled button says nothing on its own; this says why. */}
            {!email.trim() ? (
              <p className="jx-auth-form__hint" id={resetHintId}>
                Enter your email address above to get a link for setting a new
                password.
              </p>
            ) : null}
            </>
          ) : null}
        </form>
      </AuthPanel>
    </AuthRouteFrame>
  );
}

function LinkRecovery({
  title,
  description,
  focus = false,
  children,
}: {
  title: string;
  description: string;
  focus?: boolean;
  children: ReactNode;
}) {
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
          title={title}
          description={description}
          footer={
            <Link className="jx-auth-route__link" to="/sign-in">
              Go to sign in
            </Link>
          }
        >
          {children}
        </AuthPanel>
      </div>
    </AuthRouteFrame>
  );
}

/**
 * Choose and confirm a password against a one-time provider link. Shared by
 * invitation acceptance and password reset, which differ only in their words.
 */
function NewPasswordForm({
  submitLabel,
  busyLabel,
  onFailed,
}: {
  submitLabel: string;
  busyLabel: string;
  onFailed: () => void;
}) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [confirmationError, setConfirmationError] = useState("");
  const [busy, setBusy] = useState(false);

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
      await updateProviderPassword(password);
      navigate("/sign-in?password=updated", { replace: true });
    } catch {
      onFailed();
    } finally {
      setBusy(false);
    }
  }

  return (
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
      <Field label="Confirm password" error={confirmationError} required>
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
        busyLabel={busyLabel}
      >
        {submitLabel}
      </Button>
    </form>
  );
}

export function AcceptInvitationRoute() {
  useDocumentTitle("Accept invitation");
  const { canAccept } = useLoaderData() as InvitationLoaderData;
  const [invitationFailed, setInvitationFailed] = useState(false);

  if (!canAccept || invitationFailed) {
    return (
      <LinkRecovery
        title="Request a new invitation"
        description={INVITATION_ERROR}
        focus={invitationFailed}
      >
        <p>
          A replacement goes to the same address the invitation left from. I
          never see your password.
        </p>
      </LinkRecovery>
    );
  }

  return (
    <AuthRouteFrame>
      <AuthPanel
        title="Create your password"
        description="Finish accepting your invitation. Your administrator cannot see or set this password."
      >
        <NewPasswordForm
          submitLabel="Create password"
          busyLabel="Creating password…"
          onFailed={() => setInvitationFailed(true)}
        />
      </AuthPanel>
    </AuthRouteFrame>
  );
}

/** Where a password link from sign-in lands. Never invitation words. */
export function ResetPasswordRoute() {
  useDocumentTitle("Choose a new password");
  const { canReset } = useLoaderData() as PasswordResetLoaderData;
  const [resetFailed, setResetFailed] = useState(false);

  if (!canReset || resetFailed) {
    return (
      <LinkRecovery
        title="This password link has expired"
        description={RESET_LINK_ERROR}
        focus={resetFailed}
      >
        <p>
          On sign-in, enter your email address and choose Set or reset
          password. Your current password still works until you change it.
        </p>
      </LinkRecovery>
    );
  }

  return (
    <AuthRouteFrame>
      <AuthPanel
        title="Choose a new password"
        description="This replaces the password for your Jawnix account. Nobody at Jawnix can see it."
      >
        <NewPasswordForm
          submitLabel="Save new password"
          busyLabel="Saving password…"
          onFailed={() => setResetFailed(true)}
        />
      </AuthPanel>
    </AuthRouteFrame>
  );
}
