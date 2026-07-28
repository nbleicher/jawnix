import { useState } from "react";

import { Button } from "../../../design-system/primitives/Button";
import { ConfirmDialog, Dialog } from "../../../design-system/primitives/Dialog";
import { Card, Cluster } from "../../../design-system/primitives/layout";
import { Text } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

function Dialogs() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <>
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
    </>
  );
}

export default { title: "Dialogs", order: 60, Component: Dialogs } satisfies GallerySection;
