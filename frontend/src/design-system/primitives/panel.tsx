import { useEffect, useId, useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";

import "./panel.css";
import { ActionLink } from "./Button";
import { cx } from "./cx";
import { Heading, LabelText, VisuallyHidden } from "./typography";

export interface PanelProps {
  title: string;
  /** Rendered beside the title, e.g. a count or a small link. */
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}

/** A boxed panel for the right column of a record page (V2). */
export function Panel({ title, action, className, children }: PanelProps) {
  const id = useId();
  return (
    <section className={cx("jx-panel", className)} aria-labelledby={id}>
      <div className="jx-panel__title">
        <Heading level={2} size="sm" id={id}>
          {title}
        </Heading>
        {action}
      </div>
      {children}
    </section>
  );
}

export interface RecordColumnsProps {
  /** Sections: headings outside, a table or fact list below (V2). */
  main: ReactNode;
  /** Boxed panels. Drops below the main column on narrow screens. */
  aside: ReactNode;
}

export function RecordColumns({ main, aside }: RecordColumnsProps) {
  return (
    <div className="jx-record-columns">
      <div className="jx-record-columns__main">{main}</div>
      <div className="jx-record-columns__aside">{aside}</div>
    </div>
  );
}

export interface PanelLayoutProps {
  /** The list. It stays in view while a row is open beside it. */
  children: ReactNode;
  /** The open side panel, or nothing. */
  panel?: ReactNode;
}

/** A list with its side panel beside it; on narrow screens the panel follows
 *  the list. */
export function PanelLayout({ children, panel }: PanelLayoutProps) {
  return (
    <div className={cx("jx-panel-layout", panel ? "jx-panel-layout--open" : null)}>
      <div className="jx-panel-layout__list">{children}</div>
      {panel ? <div className="jx-panel-layout__panel">{panel}</div> : null}
    </div>
  );
}

export interface SidePanelProps {
  /** The small label above the title, e.g. "Batch · Awaiting approval". */
  eyebrow: string;
  title: string;
  onClose: () => void;
  /** The record's own page. "Open full page" is always the last thing. */
  fullHref?: string | undefined;
  /** Buttons at the bottom of the panel (V2). */
  actions?: ReactNode;
  /** Move focus to the panel when it opens. True when the operator opened
   *  it; false when it arrives open from a shared link. */
  autoFocus?: boolean | undefined;
  /** Where focus returns when the panel closes: the row that opened it. */
  returnFocus?: (() => HTMLElement | null) | undefined;
  children?: ReactNode;
}

/**
 * The side panel (C): a record opened beside the list it came from.
 *
 * Not a dialog: the list stays visible and usable, so the panel is a named
 * region rather than a modal. Escape and the close button close it. Focus
 * moves to its heading when the operator opens it, and returns to the row
 * that opened it when it closes — but only if focus was inside the panel, so
 * opening another row is not undone.
 */
export function SidePanel({
  eyebrow,
  title,
  onClose,
  fullHref,
  actions,
  autoFocus = false,
  returnFocus,
  children,
}: SidePanelProps) {
  const id = useId();
  const panelRef = useRef<HTMLElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const closeRef = useRef(onClose);
  const returnRef = useRef(returnFocus);
  closeRef.current = onClose;
  returnRef.current = returnFocus;

  // A layout effect, so focus has moved before anything else runs against
  // the newly opened panel.
  useLayoutEffect(() => {
    if (autoFocus) headingRef.current?.focus();
    const panel = panelRef.current;
    return () => {
      const active = document.activeElement;
      if (!active || active === document.body || panel?.contains(active)) {
        returnRef.current?.()?.focus();
      }
    };
  }, [autoFocus]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      // A confirmation opened from the panel owns Escape until it closes.
      if (document.querySelector("dialog[open]")) return;
      event.preventDefault();
      closeRef.current();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <aside className="jx-side-panel" aria-labelledby={id} ref={panelRef}>
      <div className="jx-side-panel__head">
        <div className="jx-side-panel__heading">
          <LabelText>{eyebrow}</LabelText>
          {/* The heading itself rather than <Heading>, which takes no ref. */}
          <h2
            className="jx-heading jx-heading--md jx-side-panel__title"
            id={id}
            ref={headingRef}
            tabIndex={-1}
          >
            {title}
          </h2>
        </div>
        <button
          type="button"
          className="jx-side-panel__close"
          aria-label="Close panel"
          onClick={() => closeRef.current()}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <div className="jx-side-panel__body">{children}</div>
      {actions ? <div className="jx-side-panel__actions">{actions}</div> : null}
      {fullHref ? (
        <div className="jx-side-panel__full">
          <ActionLink href={fullHref}>Open full page</ActionLink>
        </div>
      ) : null}
    </aside>
  );
}

export interface Step {
  key: string;
  label: string;
  done: boolean;
  /** Already formatted, e.g. "09:41". */
  time?: string | null;
}

/** Where a record has got to: reached steps filled, the rest open. */
export function Steps({ label, steps }: { label: string; steps: Step[] }) {
  return (
    <ol className="jx-steps" aria-label={label}>
      {steps.map((step) => (
        <li
          key={step.key}
          className={cx(
            "jx-steps__step",
            step.done ? "jx-steps__step--done" : "jx-steps__step--todo",
          )}
        >
          <span className="jx-steps__mark" aria-hidden="true" />
          <span>
            {step.label}
            <VisuallyHidden>{step.done ? ", done" : ", not yet"}</VisuallyHidden>
          </span>
          <span className="jx-steps__time">{step.time ?? ""}</span>
        </li>
      ))}
    </ol>
  );
}
