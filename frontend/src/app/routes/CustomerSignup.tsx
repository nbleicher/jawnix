import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, redirect, useLoaderData, useNavigate, useRevalidator } from "react-router";
import { AuthPanel } from "../../design-system/primitives/auth";
import { Button } from "../../design-system/primitives/Button";
import { Field, Input } from "../../design-system/primitives/form";
import { exchangeJawnixSession, routerPath, signOut } from "../auth/customerAuth";
import { getProviderSession, resendVerificationEmail, signUpWithPassword } from "../auth/providerSession";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { AuthRouteFrame, FormError } from "./CustomerAuth";
import "./CustomerSignup.css";

interface StateOption { code: string; name: string }
export interface RegistrationStatus { status: "active" | "awaiting_review" | "rejected"; next: string }

export async function signupOptionsLoader(): Promise<{ states: StateOption[] }> {
  const response = await fetch("/api/auth/signup-options");
  if (!response.ok) throw new Response("Signup is temporarily unavailable.", { status: response.status });
  return response.json();
}
export async function registrationStatusLoader(): Promise<RegistrationStatus> {
  const response = await fetch("/api/me/registration", { credentials: "same-origin" });
  if (response.status === 401) throw redirect("/sign-in");
  if (!response.ok) throw new Response("Account status is temporarily unavailable.", { status: response.status });
  const data = await response.json() as RegistrationStatus;
  if (data.status === "active") throw redirect(routerPath(data.next));
  return data;
}

export function CustomerSignupRoute() {
  useDocumentTitle("Create account");
  const { states } = useLoaderData() as { states: StateOption[] };
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [resent, setResent] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!chosen.length) { setError("Select at least one appointed state."); return; }
    setBusy(true); setError("");
    try {
      await signUpWithPassword(email.trim(), password, { first_name: first.trim(), last_name: last.trim(), phone, licensed_states: chosen });
      setPassword(""); setSent(true);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Signup is temporarily unavailable."); }
    finally { setBusy(false); }
  }
  return <AuthRouteFrame><AuthPanel title={sent ? "Check your email" : "Create your account"}
    description={sent ? "Open the verification link to finish creating your account." : "Enter your details and the states where you are appointed."}
    footer={<Link className="jx-auth-route__link" to="/sign-in">Already registered? Sign in</Link>}>
    {sent ? <div className="jx-auth-form">
      <p role="status">We sent a verification link to {email}. The link expires in one hour.</p>
      {error ? <FormError errorRef={errorRef}>{error}</FormError> : null}
      <Button type="button" busy={busy} disabled={resent} onClick={async () => {
        setBusy(true); setError("");
        try { await resendVerificationEmail(email.trim()); setResent(true); }
        catch (caught) { setError(caught instanceof Error ? caught.message : "Try again shortly."); }
        finally { setBusy(false); }
      }}>{resent ? "Verification email resent" : "Resend verification email"}</Button>
    </div> : <form className="jx-auth-form" onSubmit={submit}>
      {error ? <FormError errorRef={errorRef}>{error}</FormError> : null}
      <div className="jx-signup-names">
        <Field label="First name" required><Input name="first_name" autoComplete="given-name" maxLength={120} value={first} onChange={event => setFirst(event.target.value)} required /></Field>
        <Field label="Last name" required><Input name="last_name" autoComplete="family-name" maxLength={120} value={last} onChange={event => setLast(event.target.value)} required /></Field>
      </div>
      <Field label="Phone number" required><Input name="phone" type="tel" autoComplete="tel-national" maxLength={40} value={phone} onChange={event => setPhone(event.target.value)} required /></Field>
      <Field label="Email address" required><Input name="email" type="email" autoComplete="email" maxLength={320} value={email} onChange={event => setEmail(event.target.value)} required /></Field>
      <Field label="Password" description="Use at least 8 characters." required><Input name="password" type="password" autoComplete="new-password" minLength={8} value={password} onChange={event => setPassword(event.target.value)} required /></Field>
      <fieldset className="jx-signup-states"><legend>Appointed states (required)</legend>
        <p className="jx-signup-states__count" aria-live="polite">{chosen.length ? chosen.join(", ") : "No states selected"}</p>
        <Field label="Find a state"><Input type="search" value={search} onChange={event => setSearch(event.target.value)} /></Field>
        <div className="jx-signup-states__options">
          {states.filter(state => `${state.name} ${state.code}`.toLowerCase().includes(search.toLowerCase())).map(state => <label key={state.code}>
            <input type="checkbox" name="appointed_states" value={state.code} checked={chosen.includes(state.code)} onChange={event => setChosen(current => event.target.checked ? [...current, state.code].sort() : current.filter(code => code !== state.code))} />
            {state.name} ({state.code})
          </label>)}
        </div>
      </fieldset>
      <p>If your name matches a previous customer, Noah will review the account before any previous history becomes available.</p>
      <Button type="submit" variant="primary" fullWidth busy={busy} busyLabel="Creating account…">Create account</Button>
    </form>}
  </AuthPanel></AuthRouteFrame>;
}

export function CompleteSignupRoute() {
  useDocumentTitle("Finish signup");
  const navigate = useNavigate();
  const started = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (new URLSearchParams(window.location.search).has("error")) { setError("This verification link is invalid or expired. Sign in to request a new link."); return; }
    void getProviderSession(true).then(provider => exchangeJawnixSession(provider.access_token, "/app/overview"))
      .then(session => navigate(routerPath(session.next), { replace: true }))
      .catch(() => setError("We could not finish signup. Sign in to continue, or request a new verification link."));
  }, [navigate]);
  return <AuthRouteFrame><AuthPanel title="Finish signup" description={error || "Your email is verified. We are preparing your account."}>
    {error ? <Link className="jx-auth-route__link" to="/sign-in">Go to sign in</Link> : <p role="status">Preparing your account…</p>}
  </AuthPanel></AuthRouteFrame>;
}

export function RegistrationStatusRoute() {
  useDocumentTitle("Account review");
  const data = useLoaderData() as RegistrationStatus;
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  return <AuthRouteFrame><AuthPanel title={data.status === "rejected" ? "Registration not approved" : "Account awaiting review"}
    description={data.status === "rejected" ? "Email noah@jawnix.com if you need help with your registration." : "Noah will review your registration before granting access to previous customer history."}>
    <div className="jx-auth-form"><Button type="button" busy={revalidator.state !== "idle"} onClick={() => { void revalidator.revalidate(); }}>Check status</Button>
      <Button type="button" onClick={async () => { await signOut(); navigate("/sign-in", { replace: true }); }}>Sign out</Button></div>
  </AuthPanel></AuthRouteFrame>;
}
