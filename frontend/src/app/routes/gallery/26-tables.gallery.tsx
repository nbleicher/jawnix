import { useState } from "react";

import { Button } from "../../../design-system/primitives/Button";
import { FactList } from "../../../design-system/primitives/facts";
import { Stack } from "../../../design-system/primitives/layout";
import { Panel, PanelLayout, SidePanel, Steps } from "../../../design-system/primitives/panel";
import { StatusBadge } from "../../../design-system/primitives/status";
import { DataTable } from "../../../design-system/primitives/table";
import { Text } from "../../../design-system/primitives/typography";
import type { GallerySection } from "./types";

interface Row {
  number: number;
  customer: string;
  leads: number;
  status: string;
  tone: "danger" | "info" | "neutral";
}

const ROWS: Row[] = [
  { number: 1039, customer: "Ridgeline Benefits", leads: 100_000, status: "Waiting", tone: "danger" },
  { number: 1041, customer: "Harbor Insurance", leads: 50_000, status: "Generating", tone: "info" },
  { number: 1042, customer: "Northstar Insurance", leads: 25_000, status: "Awaiting approval", tone: "danger" },
];

function TablesAndPanels() {
  const [open, setOpen] = useState<number | null>(1042);
  return (
    <Stack gap={5}>
      <PanelLayout
        panel={
          open ? (
            <SidePanel
              eyebrow="Batch · Awaiting approval"
              title={`#${open} · Northstar Insurance`}
              onClose={() => setOpen(null)}
              fullHref="/app/design-system"
              actions={<Button variant="primary">Approve</Button>}
            >
              <FactList
                label="Gallery batch"
                items={[
                  { term: "Leads", description: "25,000 · FL · DB1" },
                  { term: "Result", description: <StatusBadge tone="success">Fillable in full</StatusBadge> },
                  { term: "Cost", description: "$125.00 at $5.00 per 1,000" },
                ]}
              />
            </SidePanel>
          ) : null
        }
      >
        <DataTable
          label="Gallery batches"
          columns={[
            {
              key: "batch",
              header: "Batch",
              cell: (row) => (
                <a
                  href={`#batch-${row.number}`}
                  onClick={(event) => {
                    event.preventDefault();
                    setOpen(row.number);
                  }}
                >
                  #{row.number}
                </a>
              ),
            },
            { key: "customer", header: "Customer", cell: (row) => row.customer },
            {
              key: "leads",
              header: "Leads",
              numeric: true,
              cell: (row) => row.leads.toLocaleString("en-US"),
              footer: "175,000",
            },
            {
              key: "status",
              header: "Status",
              cell: (row) => <StatusBadge tone={row.tone}>{row.status}</StatusBadge>,
            },
          ]}
          rows={ROWS}
          rowKey={(row) => String(row.number)}
          selectedKey={open ? String(open) : null}
          onRowClick={(row) => setOpen(row.number)}
        />
      </PanelLayout>
      <Panel title="Progress">
        <Steps
          label="Gallery progress"
          steps={[
            { key: "submitted", label: "Submitted", done: true, time: "09:41" },
            { key: "approved", label: "Approved", done: false },
            { key: "delivered", label: "Delivered", done: false },
          ]}
        />
        <Text size="sm" tone="muted">
          Boxed panels belong to the right column of a record page.
        </Text>
      </Panel>
    </Stack>
  );
}

export default {
  title: "Tables and panels",
  description:
    "Data tables with a selected row, the side panel that opens beside them, fact lists, and progress steps.",
  order: 26,
  Component: TablesAndPanels,
} satisfies GallerySection;
