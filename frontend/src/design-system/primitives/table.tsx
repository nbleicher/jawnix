import type { MouseEvent, ReactNode } from "react";

import "./table.css";
import { cx } from "./cx";

export interface DataColumn<Row> {
  key: string;
  header: string;
  cell: (row: Row) => ReactNode;
  /** Right-aligned with tabular figures (V3). */
  numeric?: boolean;
  /** The bold footer cell, for tables whose sum matters (V3). */
  footer?: ReactNode;
  /** Phones stack each row; a column can opt out of the stacked line when
   *  another cell already says the same thing. */
  hideWhenStacked?: boolean;
}

export interface DataTableProps<Row> {
  /** Names the table for assistive technology. */
  label: string;
  columns: DataColumn<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** The row whose side panel is open: it carries the dark left bar (V3). */
  selectedKey?: string | null;
  /** A pointer convenience: clicking anywhere on a row does what its first
   *  cell's link does. Keyboard users reach the same action through that
   *  link, so the row itself never needs to be focusable. */
  onRowClick?: (row: Row) => void;
  /** Rows in a table with a button column share one height (V3). */
  hasActions?: boolean;
  /** Shown in place of the body when there are no rows. */
  empty?: ReactNode;
  className?: string;
}

function interactive(event: MouseEvent<HTMLElement>): boolean {
  const target = event.target as HTMLElement | null;
  return Boolean(target?.closest("a, button, input, select, textarea, label"));
}

/**
 * The operator data table (V3).
 *
 * The first column is the record, as a link. Numbers are right-aligned with
 * tabular figures; status is a badge supplied by the cell. On phones each row
 * stacks: the record on top, the other values on one labelled line beneath
 * it, and a button column (if any) on the right (V8).
 */
export function DataTable<Row>({
  label,
  columns,
  rows,
  rowKey,
  selectedKey,
  onRowClick,
  hasActions = false,
  empty,
  className,
}: DataTableProps<Row>) {
  const hasFooter = columns.some((column) => column.footer !== undefined);
  return (
    <div className={cx("jx-table-wrap", className)}>
      <table
        className={cx("jx-table", hasActions && "jx-table--actions")}
        aria-label={label}
      >
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={column.numeric ? "jx-table__numeric" : undefined}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && empty ? (
            <tr className="jx-table__empty">
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          ) : null}
          {rows.map((row) => {
            const key = rowKey(row);
            const selected = selectedKey === key;
            return (
              <tr
                key={key}
                className={cx(
                  selected && "jx-table__row--selected",
                  onRowClick && "jx-table__row--clickable",
                )}
                aria-current={selected ? "true" : undefined}
                onClick={
                  onRowClick
                    ? (event) => {
                        if (!interactive(event)) onRowClick(row);
                      }
                    : undefined
                }
              >
                {columns.map((column, index) => (
                  <td
                    key={column.key}
                    data-label={index === 0 ? undefined : column.header}
                    className={cx(
                      column.numeric && "jx-table__numeric",
                      index === 0 && "jx-table__record",
                      column.hideWhenStacked && "jx-table__cell--unstacked",
                    )}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
        {hasFooter ? (
          <tfoot>
            <tr>
              {columns.map((column, index) => {
                const empty = column.footer === undefined || column.footer === null || column.footer === "";
                return (
                  <td
                    key={column.key}
                    // A blank footer cell has nothing to label on a stacked row.
                    data-label={index === 0 || empty ? undefined : column.header}
                    className={cx(
                      column.numeric && "jx-table__numeric",
                      empty && "jx-table__cell--blank",
                    )}
                  >
                    {column.footer ?? null}
                  </td>
                );
              })}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}
