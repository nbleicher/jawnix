import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useNavigate } from "react-router";

import { cx } from "../../design-system/primitives/cx";
import { VisuallyHidden } from "../../design-system/primitives/typography";
import { api } from "../auth/adminMFA";
import "./GlobalSearch.css";

/**
 * The operator's header search (A).
 *
 * An ARIA 1.2 combobox: focus stays in the input while the arrow keys move
 * the highlighted result (aria-activedescendant), Enter opens it, and Escape
 * closes the list, then clears the box. Results arrive grouped by record type
 * from GET /api/admin/search; a new group (phone-number Leads in A4) renders
 * here without changes.
 */

export interface SearchResult {
  kind: string;
  id: string;
  title: string;
  detail: string;
  href: string;
}

export interface SearchGroup {
  key: string;
  label: string;
  results: SearchResult[];
}

interface SearchResponse {
  query: string;
  groups: SearchGroup[];
}

const MIN_LENGTH = 2;
const DEBOUNCE_MS = 200;

function routePath(href: string): string {
  return href.startsWith("/app/") ? href.slice("/app".length) : href;
}

export function GlobalSearch() {
  const navigate = useNavigate();
  const id = useId();
  const listboxId = `${id}-results`;
  const [query, setQuery] = useState("");
  const [groups, setGroups] = useState<SearchGroup[]>([]);
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const containerRef = useRef<HTMLFormElement>(null);

  const results = groups.flatMap((group) => group.results);
  const trimmed = query.trim();

  useEffect(() => {
    if (trimmed.length < MIN_LENGTH) {
      setGroups([]);
      setStatus("idle");
      setActive(-1);
      return;
    }
    const controller = new AbortController();
    setStatus("loading");
    const timer = window.setTimeout(() => {
      api<SearchResponse>(`/api/admin/search?q=${encodeURIComponent(trimmed)}`, {
        signal: controller.signal,
      })
        .then((response) => {
          setGroups(response.groups);
          setStatus("done");
          setActive(response.groups.length ? 0 : -1);
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setGroups([]);
          setStatus("error");
          setActive(-1);
        });
    }, DEBOUNCE_MS);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [trimmed]);

  const expanded = open && trimmed.length >= MIN_LENGTH;

  function choose(result: SearchResult | undefined) {
    if (!result) return;
    setOpen(false);
    setQuery("");
    navigate(routePath(result.href));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!results.length) break;
        // Reopening starts from the first result; an open list moves on.
        if (!expanded) {
          setOpen(true);
          setActive(0);
        } else {
          setActive((current) => (current + 1) % results.length);
        }
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!results.length) break;
        if (!expanded) {
          setOpen(true);
          setActive(results.length - 1);
        } else {
          setActive((current) => (current <= 0 ? results.length - 1 : current - 1));
        }
        break;
      case "Enter":
        event.preventDefault();
        if (expanded) choose(results[active] ?? results[0]);
        break;
      case "Escape":
        // Handled here, so a side panel behind the list stays open.
        if (expanded) {
          event.preventDefault();
          setOpen(false);
        } else if (query) {
          event.preventDefault();
          setQuery("");
        }
        break;
      default:
        break;
    }
  }

  const activeId = expanded && active >= 0 ? `${id}-option-${active}` : undefined;
  const message =
    status === "loading"
      ? "Searching…"
      : status === "error"
        ? "Search is unavailable. Try again."
        : status === "done" && !results.length
          ? `No customers, batches, or uploads match “${trimmed}”.`
          : status === "done"
            ? `${results.length} result${results.length === 1 ? "" : "s"}.`
            : "";

  let index = -1;
  return (
    <form
      className="jx-global-search"
      role="search"
      ref={containerRef}
      onSubmit={(event) => event.preventDefault()}
      onBlur={(event) => {
        if (!containerRef.current?.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
        }
      }}
    >
      <input
        className="jx-global-search__input"
        type="search"
        role="combobox"
        aria-label="Search customers, batches, and uploads"
        aria-autocomplete="list"
        aria-expanded={expanded}
        aria-controls={listboxId}
        aria-activedescendant={activeId}
        aria-describedby={`${id}-status`}
        placeholder="Search customers, batches, uploads"
        autoComplete="off"
        spellCheck={false}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      <div className="jx-global-search__popup" hidden={!expanded}>
        <div role="listbox" id={listboxId} aria-label="Search results" className="jx-global-search__list">
          {groups.map((group) => (
            <div
              key={group.key}
              role="group"
              aria-labelledby={`${id}-group-${group.key}`}
              className="jx-global-search__group"
            >
              <div
                role="presentation"
                id={`${id}-group-${group.key}`}
                className="jx-global-search__group-label"
              >
                {group.label}
              </div>
              {group.results.map((result) => {
                index += 1;
                const position = index;
                return (
                  <div
                    key={`${result.kind}:${result.id}`}
                    id={`${id}-option-${position}`}
                    role="option"
                    aria-selected={position === active}
                    className={cx(
                      "jx-global-search__option",
                      position === active && "jx-global-search__option--active",
                    )}
                    // Keep focus in the input so the combobox stays the one
                    // focus stop.
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => setActive(position)}
                    onClick={() => choose(result)}
                  >
                    <span className="jx-global-search__title">{result.title}</span>
                    <span className="jx-global-search__detail">{result.detail}</span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        {!results.length && message ? (
          <p className="jx-global-search__empty">{message}</p>
        ) : null}
        <p className="jx-global-search__hint">
          {results.length ? "Enter opens the highlighted result. " : ""}
          Searches customer names and emails, batch numbers, and DB2 upload files.
        </p>
      </div>
      <VisuallyHidden id={`${id}-status`} role="status">
        {expanded ? message : ""}
      </VisuallyHidden>
    </form>
  );
}
