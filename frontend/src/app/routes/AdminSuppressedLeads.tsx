import type { LoaderFunctionArgs } from "react-router";
import { Link, useLoaderData } from "react-router";

import { Page } from "../../design-system/primitives/layout";
import { DataTable } from "../../design-system/primitives/table";
import type { DataColumn } from "../../design-system/primitives/table";
import { Text } from "../../design-system/primitives/typography";
import { api } from "../api";
import { routePath } from "../routePath";
import { ControlActionBar, leadControlRequest } from "../operator/actions";
import type { ControlAction } from "../operator/actions";
import { formatPhone } from "../operator/format";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { BackToInventory, Pager, pageParam } from "./inventoryTools";
import type { Paged } from "./inventoryTools";
import "./AdminInventory.css";

/** Suppressed leads: the leads withheld from every Customer, with the
 *  restore action that takes a reason and is recorded in Activity. */

export interface SuppressedLeadRow {
  id: number;
  phone: string;
  title: string;
  state: string;
  reason: string;
  href: string;
  actions: ControlAction[];
}

export interface SuppressedLeadsData extends Paged<SuppressedLeadRow> {
  restoreNotice: string;
}

export async function adminSuppressedLeadsLoader({
  request,
}: LoaderFunctionArgs): Promise<SuppressedLeadsData> {
  return api<SuppressedLeadsData>(`/api/admin/suppressed-leads${pageParam(request)}`);
}

export function AdminSuppressedLeadsRoute() {
  const data = useLoaderData<SuppressedLeadsData>();
  useDocumentTitle("Suppressed leads");
  const columns: DataColumn<SuppressedLeadRow>[] = [
    {
      key: "phone",
      header: "Phone",
      cell: (row) => <Link to={routePath(row.href)}>{formatPhone(row.phone)}</Link>,
    },
    { key: "title", header: "Title", cell: (row) => row.title || "No title" },
    { key: "state", header: "State", cell: (row) => row.state || "—" },
    { key: "reason", header: "Reason", cell: (row) => row.reason || "No reason recorded" },
    {
      key: "action",
      header: "Action",
      cell: (row) => (
        <ControlActionBar
          actions={row.actions}
          resolve={leadControlRequest(row.id)}
          settled="Restored"
        />
      ),
    },
  ];
  return (
    <Page
      title="Suppressed leads"
      width="data"
      density="data"
      description="Leads withheld from every Customer. Restoring one takes a reason and is recorded in Activity."
    >
      <Text size="sm" tone="muted">
        {data.restoreNotice}
      </Text>
      <DataTable
        label="Suppressed leads"
        columns={columns}
        rows={data.items}
        rowKey={(row) => String(row.id)}
        hasActions
        empty="No leads are suppressed. Suppressing a lead from lead lookup or a Lead Report puts it here."
      />
      <Pager path="/admin/inventory/suppressed" data={data} noun="suppressed leads" />
      <BackToInventory />
    </Page>
  );
}
