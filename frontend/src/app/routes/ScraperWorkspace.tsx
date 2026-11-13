import { useState } from "react";
import {
  redirect,
  useLoaderData,
  useNavigate,
  useRevalidator,
} from "react-router";

import { api, apiFetch, csrfToken } from "../api";
import { formatCode, providerAccessToken } from "../auth/adminMFA";
import { storeProviderSession } from "../auth/providerSession";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Stack,
} from "../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { Text } from "../../design-system/primitives/typography";
import { defaultFactorId, FactorChooser } from "../auth/factorChoice";

interface ScraperFactor {
  id: string;
  name: string;
  type: string;
  lastUsedAt?: string | null;
}

interface ScraperEntryData {
  /** True when this sign-in already holds a live grant. */
  granted: boolean;
  factors: ScraperFactor[];
}

interface ScraperWorkspaceData {
  available: boolean;
  serviceState?: "connected";
  lastSuccessfulAt: string | null;
}

interface ScraperStepUpResult {
  ok: true;
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

/** One verification per sign-in: a live grant goes straight to the
 *  workspace, and only a sign-in without one is asked for a code. */
export async function scraperEntryLoader(): Promise<ScraperEntryData> {
  const entry = await api<ScraperEntryData>("/api/admin/scraper/entry", {
    method: "POST",
  });
  if (entry.granted) throw redirect("/admin/acquisition/scraper/workspace");
  return entry;
}

export async function scraperWorkspaceLoader(): Promise<ScraperWorkspaceData> {
  const response = await apiFetch("/api/admin/scraper/workspace", {
    headers: { "X-CSRF-Token": csrfToken() },
  });
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string;
    serviceState?: "connected";
    lastSuccessfulAt?: string | null;
  };
  if (response.status === 401) {
    throw redirect("/admin/acquisition/scraper");
  }
  if (response.status === 503) {
    return {
      available: false,
      lastSuccessfulAt: body.lastSuccessfulAt ?? null,
    };
  }
  if (!response.ok) {
    throw new Error(body.detail || "Scraper Operations could not be loaded.");
  }
  return {
    available: true,
    serviceState: "connected",
    lastSuccessfulAt: body.lastSuccessfulAt ?? null,
  };
}

export function ScraperStepUpRoute() {
  const data = useLoaderData<ScraperEntryData>();
  const navigate = useNavigate();
  const [factorId, setFactorId] = useState(() => defaultFactorId(data.factors));
  const [expanded, setExpanded] = useState(false);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useDocumentTitle("Verify access to Scraper Operations");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!factorId || (recovery ? !code.trim() : code.length !== 6)) {
      setError("Choose an authenticator and enter its six-digit code.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api<ScraperStepUpResult>(
        "/api/admin/scraper/step-up",
        {
          method: "POST",
          body: JSON.stringify({
            access_token: await providerAccessToken(),
            factor_id: factorId,
            code,
            method: recovery ? "recovery" : "totp",
          }),
        },
      );
      await storeProviderSession(result.session);
      await navigate("/admin/acquisition/scraper/workspace", {
        replace: true,
      });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page
      title="Verify access to Scraper Operations"
      description="One authenticator-app challenge per sign-in opens the workspace."
    >
      <TerminalWorkspace status="LOCKED / STEP-UP REQUIRED">
        <form id="scraper-access" onSubmit={(event) => void submit(event)}>
          <Stack gap={5}>
            <Text>
              A successful challenge opens a privileged session that lasts until
              you sign out or your administrator session expires.
            </Text>
            <Text size="sm" tone="muted">
              This challenge is independent of your current administrator
              assurance.
            </Text>
            <FactorChooser
              factors={data.factors}
              factorId={factorId}
              onSelect={setFactorId}
              expanded={expanded}
              onExpand={() => setExpanded(true)}
            />
            <Field
              id="scraper-authenticator-code"
              label={
                recovery ? "Recovery code" : "Six-digit authenticator code"
              }
              description={
                recovery
                  ? "Enter one unused recovery code."
                  : "Paste or type the current code from your authenticator app."
              }
              required
              {...(error ? { error } : {})}
            >
              <Input
                value={code}
                onChange={(event) =>
                  setCode(
                    recovery
                      ? event.target.value
                      : formatCode(event.target.value),
                  )
                }
                inputMode={recovery ? "text" : "numeric"}
                autoComplete="one-time-code"
                maxLength={recovery ? 32 : 6}
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
            <Cluster>
              <Button
                type="submit"
                variant="primary"
                busy={busy}
                busyLabel="Verifying…"
              >
                Verify and open workspace
              </Button>
              <ActionLink href="/app/admin/setup">
                Back to Setup
              </ActionLink>
            </Cluster>
          </Stack>
        </form>
      </TerminalWorkspace>
    </Page>
  );
}

export function ScraperWorkspaceRoute() {
  const data = useLoaderData<ScraperWorkspaceData>();
  const revalidator = useRevalidator();
  useDocumentTitle("Scraper Operations");

  return (
    <Page
      title="Scraper Operations"
      description="Native, backend-mediated access to the private acquisition service."
    >
      <TerminalWorkspace
        status={
          data.available ? "ONLINE / PRIVILEGED" : "OFFLINE / FAIL-CLOSED"
        }
      >
        <div id="scraper-access">
          {data.available ? (
            <Grid minColumnWidth="16rem">
              <Card as="section" aria-label="Service connection">
                <Stack gap={2}>
                  <Text size="sm" tone="muted">
                    Service connection
                  </Text>
                  <Text weight="bold">Connected through Jawnix</Text>
                  <Text size="sm">
                    Last successful connection:{" "}
                    {data.lastSuccessfulAt
                      ? new Date(data.lastSuccessfulAt).toLocaleString()
                      : "Just now"}
                  </Text>
                </Stack>
              </Card>
              <Card as="section" aria-label="Privileged session">
                <Stack gap={2}>
                  <Text size="sm" tone="muted">
                    Privileged session
                  </Text>
                  <Text weight="bold">Challenge verified</Text>
                  <Text size="sm">
                    Lasts until you sign out or your administrator session
                    expires.
                  </Text>
                </Stack>
              </Card>
            </Grid>
          ) : (
            <Stack gap={4}>
              <ErrorState
                title="Scraper Operations unavailable"
                description={`The private service did not respond. No controls are available. Last successful connection: ${
                  data.lastSuccessfulAt
                    ? new Date(data.lastSuccessfulAt).toLocaleString()
                    : "Never"
                }.`}
                onRetry={() => void revalidator.revalidate()}
              />
              <div>
                <ActionLink href="/app/admin/setup">
                  Back to Setup
                </ActionLink>
              </div>
            </Stack>
          )}
        </div>
      </TerminalWorkspace>
    </Page>
  );
}
