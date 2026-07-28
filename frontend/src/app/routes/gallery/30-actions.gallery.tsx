import { ActionLink, Button } from "../../../design-system/primitives/Button";
import { Card, Cluster, Stack } from "../../../design-system/primitives/layout";
import type { GallerySection } from "./types";

function Actions() {
  return (
    <Card>
      <Stack gap={4}>
        <Cluster gap={2}>
          <Button variant="primary">Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <ActionLink href="#jx-main">Navigation action</ActionLink>
        </Cluster>
        <Cluster gap={2}>
          <Button variant="primary" busy>
            Submitting
          </Button>
          <Button variant="secondary" disabled>
            Disabled
          </Button>
        </Cluster>
      </Stack>
    </Card>
  );
}

export default { title: "Actions", order: 30, Component: Actions } satisfies GallerySection;
