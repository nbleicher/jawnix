import { useEffect, useId, useRef } from "react";
import type { MouseEvent, ReactNode } from "react";

import { Button } from "./Button";
import "./Dialog.css";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  /** Action row. Rendered stacked on mobile and inline on desktop. */
  footer?: ReactNode;
  /** Clicking the backdrop dismisses by default. Confirmation prompts turn this
   *  off so a destructive action is never dismissed by a stray click. */
  dismissOnBackdrop?: boolean;
  children?: ReactNode;
}

/**
 * Modal dialog built on the native `<dialog>` element.
 *
 * Using the platform element buys real focus containment, background inertness,
 * top-layer stacking, and Escape handling from the browser rather than a
 * hand-rolled trap. What this component adds on top is the labelling contract,
 * React-driven open state, and focus restoration.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  footer,
  dismissOnBackdrop = true,
  children,
}: DialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<Element | null>(null);
  const generatedId = useId();
  const titleId = `${generatedId}-title`;
  const descriptionId = `${generatedId}-description`;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open) {
      // Remember the opener before the browser moves focus into the dialog.
      openerRef.current = document.activeElement;
      if (!dialog.open) dialog.showModal();
      return;
    }

    if (dialog.open) dialog.close();
  }, [open]);

  // Restore focus on unmount-while-open (route change closing the dialog), which
  // the browser's own restoration does not cover.
  useEffect(() => {
    if (open) return;
    const opener = openerRef.current;
    openerRef.current = null;
    if (opener instanceof HTMLElement && document.contains(opener)) {
      opener.focus();
    }
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    // Escape fires `cancel`; prevent the default close so React state stays the
    // single source of truth for whether the dialog is open.
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [onClose]);

  const handleBackdropClick = (event: MouseEvent<HTMLDialogElement>) => {
    if (!dismissOnBackdrop) return;
    // The panel stops propagation, so any click reaching the <dialog> itself
    // landed on the backdrop.
    if (event.target === dialogRef.current) onClose();
  };

  if (!open) {
    // Keeping the element mounted while closed would leave it in the a11y tree
    // in engines that expose `display: none` dialogs inconsistently.
    return null;
  }

  return (
    <dialog
      ref={dialogRef}
      className="jx-dialog"
      aria-labelledby={titleId}
      {...(description ? { "aria-describedby": descriptionId } : {})}
      onClick={handleBackdropClick}
    >
      <div className="jx-dialog__panel" onClick={(event) => event.stopPropagation()}>
        <header className="jx-dialog__header">
          <h2 className="jx-dialog__title" id={titleId}>
            {title}
          </h2>
          <button type="button" className="jx-dialog__close" onClick={onClose} aria-label="Close dialog">
            <span aria-hidden="true">×</span>
          </button>
        </header>
        {description ? (
          <p className="jx-dialog__description" id={descriptionId}>
            {description}
          </p>
        ) : null}
        {children ? <div className="jx-dialog__body">{children}</div> : null}
        {footer ? <footer className="jx-dialog__footer">{footer}</footer> : null}
      </div>
    </dialog>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  /** Plain-language statement of what happens — shown as the dialog's
   *  description so it is announced with the dialog name. */
  consequence: string;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  children?: ReactNode;
}

/**
 * Confirmation for a consequential action. The consequence is mandatory: a
 * confirmation that only asks "are you sure?" gives the user nothing to decide
 * with.
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  consequence,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = true,
  busy = false,
  children,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={consequence}
      dismissOnBackdrop={false}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button variant={destructive ? "danger" : "primary"} onClick={onConfirm} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
