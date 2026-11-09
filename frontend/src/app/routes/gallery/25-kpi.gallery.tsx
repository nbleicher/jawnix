import { Kpi, KpiRow } from "../../../design-system/primitives/kpi";
import { Stack } from "../../../design-system/primitives/layout";
import type { GallerySection } from "./types";

function Numbers() {
  return (
    <Stack gap={4}>
      <KpiRow label="Page numbers, four per row">
        <Kpi label="DB1 usable" value="9,183,402" meta="Operator leads · as of 06:00" />
        <Kpi label="Open batches" value="4" meta="1 awaiting approval · 1 short" />
        <Kpi label="Credit balance" value="$8,412.50" meta="11 billed customers" />
        <Kpi label="Decisions waiting" value="7" meta="Oldest 2d" alert />
      </KpiRow>
      <KpiRow label="Side panel numbers, two per row" columns={2}>
        <Kpi label="Balance" value="$1,232.00" meta="$250.00 held for Batch #1041" />
        <Kpi label="Available" value="412,880" meta="DB1 · FL, TX" />
      </KpiRow>
    </Stack>
  );
}

export default {
  title: "Numbers row",
  description:
    "Labelled figures a page with totals starts with. Red figures are reserved for problems that need action.",
  order: 25,
  Component: Numbers,
} satisfies GallerySection;
