import { useRouteLoaderData } from "react-router";
import type { ReactNode } from "react";

import { ActionLink } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Text,
} from "../../design-system/primitives/typography";
import type {
  CustomerOverviewAction,
  CustomerOverviewData,
  CustomerOverviewDelivery,
} from "../auth/customerAuth";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import "./CustomerOverview.css";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
  }).format(new Date(value));
}

function actionLink(
  action: CustomerOverviewAction,
  variant: "primary" | "secondary" = "secondary",
) {
  return (
    <ActionLink href={action.href} variant={variant}>
      {action.label}
    </ActionLink>
  );
}

function Detail({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="customer-overview__detail">
      <dt>
        <LabelText>{label}</LabelText>
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

function Delivery({ delivery }: { delivery: CustomerOverviewDelivery }) {
  return (
    <li>
      <Card>
        <Stack gap={3}>
          <Cluster justify="space-between" align="flex-start">
            <Heading level={3} size="sm">
              {delivery.lead_count.toLocaleString()} leads
            </Heading>
            <Text size="sm" tone="muted">
              {formatDate(delivery.delivered_at)}
            </Text>
          </Cluster>
          <Text size="sm">
            {delivery.states.join(", ")}
          </Text>
        </Stack>
      </Card>
    </li>
  );
}

export function CustomerOverviewRoute() {
  const overview = useRouteLoaderData<CustomerOverviewData>("customer");
  useDocumentTitle("Overview");
  if (!overview) {
    throw new Error("Customer Overview data was not loaded.");
  }

  const greeting = overview.first_name
    ? `Welcome back, ${overview.first_name}.`
    : "Welcome back.";

  return (
    <Page
      title="Overview"
      description={`${greeting} See what is happening now and what you can do next.`}
      actions={
        overview.primary_actions.map((action, index) => (
          <span key={action.kind}>
            {actionLink(action, index === 0 ? "primary" : "secondary")}
          </span>
        ))
      }
    >
      <Section title="Next step">
        <Card className="customer-overview__next">
          <Stack gap={3}>
            <LabelText>Available now</LabelText>
            <Heading level={3} size="lg">
              {overview.next_action.label}
            </Heading>
            <Text>{overview.next_action.description}</Text>
            <div>{actionLink(overview.next_action, "primary")}</div>
          </Stack>
        </Card>
      </Section>

      <Section
        title="Current request"
        description="Your most recently submitted Batch Request."
      >
        {overview.current_request ? (
          <Card>
            <Stack gap={4}>
              <Cluster justify="space-between" align="flex-start">
                <Heading level={3}>
                  {overview.current_request.lead_count.toLocaleString()} leads
                </Heading>
                <StatusBadge tone={overview.current_request.status.tone}>
                  {overview.current_request.status.label}
                </StatusBadge>
              </Cluster>
              <Text>{overview.current_request.status.description}</Text>
              <dl className="customer-overview__details">
                <Detail label="Licensed States">
                  {overview.current_request.states.join(", ")}
                </Detail>
                <Detail label="Submitted">
                  {formatDate(overview.current_request.submitted_at)}
                </Detail>
                {overview.current_request.delivered_at ? (
                  <Detail label="Delivered">
                    {formatDate(overview.current_request.delivered_at)}
                  </Detail>
                ) : null}
              </dl>
            </Stack>
          </Card>
        ) : (
          <EmptyState
            title="No Batch Requests yet"
            description="Nothing is in progress. Request a Batch when you are ready."
            action={actionLink(overview.next_action, "primary")}
          />
        )}
      </Section>

      <Grid minColumnWidth="20rem">
        <Section
          title="Licensed States"
          description="The states currently saved to your account."
        >
          {overview.licensed_states.length ? (
            <Card>
              <ul className="customer-overview__states" aria-label="Licensed States">
                {overview.licensed_states.map((state) => (
                  <li key={state}>{state}</li>
                ))}
              </ul>
            </Card>
          ) : (
            <EmptyState
              title="No Licensed States yet"
              description="Nothing has been added. Add a Licensed State before requesting a Batch."
              action={
                <ActionLink href="/app/account" variant="secondary">
                  Add Licensed States
                </ActionLink>
              }
            />
          )}
        </Section>

        <Section
          title="Recent deliveries"
          description="Your three most recent delivered Batches."
        >
          {overview.recent_deliveries.length ? (
            <Stack as="ul" gap={3} className="customer-overview__deliveries">
              {overview.recent_deliveries.map((delivery) => (
                <Delivery key={delivery.request_id} delivery={delivery} />
              ))}
            </Stack>
          ) : (
            <EmptyState
              title="No deliveries yet"
              description="Nothing has been delivered yet. Completed Batches will appear here."
            />
          )}
        </Section>
      </Grid>
    </Page>
  );
}
