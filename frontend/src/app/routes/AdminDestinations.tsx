import { useLoaderData } from "react-router";

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
import { Heading, Text } from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";

interface DestinationAction {
  label: string;
  href: string;
}

interface WorkspaceArea {
  title: string;
  description: string;
  action?: DestinationAction;
}

interface AdminDestinationData {
  title: string;
  description: string;
  sectionTitle: string;
  sectionDescription: string;
  actions?: DestinationAction[];
  areas: WorkspaceArea[];
  empty: {
    title: string;
    description: string;
    action: DestinationAction;
  };
}

const OVERVIEW: AdminDestinationData = {
  title: "Overview",
  description:
    "Start with the workspace that owns the task. This wayfinder avoids presenting work without a valid destination and next action.",
  sectionTitle: "Choose a workspace",
  sectionDescription:
    "Administration is organized by the work being done, not by the underlying record hierarchy.",
  areas: [
    {
      title: "Fulfillment",
      description:
        "Batch delivery, inventory decisions, delivery recovery, and lead eligibility controls.",
      action: {
        label: "Open Fulfillment",
        href: "/app/admin/fulfillment",
      },
    },
    {
      title: "Acquisition",
      description:
        "Scraper operations, source inputs, pipeline health, and acquired-data review.",
      action: {
        label: "Open Acquisition",
        href: "/app/admin/acquisition",
      },
    },
    {
      title: "Customers",
      description:
        "Durable Customer identity, User Account access, Licensed States, and Agency membership.",
      action: {
        label: "Open Customers",
        href: "/app/admin/customers",
      },
    },
  ],
  empty: {
    title: "No administration workspaces are available",
    description:
      "Review administrator security while the workspace directory is restored.",
    action: {
      label: "Review administrator security",
      href: "/app/admin/security",
    },
  },
};


/** Route data stays behind a loader even while it is a local information
 * architecture contract. Later slices can replace the source without changing
 * the screen or bypassing the shell's pending and error seams. */
export async function adminOverviewLoader(): Promise<AdminDestinationData> {
  return OVERVIEW;
}


export function AdminDestinationRoute() {
  const data = useLoaderData<AdminDestinationData>();
  useDocumentTitle(data.title);

  return (
    <Page
      title={data.title}
      description={data.description}
      actions={
        data.actions?.length ? (
          <Cluster gap={2}>
            {data.actions.map((action) => (
              <ActionLink key={action.href} href={action.href}>
                {action.label}
              </ActionLink>
            ))}
          </Cluster>
        ) : undefined
      }
    >
      <Section
        title={data.sectionTitle}
        description={data.sectionDescription}
      >
        {data.areas.length ? (
          <Grid minColumnWidth="16rem">
            {data.areas.map((area) => (
              <Card as="article" key={area.title}>
                <Stack gap={4}>
                  <Stack gap={2}>
                    <Heading level={3}>{area.title}</Heading>
                    <Text size="sm" tone="muted">
                      {area.description}
                    </Text>
                  </Stack>
                  {area.action ? (
                    <div>
                      <ActionLink href={area.action.href}>
                        {area.action.label}
                      </ActionLink>
                    </div>
                  ) : null}
                </Stack>
              </Card>
            ))}
          </Grid>
        ) : (
          <EmptyState
            title={data.empty.title}
            description={data.empty.description}
            action={
              <ActionLink href={data.empty.action.href}>
                {data.empty.action.label}
              </ActionLink>
            }
          />
        )}
      </Section>
    </Page>
  );
}
