import { useLoaderData } from "react-router";

import { EmptyState } from "../../design-system/primitives/feedback";
import { Page } from "../../design-system/primitives/layout";
import { useDocumentTitle } from "../shell/useDocumentTitle";

export interface PlaceholderData {
  title: string;
  /** The slice that will replace this screen, so the shell states plainly what
   *  is not built yet rather than implying a finished feature. */
  slice: string;
}

/**
 * Builds the route-level loader for a placeholder screen.
 *
 * Trivial today, but it establishes the Data Mode seam: the screen reads its
 * data through `useLoaderData` rather than props, so the slice that replaces
 * this route swaps the loader body and keeps the component contract.
 */
export function placeholderLoader(data: PlaceholderData) {
  return () => data;
}

/**
 * Stands in for a screen that a later slice delivers.
 *
 * It is a real route with a real loader, title, landmark, and heading, so the
 * shell's navigation, announcement, pending, and layout contracts are exercised
 * end-to-end before any screen content exists.
 */
export function PlaceholderRoute() {
  const { title, slice } = useLoaderData<PlaceholderData>();
  useDocumentTitle(title);

  return (
    <Page title={title}>
      <EmptyState title="Not built yet" description={`This screen arrives with ${slice}.`} />
    </Page>
  );
}
