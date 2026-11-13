import { useSearchParams } from "react-router";

import { ActionLink, ButtonLink } from "../../design-system/primitives/Button";
import { Cluster } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";
import { formatCount } from "../operator/format";
import "./AdminInventory.css";

/** The bits the Inventory tool pages share: one paged-table contract, the
 *  Pager that walks it, and the link back to Inventory. */

export interface Paged<Row> {
  items: Row[];
  page: number;
  pageSize: number;
  total: number;
  pages: number;
}

function pageHref(path: string, params: URLSearchParams, page: number): string {
  const next = new URLSearchParams(params);
  if (page <= 1) next.delete("page");
  else next.set("page", String(page));
  const query = next.toString();
  return `/app${path}${query ? `?${query}` : ""}`;
}

/** "Showing 1–25 of 61" with Previous and Next, for a paged table. */
export function Pager({ path, data, noun }: { path: string; data: Paged<unknown>; noun: string }) {
  const [params] = useSearchParams();
  const first = data.total ? (data.page - 1) * data.pageSize + 1 : 0;
  const last = Math.min(data.total, data.page * data.pageSize);
  return (
    <Cluster gap={3} justify="space-between">
      <Text size="sm" tone="muted">
        {data.total
          ? `Showing ${formatCount(first)}–${formatCount(last)} of ${formatCount(data.total)}`
          : `No ${noun}`}
      </Text>
      {data.pages > 1 ? (
        <nav aria-label={`${noun.charAt(0).toUpperCase()}${noun.slice(1)} pages`} className="jx-pages">
          {data.page > 1 ? (
            <ButtonLink variant="secondary" href={pageHref(path, params, data.page - 1)}>
              Previous
            </ButtonLink>
          ) : null}
          <Text as="span" size="sm">
            Page {data.page} of {data.pages}
          </Text>
          {data.page < data.pages ? (
            <ButtonLink variant="secondary" href={pageHref(path, params, data.page + 1)}>
              Next
            </ButtonLink>
          ) : null}
        </nav>
      ) : null}
    </Cluster>
  );
}

export function BackToInventory() {
  return <ActionLink href="/app/admin/inventory">Back to Inventory</ActionLink>;
}

export function pageParam(request: Request): string {
  const page = new URL(request.url).searchParams.get("page");
  return page && /^\d+$/.test(page) ? `?page=${page}` : "";
}
