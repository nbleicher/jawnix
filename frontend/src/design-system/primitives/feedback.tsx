import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { Button } from "./Button";
import "./feedback.css";

export interface LoadingProps {
  /** Announced to screen readers and shown beneath the indicator. */
  label?: string;
  /** Reserves vertical space so the surrounding layout does not shift when the
   *  real content arrives (CLS, and "does not appear broken"). */
  minHeight?: string;
}

export function Loading({ label = "Loading…", minHeight = "12rem" }: LoadingProps) {
  return (
    <div className="jx-loading" style={{ minHeight }} role="status">
      <span className="jx-loading__spinner" aria-hidden="true" />
      <span className="jx-loading__label">{label}</span>
    </div>
  );
}

export interface SkeletonProps {
  /** Number of placeholder rows. Match it to the expected content so the
   *  reserved space is honest. */
  lines?: number;
  label?: string;
}

/** Placeholder for content whose shape is known. Announced once via a polite
 *  live region rather than per line, so the user hears "Loading…", not a burst
 *  of noise. */
export function Skeleton({ lines = 3, label = "Loading…" }: SkeletonProps) {
  return (
    <div className="jx-skeleton" role="status" aria-label={label}>
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="jx-skeleton__line" aria-hidden="true" />
      ))}
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  /** Why the screen is empty, in plain language. */
  description: string;
  /** The relevant next action, so an empty screen is still useful. */
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="jx-empty">
      <h2 className="jx-empty__title">{title}</h2>
      <p className="jx-empty__description">{description}</p>
      {action ? <div className="jx-empty__action">{action}</div> : null}
    </div>
  );
}

export interface ErrorStateProps {
  title?: string;
  /** Plain-language context. Never a raw backend message or stack trace. */
  description: string;
  onRetry?: () => void;
  retryLabel?: string;
  /** Shown as supporting detail — a correlation id, not an internal error. */
  reference?: string;
}

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
  retryLabel = "Try again",
  reference,
}: ErrorStateProps) {
  return (
    <div className="jx-error" role="alert">
      <h2 className="jx-error__title">{title}</h2>
      <p className="jx-error__description">{description}</p>
      {reference ? <p className="jx-error__reference">Reference: {reference}</p> : null}
      {onRetry ? (
        <div className="jx-error__action">
          <Button variant="primary" onClick={onRetry}>
            {retryLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Rendered instead of the default ErrorState when supplied. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Last-resort boundary for render-time faults.
 *
 * The caught error is logged but never rendered: users get a recovery action,
 * not an internal message. Route-level loader/action failures are handled by
 * React Router's own error elements instead.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Unhandled render error", error, info.componentStack);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  override render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <ErrorState
        description="This part of Jawnix could not be displayed. Retrying usually resolves it."
        onRetry={this.reset}
      />
    );
  }
}
