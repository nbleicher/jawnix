import { Card, Stack } from "../../../design-system/primitives/layout";
import { Heading, LabelText, Mono, Text } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

function Typography() {
  return (
    <Card>
      <Stack gap={3}>
        <Heading level={2} size="lg">
          Heading large
        </Heading>
        <Heading level={3} size="md">
          Heading medium
        </Heading>
        <Text>
          Interface copy uses the sans face at a comfortable measure. Monospace is reserved for identifiers such as{" "}
          <Mono>REQ-2026-0114</Mono> and dense numeric data.
        </Text>
        <Text size="sm" tone="muted">
          Muted supporting copy.
        </Text>
        <LabelText>Section label</LabelText>
      </Stack>
    </Card>
  );
}

export default { title: "Typography", order: 10, Component: Typography } satisfies GallerySection;
