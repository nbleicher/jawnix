import { AuthPanel } from "../../../design-system/primitives/auth";
import { Button } from "../../../design-system/primitives/Button";
import { Field, Input } from "../../../design-system/primitives/form";
import { Stack } from "../../../design-system/primitives/layout";
import { Text } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

function Authentication() {
  return (
    <AuthPanel
      headingLevel={2}
      title="Sign in"
      description="Use the email address and password for your Customer account."
      footer={<Text size="sm">Recovery guidance appears here.</Text>}
    >
      <Stack gap={4}>
        <Field label="Email address" required>
          <Input type="email" autoComplete="email" />
        </Field>
        <Field label="Password" required>
          <Input type="password" autoComplete="current-password" />
        </Field>
        <Button variant="primary" fullWidth>
          Sign in
        </Button>
      </Stack>
    </AuthPanel>
  );
}

export default {
  title: "Authentication",
  description: "A narrow, mobile-first panel for public credential and recovery routes.",
  order: 50,
  Component: Authentication,
} satisfies GallerySection;
