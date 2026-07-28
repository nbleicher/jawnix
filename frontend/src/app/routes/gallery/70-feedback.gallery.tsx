import { Button } from "../../../design-system/primitives/Button";
import { EmptyState, ErrorState, Loading, Skeleton } from "../../../design-system/primitives/feedback";
import { Card, Grid } from "../../../design-system/primitives/layout";
import type { GallerySection } from "./types";

function Feedback() {
  return (
    <Grid minColumnWidth="20rem">
      <Card>
        <Loading label="Loading Batch Requests…" minHeight="8rem" />
      </Card>
      <Card>
        <Skeleton lines={4} label="Loading Customer directory…" />
      </Card>
      <Card padding={0}>
        <EmptyState
          title="No Batch Requests yet"
          description="Requesting a Batch is the fastest way to get leads delivered."
          action={<Button variant="primary">Request a Batch</Button>}
        />
      </Card>
      <Card padding={0}>
        <ErrorState
          description="Jawnix could not reach the Scraper service. The last successful run is shown below."
          reference="TRACE-8f21c0"
          onRetry={() => undefined}
        />
      </Card>
    </Grid>
  );
}

export default { title: "Loading, empty, and error", order: 70, Component: Feedback } satisfies GallerySection;
