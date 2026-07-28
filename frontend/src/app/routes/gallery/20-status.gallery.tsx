import { Card, Cluster } from "../../../design-system/primitives/layout";
import { StatusBadge } from "../../../design-system/primitives/status";
import type { StatusTone } from "../../../design-system/primitives/status";
import type { GallerySection } from "./types";

const STATUS_TONES: { tone: StatusTone; label: string }[] = [
  { tone: "neutral", label: "Draft" },
  { tone: "info", label: "Under review" },
  { tone: "success", label: "Delivered" },
  { tone: "warning", label: "Waiting for inventory" },
  { tone: "danger", label: "Rejected" },
];

function Status() {
  return (
    <Card>
      <Cluster gap={2}>
        {STATUS_TONES.map((status) => (
          <StatusBadge key={status.tone} tone={status.tone}>
            {status.label}
          </StatusBadge>
        ))}
      </Cluster>
    </Card>
  );
}

export default { title: "Status", order: 20, Component: Status } satisfies GallerySection;
