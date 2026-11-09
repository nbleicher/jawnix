import { Tabs } from "../../../design-system/primitives/tabs";
import type { GallerySection } from "./types";

const ITEMS = ["Summary", "Batches", "Billing", "Rules", "Reports", "Access", "Activity"].map(
  (label) => ({
    key: label.toLowerCase(),
    label,
    // Every tab points back at the gallery: the section shows the style,
    // not a real record.
    href: "/app/design-system",
  }),
);

function RecordTabs() {
  return <Tabs label="Example record sections" items={ITEMS} current="batches" />;
}

export default {
  title: "Record tabs",
  description:
    "Sub-sections of one record. Each tab is its own address; they stay on one line and scroll sideways on phones.",
  order: 27,
  Component: RecordTabs,
} satisfies GallerySection;
