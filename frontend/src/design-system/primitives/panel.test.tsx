import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { SidePanel } from "./panel";
import { DataTable } from "./table";

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter>
      <button type="button" data-panel-trigger="row-1" onClick={() => setOpen(true)}>
        Open row
      </button>
      {open ? (
        <SidePanel
          eyebrow="Batch · Awaiting approval"
          title="#1042 · Northstar Insurance"
          onClose={() => {
            onClose?.();
            setOpen(false);
          }}
          fullHref="/app/admin/batches/1042"
          autoFocus
          returnFocus={() => document.querySelector<HTMLElement>('[data-panel-trigger="row-1"]')}
          actions={<button type="button">Approve</button>}
        >
          <p>Body</p>
        </SidePanel>
      ) : null}
    </MemoryRouter>
  );
}

describe("SidePanel", () => {
  it("is a named region with its actions and the full page last", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open row" }));

    const panel = screen.getByRole("complementary", { name: "#1042 · Northstar Insurance" });
    expect(within(panel).getByText("Batch · Awaiting approval")).toBeVisible();
    const links = within(panel).getAllByRole("link");
    expect(links.at(-1)).toHaveTextContent("Open full page");
    expect(links.at(-1)).toHaveAttribute("href", "/app/admin/batches/1042");
  });

  it("moves focus to its heading and returns it to the row on close", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Open row" }));

    expect(screen.getByRole("heading", { name: "#1042 · Northstar Insurance" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Close panel" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.getByRole("button", { name: "Open row" })).toHaveFocus();
  });

  it("closes on Escape, but not while a confirmation is open", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Open row" }));

    const dialog = document.createElement("dialog");
    dialog.setAttribute("open", "");
    document.body.append(dialog);
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    dialog.remove();

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Open row" })).toHaveFocus();
  });
});

describe("DataTable", () => {
  const rows = [
    { id: "1039", customer: "Ridgeline Benefits", leads: 100_000 },
    { id: "1042", customer: "Northstar Insurance", leads: 25_000 },
  ];

  it("marks the selected row, right-aligns numbers, and opens rows by click", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    render(
      <DataTable
        label="Batches"
        columns={[
          { key: "batch", header: "Batch", cell: (row) => <a href={`#${row.id}`}>#{row.id}</a> },
          { key: "customer", header: "Customer", cell: (row) => row.customer },
          { key: "leads", header: "Leads", numeric: true, cell: (row) => row.leads.toLocaleString("en-US") },
        ]}
        rows={rows}
        rowKey={(row) => row.id}
        selectedKey="1042"
        onRowClick={onRowClick}
      />,
    );

    const table = screen.getByRole("table", { name: "Batches" });
    const [, first, second] = within(table).getAllByRole("row");
    expect(second).toHaveAttribute("aria-current", "true");
    expect(first).not.toHaveAttribute("aria-current");
    expect(within(table).getByText("25,000")).toHaveClass("jx-table__numeric");

    await user.click(within(first!).getByText("Ridgeline Benefits"));
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
    // The record link keeps its own behaviour.
    await user.click(within(first!).getByRole("link", { name: "#1039" }));
    expect(onRowClick).toHaveBeenCalledTimes(1);
  });
});
