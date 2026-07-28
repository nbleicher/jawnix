import type { ComponentType } from "react";

/**
 * A design-system gallery entry.
 *
 * Each `*.gallery.tsx` file default-exports one of these. The gallery route
 * discovers them by glob, so adding a section means adding a file — no shared
 * list, no shared import block, and therefore no merge conflict between parallel
 * slices. See DesignSystem.tsx for why that matters.
 */
export interface GallerySection {
  /** Section heading, and the accessible name of its region. */
  title: string;
  description?: string;
  /** Sort key. Files are ordered by this, then by filename, so sections keep a
   *  stable position for visual-regression comparison. Leave gaps. */
  order: number;
  Component: ComponentType;
}
