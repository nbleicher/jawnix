import { Section } from "../../../design-system/primitives/layout";
import { ActivityTimeline } from "../AdminActivity";
import type { ActivityPage } from "../AdminActivity";

const SAMPLE: ActivityPage = {
  entries: [
    {
      id: "gallery-activity",
      action: "customer_updated",
      entityType: "customer",
      entityId: "42",
      entityHref: "/app/admin/customers/42",
      actor: "admin@example.com",
      reason: "Correct the Customer name from the signed agreement.",
      details: {
        before: { name: "North Shore" },
        after: { name: "North Shore Insurance" },
      },
      recordedAt: "2026-07-29T14:30:00Z",
    },
  ],
  page: 1,
  pageSize: 25,
  total: 1,
  pages: 1,
};

function ActivityGallery() {
  return (
    <Section
      title="Activity timeline"
      description="Read-only attribution and safe before/after context."
    >
      <ActivityTimeline activity={SAMPLE} />
    </Section>
  );
}

export default {
  title: "Activity",
  order: 90,
  Component: ActivityGallery,
};
