import { DetailList } from "../../../design-system/primitives/detail";
import { Card } from "../../../design-system/primitives/layout";
import { StatusBadge } from "../../../design-system/primitives/status";
import { Mono } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

function Detail() {
  return (
    <Card>
      <DetailList
        label="Batch Request"
        items={[
          { term: "Customer", description: "Northstar Insurance" },
          { term: "Agency", description: "Northstar Agency" },
          { term: "Quantity", description: "250 Leads" },
          { term: "Licensed State scope", description: "FL, GA, TX" },
          {
            term: "Status",
            description: (
              <StatusBadge tone="warning">Waiting for inventory</StatusBadge>
            ),
          },
          {
            term: "Checksum",
            description: <Mono>7f3a9c2e51b8d4460af1</Mono>,
          },
        ]}
      />
    </Card>
  );
}

export default {
  title: "Detail lists",
  order: 80,
  Component: Detail,
} satisfies GallerySection;
