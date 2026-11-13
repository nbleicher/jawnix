import { useEffect, useMemo, useState } from "react";
import { useLoaderData } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { Field, Select } from "../../design-system/primitives/form";
import { Page } from "../../design-system/primitives/layout";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { errorMessage } from "../operator/actions";
import { formatCount, formatDateTime } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { BackToInventory } from "./inventoryTools";
import "./AdminInventory.css";

/** Pool analytics: usable DB1 leads by state and effective Niche. */

export interface PoolBreakdownCell {
  state: string;
  niche: string;
  count: number;
}

export interface PoolBreakdownData {
  asOf: string | null;
  cells: PoolBreakdownCell[];
  refreshQueued?: boolean;
}

export async function adminPoolAnalyticsLoader(): Promise<PoolBreakdownData> {
  return api<PoolBreakdownData>("/api/admin/pool-breakdown");
}

const POOL_TOP = 25;

/** Pool analytics, moved here from Fulfillment: usable DB1 leads by state and
 *  effective Niche, recounted by the same background job as the totals. */
export function AdminPoolAnalyticsRoute() {
  const loaded = useLoaderData<PoolBreakdownData>();
  const [data, setData] = useState(loaded);
  const [state, setState] = useState("");
  const [all, setAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => setData(loaded), [loaded]);
  useDocumentTitle("Pool analytics");

  const states = useMemo(
    () => [...new Set(data.cells.map((cell) => cell.state))].sort(),
    [data.cells],
  );
  const rows = useMemo(
    () =>
      data.cells
        .filter((cell) => !state || cell.state === state)
        .sort((a, b) => b.count - a.count || a.state.localeCompare(b.state) || a.niche.localeCompare(b.niche)),
    [data.cells, state],
  );
  const shown = all ? rows : rows.slice(0, POOL_TOP);
  const total = rows.reduce((sum, cell) => sum + cell.count, 0);

  async function refresh() {
    setBusy(true);
    setMessage("");
    setFailed(false);
    try {
      setData(await api<PoolBreakdownData>("/api/admin/pool-breakdown/refresh", { method: "POST", body: "{}" }));
      setMessage("Recount queued. The breakdown updates when it finishes.");
    } catch (caught) {
      setFailed(true);
      setMessage(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const columns: DataColumn<PoolBreakdownCell>[] = [
    { key: "state", header: "State", cell: (row) => row.state, footer: state ? `All of ${state}` : "All states" },
    { key: "niche", header: "Niche", cell: (row) => (row.niche === "unmapped" ? "Unmapped" : row.niche) },
    {
      key: "count",
      header: "Usable",
      numeric: true,
      cell: (row) => formatCount(row.count),
      footer: formatCount(total),
    },
  ];

  return (
    <Page
      title="Pool analytics"
      width="data"
      density="data"
      description="Usable DB1 leads by state and Niche. Leads with no Niche are counted as Unmapped."
      actions={
        <Button variant="secondary" busy={busy} busyLabel="Queuing…" onClick={() => void refresh()}>
          Refresh breakdown
        </Button>
      }
    >
      <Text size="sm" tone={failed ? "danger" : "muted"} role="status">
        {message || (data.asOf ? `As of ${formatDateTime(data.asOf)}` : "Not counted yet. The background recount fills this in.")}
      </Text>
      <div className="jx-inventory-filters">
        <Field label="State">
          <Select value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">All states</option>
            {states.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <DataTable
        label="Pool breakdown"
        columns={columns}
        rows={shown}
        rowKey={(row) => `${row.state}:${row.niche}`}
        empty="No breakdown yet. Opening this page never runs the count."
      />
      {rows.length > POOL_TOP ? (
        <div>
          <Button variant="secondary" onClick={() => setAll((value) => !value)}>
            {all ? `Show the top ${POOL_TOP}` : `Show all ${formatCount(rows.length)} rows`}
          </Button>
        </div>
      ) : null}
      <BackToInventory />
    </Page>
  );
}
