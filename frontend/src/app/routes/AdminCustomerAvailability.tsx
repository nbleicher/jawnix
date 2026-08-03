import { useState } from "react";

import { api } from "../auth/adminMFA";
import { Button } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Card, Section, Stack } from "../../design-system/primitives/layout";
import { Heading, Text } from "../../design-system/primitives/typography";
import { errorMessage, formatAdminDate } from "./AdminCustomers";
import { Fact } from "./AdminCustomerActions";

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

export function CustomerAvailabilitySection({
  availability,
  customerId,
  onChanged,
}: {
  availability: CustomerAvailabilityView;
  customerId: number;
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

  const forecast = availability.forecast ?? [];

  return (
    <Section
      title="Pool availability"
      description="Leads deliverable right now for this Customer, honoring Licensed States, Niche Policy, no-repeat history, Cooldown Window, and exclusions. Counts are refreshed on demand and stamped with an as-of time — never computed just by opening this page."
    >
      <Card>
        <Stack gap={4} align="flex-start">
          {failure ? (
            <Text role="alert" tone="danger" size="sm">
              {failure}
            </Text>
          ) : null}
          <dl className="admin-customer-details__facts">
            <Fact label="Available now">
              {availability.available === null
                ? "Not computed yet"
                : availability.available.toLocaleString()}
            </Fact>
            <Fact label="As of">
              {availability.asOf ? formatAdminDate(availability.asOf) : "—"}
            </Fact>
          </dl>
          {forecast.length ? (
            <Stack gap={2} align="flex-start">
              <Heading level={3} size="sm">
                Cooldown forecast (14 days)
              </Heading>
              <Text size="sm" tone="muted">
                Day 0 is eligible today. Later days count Leads whose Cooldown
                Window lapses that day.
              </Text>
              <ul
                className="admin-customer-details__list"
                aria-label="Cooldown forecast"
              >
                {forecast.map((day) => (
                  <li key={day.offset}>
                    <Text>
                      {day.date}
                      {day.offset === 0 ? " (today)" : ""}:{" "}
                      {day.count.toLocaleString()}
                    </Text>
                  </li>
                ))}
              </ul>
            </Stack>
          ) : (
            <EmptyState
              title="No forecast yet"
              description="Refresh availability to compute today's eligible count and the next 14 days of Cooldown Window lapses."
            />
          )}
          <Button variant="primary" busy={busy} onClick={() => void refresh()}>
            Refresh availability
          </Button>
        </Stack>
      </Card>
    </Section>
  );
}
