import { useState } from "react";
import {
  redirect,
  useLoaderData,
  useNavigate,
  useRevalidator,
} from "react-router";

import {
  api,
  formatCode,
  providerAccessToken,
} from "../auth/adminMFA";
import { storeProviderSession } from "../auth/providerSession";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { ActionLink, Button } from "../../design-system/primitives/Button";
import { ErrorState } from "../../design-system/primitives/feedback";
import {
  Field,
  Input,
} from "../../design-system/primitives/form";
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
  factors: ScraperFactor[];
  idleExpiresIn: number;
}

interface ScraperWorkspaceData {
  available: boolean;
  serviceState?: "connected";
  lastSuccessfulAt: string | null;
  idleExpiresIn?: number;
}

interface ScraperStepUpResult {
  ok: true;
  idleExpiresIn: number;
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

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

export async function scraperEntryLoader(): Promise<ScraperEntryData> {
  return api<ScraperEntryData>("/api/admin/scraper/entry", {
    method: "POST",
  });
}

export async function scraperWorkspaceLoader(): Promise<ScraperWorkspaceData> {
  const response = await fetch("/api/admin/scraper/workspace", {
    credentials: "same-origin",
    headers: { "X-CSRF-Token": csrf() },
  });
  const body = (await response.json().catch(() => ({}))) as {
    detail?: string;
    serviceState?: "connected";
    lastSuccessfulAt?: string | null;
    idleExpiresIn?: number;
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
    idleExpiresIn: body.idleExpiresIn ?? 900,
  };
}

export function ScraperStepUpRoute() {
  const data = useLoaderData<ScraperEntryData>();
  const navigate = useNavigate();
  const [factorId, setFactorId] = useState(() =>
    defaultFactorId(data.factors),
  );
  const [expanded, setExpanded] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useDocumentTitle("Verify access to Scraper Operations");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!factorId || code.length !== 6) {
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
      description="GMS/OPS requires a new authenticator-app challenge every time you enter, even from a verified administrator session."
    >
      <TerminalWorkspace status="LOCKED / STEP-UP REQUIRED">
        <form id="scraper-access" onSubmit={(event) => void submit(event)}>
          <Stack gap={5}>
            <Text>
              A successful challenge opens a privileged session. It closes after
              15 minutes without Scraper activity.
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
              label="Six-digit authenticator code"
              description="Paste or type the current code from your authenticator app."
              required
              {...(error ? { error } : {})}
            >
              <Input
                value={code}
                onChange={(event) => setCode(formatCode(event.target.value))}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                autoFocus
              />
            </Field>
            <Cluster>
              <Button
                type="submit"
                variant="primary"
                busy={busy}
                busyLabel="Verifying…"
              >
                Verify and open workspace
              </Button>
              <ActionLink href="/app/admin/acquisition">
                Back to Acquisition
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
        status={data.available ? "ONLINE / PRIVILEGED" : "OFFLINE / FAIL-CLOSED"}
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
                  <Text weight="bold">Fresh challenge verified</Text>
                  <Text size="sm">
                    Expires after 15 minutes without Scraper activity.
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
                <ActionLink href="/app/admin/acquisition">
                  Back to Acquisition
                </ActionLink>
              </div>
            </Stack>
          )}
        </div>
      </TerminalWorkspace>
    </Page>
  );
}
