import { useRouteLoaderData } from "react-router";

import { ActionLink } from "../../design-system/primitives/Button";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Card, Page, Stack } from "../../design-system/primitives/layout";
import { Heading, Mono, Text } from "../../design-system/primitives/typography";
import type {
  CustomerOverviewData,
  CustomerOverviewItem,
  CustomerOverviewTone,
} from "../auth/customerAuth";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { deriveRequestRef } from "./batchRequests";

import "./CustomerOverview.css";

/** The backend's tone is advisory to presentation: the book maps waiting to
 *  info, so a waiting_inventory item never renders the warning joint. */
export function attentionTone(item: CustomerOverviewItem): CustomerOverviewTone {
  return item.kind === "waiting_inventory" ? "info" : item.tone;
}

/** The item id and action href both carry the request id; the reference is
 *  derived, never fetched. Items with no request behind them (setup nudges)
 *  carry no reference. */
export function itemReference(item: CustomerOverviewItem): string | null {
  return deriveRequestRef(item.id) ?? deriveRequestRef(item.action.href);
}

function AttentionItem({ item }: { item: CustomerOverviewItem }) {
  const reference = itemReference(item);
  return (
    <Card as="li" className={`customer-attention customer-attention--${attentionTone(item)}`}>
      <Stack gap={3}>
        <Heading level={2} size="md">
          {item.title}
        </Heading>
        <Text>{item.description}</Text>
        {reference ? (
          <Text size="sm" tone="muted">
            Reference <Mono>{reference}</Mono>
          </Text>
        ) : null}
        <div>
          <ActionLink
            href={item.action.href}
            variant="primary"
            {...(reference
              ? { "aria-label": `${item.action.label} — ${reference}` }
              : {})}
          >
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
