import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./Button";
import { ConfirmDialog, Dialog } from "./Dialog";

describe("Dialog", () => {
  it("is absent from the accessibility tree while closed", () => {
    render(
      <Dialog open={false} onClose={vi.fn()} title="Cancel Batch Request">
        <p>Body</p>
      </Dialog>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("takes its accessible name from the title", () => {
    render(
      <Dialog open onClose={vi.fn()} title="Cancel Batch Request">
        <p>Body</p>
      </Dialog>,
    );

    expect(screen.getByRole("dialog")).toHaveAccessibleName("Cancel Batch Request");
  });

  it("exposes the description to assistive technology", () => {
    render(
      <Dialog open onClose={vi.fn()} title="Cancel Batch Request" description="This cannot be undone.">
        <p>Body</p>
      </Dialog>,
    );

    expect(screen.getByRole("dialog")).toHaveAccessibleDescription("This cannot be undone.");
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="Cancel Batch Request">
        <p>Body</p>
      </Dialog>,
    );

    // A native <dialog> raises `cancel` for Escape; Dialog turns that into onClose.
    screen.getByRole("dialog").dispatchEvent(new Event("cancel", { cancelable: true }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes from the explicit close control", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} title="Cancel Batch Request">
        <p>Body</p>
      </Dialog>,
    );

    await user.click(screen.getByRole("button", { name: "Close dialog" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("returns focus to the control that opened it", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Open</Button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Cancel Batch Request">
            <p>Body</p>
          </Dialog>
        </>
      );
    }

    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close dialog" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe("ConfirmDialog", () => {
  it("states the consequence and requires an explicit confirm", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <ConfirmDialog
        open
        onClose={onClose}
        onConfirm={onConfirm}
        title="Cancel Batch Request"
        consequence="Cancelling is permanent. The request cannot be resumed."
        confirmLabel="Cancel request"
      />,
    );

    expect(screen.getByRole("dialog")).toHaveAccessibleDescription(
      "Cancelling is permanent. The request cannot be resumed.",
    );
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Cancel request" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("keeps the dismissing action separate from the destructive one", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <ConfirmDialog
        open
        onClose={onClose}
        onConfirm={onConfirm}
        title="Cancel Batch Request"
        consequence="Cancelling is permanent."
        confirmLabel="Cancel request"
        cancelLabel="Keep request"
      />,
    );

    await user.click(screen.getByRole("button", { name: "Keep request" }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("does not dismiss on backdrop click, so a destructive prompt is deliberate", async () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={onClose}
        onConfirm={vi.fn()}
        title="Cancel Batch Request"
        consequence="Cancelling is permanent."
        confirmLabel="Cancel request"
      />,
    );

    // A click landing on the <dialog> itself is the backdrop; the panel stops
    // propagation for clicks inside it.
    screen.getByRole("dialog").click();

    expect(onClose).not.toHaveBeenCalled();
  });
});
