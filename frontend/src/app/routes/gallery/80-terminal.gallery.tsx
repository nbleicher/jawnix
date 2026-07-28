import { Card, Grid, Stack } from "../../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../../design-system/primitives/terminal";
import { Text } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

function Terminal() {
  return (
    <TerminalWorkspace status="ONLINE / PRIVILEGED">
      <Grid minColumnWidth="14rem">
        <Card>
          <Stack gap={2}>
            <Text size="sm" tone="muted">Service connection</Text>
            <Text weight="bold">Connected through Jawnix</Text>
          </Stack>
        </Card>
        <Card>
          <Stack gap={2}>
            <Text size="sm" tone="muted">Privileged session</Text>
            <Text weight="bold">Fresh challenge verified</Text>
          </Stack>
        </Card>
      </Grid>
    </TerminalWorkspace>
  );
}

export default {
  title: "Scraper terminal workspace",
  description: "The native GMS/OPS frame, rail, status, and dense operational panels.",
  order: 80,
  Component: Terminal,
} satisfies GallerySection;
