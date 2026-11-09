import { useId, useMemo, useState } from "react";
import type { CSSProperties, KeyboardEvent } from "react";

import { Field, Input } from "../../design-system/primitives/form";
import { cx } from "../../design-system/primitives/cx";

import "./AppointedStatesPicker.css";

export interface StateOption {
  code: string;
  name: string;
}

/** District of Columbia sits apart from the 50-state grid (V10). */
export const DISTRICT_OF_COLUMBIA = "DC";

/** Selected names in the summary before it says "and N more". */
const NAMES_IN_SUMMARY = 6;

function byName(left: StateOption, right: StateOption): number {
  return left.name.localeCompare(right.name, "en-US");
}

/** Full state names for codes, in the options' alphabetical order. */
export function stateNames(options: StateOption[], codes: string[]): string[] {
  const chosen = new Set(codes);
  return [...options]
    .sort(byName)
    .filter((option) => chosen.has(option.code))
    .map((option) => option.name);
}

/** "2 selected · Florida, Texas", or "None selected". */
export function selectionSummary(options: StateOption[], codes: string[]): string {
  const names = stateNames(options, codes);
  if (!names.length) return "None selected";
  const shown = names.slice(0, NAMES_IN_SUMMARY).join(", ");
  const more = names.length - NAMES_IN_SUMMARY;
  return `${names.length} selected · ${shown}${more > 0 ? ` and ${more} more` : ""}`;
}

/** Does the search match this state? Any part of its name, or its exact code. */
export function matchesState(option: StateOption, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase("en-US");
  if (!needle) return true;
  return (
    option.name.toLocaleLowerCase("en-US").includes(needle)
    || option.code.toLocaleLowerCase("en-US") === needle
  );
}

export interface AppointedStatesPickerProps {
  /** Every state the backend accepts, District of Columbia included. */
  options: StateOption[];
  /** Selected codes. */
  selected: string[];
  onChange: (codes: string[]) => void;
  disabled?: boolean;
  /** Account puts the selection summary beside a short search box; sign-up
   *  shows it beside its submit button instead. */
  summary?: "beside-search" | "none";
}

/**
 * The one appointed-states picker (V10), shared by Account and sign-up.
 *
 * The 50 states fill a 5 x 10 grid down each column (2 x 25 on phones), so
 * every row and column is full; District of Columbia is its own checkbox
 * below. Search narrows the grid by name or code without changing what is
 * selected, and Enter picks the one state left.
 */
export function AppointedStatesPicker({
  options,
  selected,
  onChange,
  disabled = false,
  summary = "beside-search",
}: AppointedStatesPickerProps) {
  const [query, setQuery] = useState("");
  const summaryId = useId();

  const states = useMemo(
    () => options.filter((option) => option.code !== DISTRICT_OF_COLUMBIA).sort(byName),
    [options],
  );
  const district = options.find((option) => option.code === DISTRICT_OF_COLUMBIA);
  const visible = useMemo(
    () => states.filter((option) => matchesState(option, query)),
    [states, query],
  );
  const chosen = new Set(selected);

  function toggle(code: string) {
    const next = new Set(selected);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    onChange([...next].sort());
  }

  function handleSearchKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    // Never submit the surrounding form from the search box.
    event.preventDefault();
    const [only, ...rest] = visible;
    if (only && !rest.length) {
      toggle(only.code);
      setQuery("");
    }
  }

  const grid = {
    "--appointed-rows": Math.max(1, Math.ceil(visible.length / 5)),
    "--appointed-rows-phone": Math.max(1, Math.ceil(visible.length / 2)),
  } as CSSProperties;

  return (
    <div className={cx("appointed-states", summary === "beside-search" && "appointed-states--summary")}>
      <div className="appointed-states__find">
        <Field label="Find a state">
          <Input
            type="search"
            value={query}
            placeholder="Texas or TX"
            autoComplete="off"
            disabled={disabled}
            onChange={(event) => setQuery(event.currentTarget.value)}
            onKeyDown={handleSearchKey}
          />
        </Field>
        {summary === "beside-search" ? (
          <p className="appointed-states__summary" id={summaryId} aria-live="polite">
            {selectionSummary(options, selected)}
          </p>
        ) : null}
      </div>

      <fieldset
        className="appointed-states__set"
        disabled={disabled}
        {...(summary === "beside-search" ? { "aria-describedby": summaryId } : {})}
      >
        <legend className="jx-visually-hidden">
          Appointed states
        </legend>
        {visible.length ? (
          <div className="appointed-states__grid" style={grid}>
            {visible.map((option) => (
              <label
                key={option.code}
                className={cx(
                  "appointed-states__state",
                  chosen.has(option.code) && "appointed-states__state--on",
                )}
              >
                <input
                  type="checkbox"
                  name="appointed_states"
                  value={option.code}
                  checked={chosen.has(option.code)}
                  onChange={() => toggle(option.code)}
                />
                <span>{option.name}</span>
              </label>
            ))}
          </div>
        ) : (
          <p className="appointed-states__none" role="status">
            No state matches “{query.trim()}”.
          </p>
        )}
        {district ? (
          <label className="appointed-states__district">
            <input
              type="checkbox"
              name="appointed_states"
              value={district.code}
              checked={chosen.has(district.code)}
              onChange={() => toggle(district.code)}
            />
            <span>Also appointed in {district.name}</span>
          </label>
        ) : null}
      </fieldset>
    </div>
  );
}
