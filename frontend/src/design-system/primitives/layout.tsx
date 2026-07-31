import type { CSSProperties, ElementType, HTMLAttributes, ReactNode } from "react";

import "./layout.css";
import { cx } from "./cx";

type SpaceStep = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

const spaceVar = (step: SpaceStep) => `var(--jx-space-${step})`;

export interface StackProps extends HTMLAttributes<HTMLElement> {
  /** Vertical rhythm between children, in design-system space steps. */
  gap?: SpaceStep;
  align?: CSSProperties["alignItems"];
  as?: ElementType;
  children?: ReactNode;
}

/** Vertical flow. The single spacing primitive — children never set their own
 *  outer margins, so rhythm stays owned by the container. */
export function Stack({ gap = 4, align, as: Component = "div", style, className, ...rest }: StackProps) {
  return (
    <Component
      className={cx("jx-stack", className)}
      style={{ "--jx-stack-gap": spaceVar(gap), alignItems: align, ...style } as CSSProperties}
      {...rest}
    />
  );
}

export interface ClusterProps extends HTMLAttributes<HTMLElement> {
  gap?: SpaceStep;
  align?: CSSProperties["alignItems"];
  justify?: CSSProperties["justifyContent"];
  as?: ElementType;
  children?: ReactNode;
}

/** Horizontal grouping that wraps rather than overflowing — the mobile-first
 *  counterpart to a row. */
export function Cluster({
  gap = 3,
  align = "center",
  justify,
  as: Component = "div",
  style,
  className,
  ...rest
}: ClusterProps) {
  return (
    <Component
      className={cx("jx-cluster", className)}
      style={
        { "--jx-cluster-gap": spaceVar(gap), alignItems: align, justifyContent: justify, ...style } as CSSProperties
      }
      {...rest}
    />
  );
}

export interface GridProps extends HTMLAttributes<HTMLElement> {
  gap?: SpaceStep;
  /** Minimum column width before the grid drops to fewer columns. Single column
   *  on mobile falls out of this automatically. */
  minColumnWidth?: string;
  as?: ElementType;
  children?: ReactNode;
}

/** Responsive auto-fitting grid. No breakpoints — columns are a function of
 *  available width, which keeps dense desktop layouts and mobile cards on one
 *  code path. */
export function Grid({
  gap = 4,
  minColumnWidth = "18rem",
  as: Component = "div",
  style,
  className,
  ...rest
}: GridProps) {
  return (
    <Component
      className={cx("jx-grid", className)}
      style={{ "--jx-grid-gap": spaceVar(gap), "--jx-grid-min": minColumnWidth, ...style } as CSSProperties}
      {...rest}
    />
  );
}

export interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType;
  padding?: SpaceStep;
  children?: ReactNode;
}

export function Card({ as: Component = "div", padding, style, className, ...rest }: CardProps) {
  return (
    <Component
      className={cx("jx-card", className)}
      style={{ ...(padding === undefined ? {} : { "--jx-card-padding": spaceVar(padding) }), ...style } as CSSProperties}
      {...rest}
    />
  );
}

export interface PageProps {
  title: string;
  description?: string;
  /** Tightens passive spacing on information-heavy screens without reducing
   *  the 44px target contract for their controls. */
  density?: "default" | "data";
  /** Primary page actions. Rendered below the title on mobile, inline on
   *  desktop — never hidden behind a menu. */
  actions?: ReactNode;
  children?: ReactNode;
}

/** A routed screen. Owns the single <h1> and the page-level heading structure so
 *  no route has to remember the landmark contract. */
export function Page({ title, description, density = "default", actions, children }: PageProps) {
  return (
    <div className={cx("jx-page", density === "data" && "jx-page--data")}>
      {/* A plain div, not <header>: a <header> inside <main> is generic in
          browsers but is reported as a second `banner` by some DOM
          accessibility implementations. The <h1> already carries the structure. */}
      <div className="jx-page__header">
        <div className="jx-page__heading">
          <h1 className="jx-page__title">{title}</h1>
          {description ? <p className="jx-page__description">{description}</p> : null}
        </div>
        {actions ? <div className="jx-page__actions">{actions}</div> : null}
      </div>
      <div className="jx-page__body">{children}</div>
    </div>
  );
}

export interface SectionProps {
  id?: string;
  title: string;
  description?: string;
  children?: ReactNode;
}

/** A titled region inside a Page. Renders a real <section> with an accessible
 *  name so screen-reader users can navigate by region. */
export function Section({ id, title, description, children }: SectionProps) {
  return (
    <section
      className="jx-section"
      aria-label={title}
      {...(id ? { id } : {})}
    >
      <div className="jx-section__header">
        <h2 className="jx-section__title">{title}</h2>
        {description ? <p className="jx-section__description">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}
