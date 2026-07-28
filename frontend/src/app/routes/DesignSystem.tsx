import { useState } from "react";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../design-system/primitives/Dialog";
import { EmptyState, ErrorState, Loading, Skeleton } from "../../design-system/primitives/feedback";
import { Field, Fieldset, Input, Select, Textarea } from "../../design-system/primitives/form";
import { Card, Cluster, Grid, Page, Section, Stack } from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import type { StatusTone } from "../../design-system/primitives/status";
import { Heading, LabelText, Mono, Text } from "../../design-system/primitives/typography";
import { useTheme } from "../../design-system/theme/ThemeProvider";
import type { Theme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";

const STATUS_TONES: { tone: StatusTone; label: string }[] = [
  { tone: "neutral", label: "Draft" },
  { tone: "info", label: "Under review" },
  { tone: "success", label: "Delivered" },
  { tone: "warning", label: "Waiting for inventory" },
  { tone: "danger", label: "Rejected" },
];

const THEMES: { value: Theme; label: string }[] = [
  { value: "jawnix", label: "Jawnix" },
  { value: "terminal", label: "Terminal" },
];

/**
 * Gallery of every design-system primitive.
 *
 * This is a working fixture rather than documentation: the accessibility sweep
 * and the visual-regression baseline both run against this one route, so a
 * primitive that regresses in either theme fails a test instead of shipping.
 */
export function DesignSystemRoute() {
  useDocumentTitle("Design system");
  const { theme, setTheme } = useTheme();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <Page
      title="Design system"
      description="Every shell primitive, in both themes. Used as the accessibility and visual-regression fixture."
      actions={
        <Cluster gap={2}>
          {THEMES.map((option) => (
            <Button
              key={option.value}
              variant={theme === option.value ? "primary" : "secondary"}
              aria-pressed={theme === option.value}
              onClick={() => setTheme(option.value)}
            >
              {option.label}
            </Button>
          ))}
        </Cluster>
      }
    >
      <Section title="Typography">
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
      </Section>

      <Section title="Status">
        <Card>
          <Cluster gap={2}>
            {STATUS_TONES.map((status) => (
              <StatusBadge key={status.tone} tone={status.tone}>
                {status.label}
              </StatusBadge>
            ))}
          </Cluster>
        </Card>
      </Section>

      <Section title="Actions">
        <Card>
          <Stack gap={4}>
            <Cluster gap={2}>
              <Button variant="primary">Primary</Button>
              <Button variant="secondary">Secondary</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Danger</Button>
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
      </Section>

      <Section title="Forms">
        <Card>
          <Stack gap={4}>
            <Field label="Delivery email" description="Batches are sent here." required>
              <Input type="email" name="email" placeholder="name@example.com" autoComplete="email" />
            </Field>
            <Field label="Quantity" error="Enter a quantity of 500 or fewer.">
              <Input type="number" name="quantity" defaultValue={900} />
            </Field>
            <Field label="Licensed state">
              <Select name="state" defaultValue="">
                <option value="">Choose a state…</option>
                <option value="pa">Pennsylvania</option>
                <option value="nj">New Jersey</option>
              </Select>
            </Field>
            <Field label="Note" description="Required when the disposition is Other.">
              <Textarea name="note" />
            </Field>
            <Fieldset legend="Quality rating" description="Independent of the disposition.">
              <Cluster gap={4}>
                <label>
                  <input type="radio" name="quality" value="good" /> Good
                </label>
                <label>
                  <input type="radio" name="quality" value="poor" /> Poor
                </label>
              </Cluster>
            </Fieldset>
          </Stack>
        </Card>
      </Section>

      <Section title="Dialogs">
        <Card>
          <Cluster gap={2}>
            <Button variant="secondary" onClick={() => setDialogOpen(true)}>
              Open dialog
            </Button>
            <Button variant="danger" onClick={() => setConfirmOpen(true)}>
              Open confirmation
            </Button>
          </Cluster>
        </Card>

        <Dialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          title="Batch Request details"
          description="A standard dialog. Escape and the backdrop both dismiss it."
          footer={
            <Button variant="primary" onClick={() => setDialogOpen(false)}>
              Done
            </Button>
          }
        >
          <Text size="sm">Dialog body content sits here.</Text>
        </Dialog>

        <ConfirmDialog
          open={confirmOpen}
          onClose={() => setConfirmOpen(false)}
          onConfirm={() => setConfirmOpen(false)}
          title="Cancel Batch Request"
          consequence="Cancelling is permanent. The request cannot be resumed, and any reserved inventory is released."
          confirmLabel="Cancel request"
        />
      </Section>

      <Section title="Loading, empty, and error">
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
      </Section>
    </Page>
  );
}
