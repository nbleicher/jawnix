import { useMemo, useState } from "react";
import {
  Link,
  useLoaderData,
  useNavigate,
  useRevalidator,
  useSearchParams,
} from "react-router";

import {
  api,
  formatCode,
  providerAccessToken,
} from "../auth/adminMFA";
import type {
  AdminMFAFactor,
  AdminMFAStatus,
  EnrollmentSecret,
} from "../auth/adminMFA";
import { storeProviderSession } from "../auth/providerSession";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { Button } from "../../design-system/primitives/Button";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import { defaultFactorId, FactorChooser } from "../auth/factorChoice";

const PENDING_ENROLLMENT_KEY = "jawnix_pending_mfa_enrollment";

interface VerificationResult {
  ok: true;
  complete: boolean;
  next: string;
  session: {
    accessToken: string;
    refreshToken: string;
    expiresIn: number;
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function readPendingEnrollment(): EnrollmentSecret | null {
  try {
    const value = sessionStorage.getItem(PENDING_ENROLLMENT_KEY);
    return value ? (JSON.parse(value) as EnrollmentSecret) : null;
  } catch {
    return null;
  }
}

function savePendingEnrollment(value: EnrollmentSecret | null) {
  if (value) {
    sessionStorage.setItem(PENDING_ENROLLMENT_KEY, JSON.stringify(value));
  } else {
    sessionStorage.removeItem(PENDING_ENROLLMENT_KEY);
  }
}

function AuthenticatorCode({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
}) {
  return (
    <Field
      id="authenticator-code"
      label="Six-digit authenticator code"
      description="Paste or type the code. Spaces and punctuation are ignored."
      required
      {...(error ? { error } : {})}
    >
      <Input
        className="jx-mfa-code"
        value={value}
        onChange={(event) => onChange(formatCode(event.target.value))}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9 ]*"
        maxLength={11}
        autoFocus
      />
    </Field>
  );
}

export function AdminMFAEnrollmentRoute() {
  const status = useLoaderData<AdminMFAStatus>();
  const revalidator = useRevalidator();
  const navigate = useNavigate();
  const verifiedCount = status.factors.filter(
    (factor) => factor.status === "verified" && factor.type === "totp",
  ).length;
  const expectedSlot = verifiedCount === 0 ? "primary" : "backup";
  const [enrollment, setEnrollment] = useState<EnrollmentSecret | null>(
    readPendingEnrollment,
  );
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const slot = enrollment?.slot ?? expectedSlot;
  const replacement = slot === "replacement";
  const title = replacement
    ? "Replace lost authenticator"
    : slot === "primary"
      ? "Set up your primary authenticator"
      : "Set up your backup authenticator";
  useDocumentTitle(title);

  async function start() {
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      const value = await api<EnrollmentSecret>(
        "/api/auth/admin-mfa/enrollment",
        {
          method: "POST",
          body: JSON.stringify({
            access_token: accessToken,
            slot: expectedSlot,
          }),
        },
      );
      savePendingEnrollment(value);
      setEnrollment(value);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    if (code.length !== 6) {
      setError("Enter the six-digit code from your authenticator app.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      const result = await api<VerificationResult>(
        "/api/auth/admin-mfa/enrollment/verify",
        {
          method: "POST",
          body: JSON.stringify({ access_token: accessToken, code }),
        },
      );
      await storeProviderSession(result.session);
      savePendingEnrollment(null);
      setEnrollment(null);
      setCode("");
      if (result.complete) {
        await navigate("/admin/overview", { replace: true });
      } else {
        await revalidator.revalidate();
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      await api("/api/auth/admin-mfa/enrollment/cancel", {
        method: "POST",
        body: JSON.stringify({ access_token: accessToken }),
      });
      savePendingEnrollment(null);
      setEnrollment(null);
      setCode("");
      await revalidator.revalidate();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title={title}
      description={
        replacement
          ? "Verify the replacement before Jawnix removes the authenticator you lost."
          : slot === "primary"
            ? "Administration stays closed until both authenticators are verified."
            : "This second factor is your recovery path when the primary device is unavailable."
      }
    >
      <Card>
        <Stack gap={5}>
          {slot === "backup" ? (
            <p className="jx-mfa-notice">
              Store the backup authenticator somewhere other than your primary
              device. Two factors on one device do not protect you from losing it.
            </p>
          ) : null}

          {error ? (
            <Text tone="danger" weight="semibold" role="alert">
              {error}
            </Text>
          ) : null}

          {!enrollment ? (
            <Stack gap={4}>
              <Text>
                You will scan a QR code with an authenticator app, then confirm
                one generated code.
              </Text>
              <Cluster className="jx-mfa-actions">
                <Button variant="primary" busy={busy} onClick={() => void start()}>
                  {status.stage.endsWith("_pending")
                    ? "Restart setup"
                    : `Set up ${slot} authenticator`}
                </Button>
                {status.stage !== "idle" ? (
                  <Button busy={busy} onClick={() => void cancel()}>
                    Cancel enrollment
                  </Button>
                ) : null}
              </Cluster>
            </Stack>
          ) : (
            <form onSubmit={(event) => void verify(event)}>
              <Stack gap={5}>
                <img
                  className="jx-mfa-qr"
                  src={`data:image/svg+xml,${encodeURIComponent(enrollment.qrCode)}`}
                  alt={`QR code for the ${slot} authenticator`}
                />
                <Stack gap={2}>
                  <Text weight="semibold">Cannot scan the QR code?</Text>
                  <Text size="sm" tone="muted">
                    Enter this manual key in your authenticator app.
                  </Text>
                  <code className="jx-mfa-key">{enrollment.manualKey}</code>
                </Stack>
                <AuthenticatorCode
                  value={code}
                  onChange={setCode}
                  error={error || undefined}
                />
                <Cluster className="jx-mfa-actions">
                  <Button
                    type="submit"
                    variant="primary"
                    busy={busy}
                    busyLabel="Verifying…"
                  >
                    Verify authenticator
                  </Button>
                  <Button busy={busy} onClick={() => void cancel()}>
                    Cancel enrollment
                  </Button>
                </Cluster>
              </Stack>
            </form>
          )}
        </Stack>
      </Card>
    </Page>
  );
}

export function AdminMFAChallengeRoute() {
  const status = useLoaderData<AdminMFAStatus>();
  const navigate = useNavigate();
  const factors = status.factors.filter(
    (factor) => factor.status === "verified" && factor.type === "totp",
  );
  const [searchParams] = useSearchParams();
  const [factorId, setFactorId] = useState(() => defaultFactorId(factors));
  const [expanded, setExpanded] = useState(
    () => searchParams.get("choose") === "1",
  );
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useDocumentTitle("Verify your administrator sign-in");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!factorId || code.length !== 6) {
      setError("Choose an authenticator and enter its six-digit code.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      const result = await api<VerificationResult>(
        "/api/auth/admin-mfa/challenge",
        {
          method: "POST",
          body: JSON.stringify({
            access_token: accessToken,
            factor_id: factorId,
            code,
          }),
        },
      );
      await storeProviderSession(result.session);
      await navigate("/admin/overview", { replace: true });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Verify your administrator sign-in"
      description="Your password was accepted. Complete the authenticator challenge to enter administration."
    >
      <Card>
        <form onSubmit={(event) => void submit(event)}>
          <Stack gap={5}>
            {status.throttled ? (
              <p className="jx-mfa-notice" role="alert">
                Verification is temporarily throttled. Try again after{" "}
                {status.lockedUntil
                  ? new Date(status.lockedUntil).toLocaleTimeString()
                  : "the stated time"}.
              </p>
            ) : null}
            <FactorChooser
              factors={factors}
              factorId={factorId}
              onSelect={setFactorId}
              expanded={expanded}
              onExpand={() => setExpanded(true)}
            />
            <AuthenticatorCode
              value={code}
              onChange={setCode}
              error={error || undefined}
            />
            <Cluster className="jx-mfa-actions">
              <Button
                type="submit"
                variant="primary"
                busy={busy}
                disabled={status.throttled}
                busyLabel="Verifying…"
              >
                Verify and continue
              </Button>
            </Cluster>
          </Stack>
        </form>
      </Card>
    </Page>
  );
}

export function AdminMFARecoveryRoute() {
  const status = useLoaderData<AdminMFAStatus>();
  useDocumentTitle("Recover administrator access");

  return (
    <Page
      title="Recover administrator access"
      description="Choose the path that matches what you still hold."
    >
      <Stack gap={5}>
        <Card>
          <Stack gap={3}>
            <h2>Use your backup authenticator</h2>
            <Text>
              If one device is unavailable, verify with the separately stored
              backup. After sign-in, Security lets you replace the lost factor.
            </Text>
            <div>
              <Link to="/admin/mfa/challenge?choose=1">
                Choose the backup authenticator
              </Link>
            </div>
          </Stack>
        </Card>
        <Card>
          <Stack gap={3}>
            <h2>Both authenticators are unavailable</h2>
            <Text>
              There is no self-service reset. Contact an operator and provide
              the incident details required for the two-person break-glass
              procedure. Recovery restores enrollment access only.
            </Text>
          </Stack>
        </Card>
        {status.factors.length ? (
          <Text size="sm" tone="muted">
            Jawnix still sees {status.factors.length} enrolled authenticator
            {status.factors.length === 1 ? "" : "s"}.
          </Text>
        ) : null}
      </Stack>
    </Page>
  );
}

function FactorCard({
  factor,
  onReplace,
  busy,
}: {
  factor: AdminMFAFactor;
  onReplace: (factor: AdminMFAFactor) => void;
  busy: boolean;
}) {
  return (
    <Card>
      <Stack gap={3}>
        <h3>{factor.name}</h3>
        <Text size="sm" tone="muted">
          {factor.status === "verified" ? "Verified" : "Awaiting verification"}
        </Text>
        <Text size="sm">
          Last used:{" "}
          {factor.lastUsedAt
            ? new Date(factor.lastUsedAt).toLocaleString()
            : "Never"}
        </Text>
        {factor.lastUsedFrom ? (
          <Text size="sm" tone="muted">
            From {factor.lastUsedFrom.ipAddress} ·{" "}
            {factor.lastUsedFrom.userAgent}
          </Text>
        ) : null}
        {factor.status === "verified" ? (
          <Button busy={busy} onClick={() => onReplace(factor)}>
            Replace this authenticator
          </Button>
        ) : null}
      </Stack>
    </Card>
  );
}

export function AdminMFASecurityRoute() {
  const status = useLoaderData<AdminMFAStatus>();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const verified = useMemo(
    () => status.factors.filter((factor) => factor.status === "verified"),
    [status.factors],
  );
  useDocumentTitle("Administrator security");

  async function replace(factor: AdminMFAFactor) {
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      const enrollment = await api<EnrollmentSecret>(
        "/api/auth/admin-mfa/replacement",
        {
          method: "POST",
          body: JSON.stringify({
            access_token: accessToken,
            lost_factor_id: factor.id,
          }),
        },
      );
      savePendingEnrollment(enrollment);
      await navigate("/admin/mfa/enroll");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function logoutEverywhere() {
    setBusy(true);
    setError("");
    try {
      const accessToken = await providerAccessToken();
      await api("/api/auth/admin-mfa/logout-everywhere", {
        method: "POST",
        body: JSON.stringify({ access_token: accessToken }),
      });
      window.location.assign("/login.html");
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <Page
      title="Administrator security"
      description="Review authenticator activity, replace a lost factor, or revoke every session."
    >
      <Stack gap={6}>
        {error ? (
          <Text role="alert" tone="danger" weight="semibold">
            {error}
          </Text>
        ) : null}
        <Section
          title="Authenticator factors"
          description="Keep exactly one primary and one separately stored backup available."
        >
          <Grid>
            {verified.map((factor) => (
              <FactorCard
                key={factor.id}
                factor={factor}
                onReplace={(value) => void replace(value)}
                busy={busy}
              />
            ))}
          </Grid>
        </Section>
        <Section
          title="Sessions"
          description="Use this after suspicious activity or use on a device you no longer trust."
        >
          <Button
            variant="danger"
            busy={busy}
            onClick={() => void logoutEverywhere()}
          >
            Sign out everywhere
          </Button>
        </Section>
      </Stack>
    </Page>
  );
}
