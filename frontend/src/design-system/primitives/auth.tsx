import { useId } from "react";
import type { ElementType, ReactNode } from "react";

import "./auth.css";

export interface AuthPanelProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
  headingLevel?: 1 | 2;
}

/**
 * The compact, mobile-first surface shared by credential and recovery routes.
 *
 * The heading level is explicit because the routed version owns the page h1,
 * while the design-system gallery demonstrates the same primitive below its
 * existing h1.
 */
export function AuthPanel({
  title,
  description,
  children,
  footer,
  headingLevel = 1,
}: AuthPanelProps) {
  const Heading = `h${headingLevel}` as ElementType;
  const titleId = useId();

  return (
    <section className="jx-auth-panel" aria-labelledby={titleId}>
      <div className="jx-auth-panel__heading">
        <Heading className="jx-auth-panel__title" id={titleId}>
          {title}
        </Heading>
        <p className="jx-auth-panel__description">{description}</p>
      </div>
      <div className="jx-auth-panel__body">{children}</div>
      {footer ? <div className="jx-auth-panel__footer">{footer}</div> : null}
    </section>
  );
}
