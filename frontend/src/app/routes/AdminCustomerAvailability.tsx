import { useState } from "react";

import { api } from "../api";
import { Button } from "../../design-system/primitives/Button";
import { FactList } from "../../design-system/primitives/facts";
import { Panel } from "../../design-system/primitives/panel";
import { DataTable } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { formatCount, formatDay } from "../operator/format";
import { errorMessage } from "./AdminCustomers";
import { formatAsOf } from "./OperatorNumbers";

import "./AdminCustomerDetails.css";

export interface AvailabilityForecastDay {
  offset: number;
  date: string;
  count: number;
}

export interface CustomerAvailabilityView {
  asOf: string | null;
  available: number | null;
  forecast: AvailabilityForecastDay[] | null;
}

/** "2026-08-04" is a calendar day, not an instant: read it as local noon so
 *  no time zone moves it to the day before. */
function forecastDay(date: string, offset: number): string {
  if (offset === 0) return "Today";
  return formatDay(`${date}T12:00:00`);
}

/**
 * What this Customer could receive right now, and when more becomes
 * eligible as other customers' cooldown windows lapse.
 *
 * The count is a stored snapshot with its as-of time; opening the page never
 * recounts it. Refreshing is the operator's explicit choice.
 */
export function CustomerAvailabilityPanel({
  availability,
  customerId,
  database,
  onChanged,
}: {
  availability: CustomerAvailabilityView;
  customerId: number;
  database: string;
  onChanged: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  async function refresh() {
    setBusy(true);
    setFailure("");
    try {
      await api(`/api/admin/customers/${customerId}/availability/refresh`, {
        method: "POST",
        body: "{}",
      });
      await onChanged();
    } catch (caught) {
      setFailure(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const now = new Date();
  const days = (availability.forecast ?? []).filter(
    (day) => day.offset === 0 || day.count > 0,
  );

  return (
    <Panel title="Availability">
      {failure ? (
        <Text role="alert" tone="danger" size="sm">
          {failure}
        </Text>
      ) : null}
      <FactList
        label="Availability"
        items={[
          {
            term: "Available now",
            description:
              availability.available === null
                ? "Not counted yet"
                : formatCount(availability.available),
          },
          { term: "Database", description: database === "db2" ? "DB2" : "DB1" },
          {
            term: "As of",
            description: availability.asOf ? formatAsOf(availability.asOf, now) : "—",
          },
        ]}
      />
      {days.length ? (
        <DataTable
          label="Cooldown forecast"
          columns={[
            { key: "day", header: "Day", cell: (day) => forecastDay(day.date, day.offset) },
            {
              key: "count",
              header: "Becomes eligible",
              numeric: true,
              cell: (day) => formatCount(day.count),
            },
          ]}
          rows={days}
          rowKey={(day) => String(day.offset)}
        />
      ) : null}
      <Text size="xs" tone="muted">
        {days.length
          ? "Today is what is eligible now; later days count leads whose cooldown window lapses that day. Days with nothing lapsing are left out."
          : "Counts this customer's states, niche policy, no-repeat history, cooldown window, and exclusions. Refresh to count it and forecast the next 14 days."}
      </Text>
      <div>
        <Button busy={busy} busyLabel="Counting…" onClick={() => void refresh()}>
          Refresh availability
        </Button>
      </div>
    </Panel>
  );
}
