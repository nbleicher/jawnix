import { Link, Navigate, useLoaderData, useLocation } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Page } from "../../design-system/primitives/layout";
import { PanelLayout, SidePanel } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import type { AdminMFAStatus } from "../auth/adminMFA";
import { formatDay, formatRate, plural } from "../operator/format";
import { useSelectedRow } from "../operator/useSelectedRow";
import { useDocumentTitle } from "../shell/useDocumentTitle";

/**
 * Setup (P7): the occasional tools, one row each with where it stands and
 * when it last changed. Daily decisions stay on Today.
 *
 * Mirrors `operator_setup` in jawnix/operator_setup.py. Two rows are
 * completed here because only the browser can ask: whether this sign-in
 * already holds a Scraper grant, and the authenticators the identity
 * provider holds. Either may be unavailable without failing the page.
 */

export interface SetupArea {
  key: string;
  label: string;
  href: string;
  state: string;
  attention: boolean;
  changedAt: string | null;
  changedNote: string | null;
  upcoming: { label: string; at: string } | null;
}

export interface SetupData {
  generatedAt: string;
  areas: SetupArea[];
  billing: {
    enabled: boolean;
    startingRateCentsPerThousand: number | null;
    billedCustomers: number;
  };
  /** Null when the Scraper grant could not be checked. */
  scraperGranted: boolean | null;
  /** Null when the identity provider could not be reached. */
  security: AdminMFAStatus | null;
}

export async function adminSetupLoader(): Promise<SetupData> {
  const [setup, grant, security] = await Promise.all([
    api<Omit<SetupData, "scraperGranted" | "security">>("/api/admin/setup"),
    api<{ granted: boolean }>("/api/admin/scraper/grant").catch(() => null),
    api<AdminMFAStatus>("/api/auth/admin-mfa").catch(() => null),
  ]);
  return { ...setup, scraperGranted: grant?.granted ?? null, security };
}

/** The state line, completed with what only the browser could read. */
function stateText(area: SetupArea, data: SetupData, now: Date): string {
  if (area.key === "scraperWorkspace" && data.scraperGranted !== null) {
    return `${data.scraperGranted ? "Verified until you sign out" : "Verify once per sign-in"} · ${area.state}`;
  }
  if (area.key === "security" && data.security) {
    const verified = data.security.factors.filter((factor) => factor.status === "verified");
    const used = verified
      .map((factor) => factor.lastUsedAt)
      .filter((value): value is string => Boolean(value))
      .sort()
      .at(-1);
    return `${plural(verified.length, "authenticator")}${used ? ` · last used ${formatDay(used, now)}` : ""}`;
  }
  if (area.upcoming) {
    return `${area.state} · ${area.upcoming.label} ${formatDay(area.upcoming.at, now)}`;
  }
  return area.state;
}

function changedText(area: SetupArea, data: SetupData, now: Date): string {
  if (area.key === "scraperWorkspace" && data.scraperGranted) return "Live";
  if (area.changedAt) return formatDay(area.changedAt, now);
  return area.changedNote ?? "—";
}

function BillingPanel({
  billing,
  onClose,
  autoFocus,
  returnFocus,
}: {
  billing: SetupData["billing"];
  onClose: () => void;
  autoFocus: boolean;
  returnFocus: () => HTMLElement | null;
}) {
  return (
    <SidePanel
      eyebrow="Setup · Server setting"
      title="Billing defaults"
      onClose={onClose}
      autoFocus={autoFocus}
      returnFocus={returnFocus}
      actions={<ActionLink href="/app/admin/customers">Change a customer's rate</ActionLink>}
    >
      <FactList
        label="Billing defaults"
        items={[
          { term: "Platform billing", description: billing.enabled ? "On" : "Off" },
          {
            term: "Starting rate",
            description:
              billing.startingRateCentsPerThousand === null
                ? "None"
                : formatRate(billing.startingRateCentsPerThousand),
          },
          { term: "Billed customers", description: billing.billedCustomers.toLocaleString() },
        ]}
      />
      <Text size="sm" tone="muted">
        New customers start at this rate on either database. It is set in the server
        configuration, and each customer's own rate is changed on their page.
      </Text>
    </SidePanel>
  );
}

export function AdminSetupRoute() {
  const data = useLoaderData<SetupData>();
  const selection = useSelectedRow("area");
  const now = new Date();
  useDocumentTitle("Setup");

  const columns: DataColumn<SetupArea>[] = [
    {
      key: "area",
      header: "Area",
      cell: (area) =>
        area.key === "billing" ? (
          <Link
            to={routePath(area.href)}
            data-panel-trigger={area.key}
            onClick={selection.openFromLink(area.key)}
          >
            {area.label}
          </Link>
        ) : (
          <Link to={routePath(area.href)}>{area.label}</Link>
        ),
    },
    {
      key: "state",
      header: "Current state",
      cell: (area) =>
        area.attention ? (
          <Text as="span" size="sm" tone="danger">
            {stateText(area, data, now)}
          </Text>
        ) : (
          stateText(area, data, now)
        ),
    },
    { key: "changed", header: "Last changed", cell: (area) => changedText(area, data, now) },
  ];

  return (
    <Page
      title="Setup"
      width="data"
      density="data"
      description="Tools you use occasionally. Daily decisions stay on Today."
    >
      <PanelLayout
        panel={
          selection.selected === "billing" ? (
            <BillingPanel
              billing={data.billing}
              onClose={selection.close}
              autoFocus={selection.autoFocus}
              returnFocus={selection.returnFocus("billing")}
            />
          ) : null
        }
      >
        <DataTable
          label="Setup areas"
          columns={columns}
          rows={data.areas}
          rowKey={(area) => area.key}
          selectedKey={selection.selected}
        />
      </PanelLayout>
    </Page>
  );
}

/** Where each part of the former Acquisition page now lives, by the anchor
 *  older links (and Telegram reviews) carry. */
const ACQUISITION_ANCHORS: Record<string, string> = {
  "#nightly-reviews": "/admin/insights#nightly-reviews",
  "#held-scrape-anomalies": "/admin/today",
  "#scraper-configuration-versions": "/admin/setup/configurations",
};

export function AcquisitionMovedRoute() {
  const { hash } = useLocation();
  return <Navigate to={ACQUISITION_ANCHORS[hash] ?? "/admin/setup"} replace />;
}
