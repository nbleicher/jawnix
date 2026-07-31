import { useRouteLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Card, Page, Stack } from "../../design-system/primitives/layout";
import { Heading, Text } from "../../design-system/primitives/typography";
import type {
  CustomerOverviewData,
  CustomerOverviewItem,
} from "../auth/customerAuth";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import "./CustomerOverview.css";

function AttentionItem({ item }: { item: CustomerOverviewItem }) {
  return (
    <Card as="li" className={`customer-attention customer-attention--${item.tone}`}>
      <Stack gap={3}>
        <Heading level={2} size="md">
          {item.title}
        </Heading>
        <Text>{item.description}</Text>
        <div>
          <ActionLink href={item.action.href} variant="primary">
            {item.action.label}
          </ActionLink>
        </div>
      </Stack>
    </Card>
  );
}

export function CustomerOverviewRoute() {
  const overview = useRouteLoaderData<CustomerOverviewData>("customer");
  useDocumentTitle("Overview");
  if (!overview) {
    throw new Error("Customer Overview data was not loaded.");
  }

  return (
    <Page
      title="Overview"
      density="data"
      description="Only work that needs your attention appears here."
    >
      {overview.items.length ? (
        <Stack as="ol" gap={4} className="customer-attention-queue" aria-label="Attention queue">
          {overview.items.map((item) => (
            <AttentionItem key={item.id} item={item} />
          ))}
        </Stack>
      ) : (
        <EmptyState
          title="Nothing needs your attention"
          description="You're all caught up."
        />
      )}
    </Page>
  );
}
