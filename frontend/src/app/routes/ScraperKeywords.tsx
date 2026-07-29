import { useRef, useState } from "react";
import { useLoaderData, useNavigate } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input, Textarea } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import {
  Text,
  VisuallyHidden,
} from "../../design-system/primitives/typography";
import { useRouteTheme } from "../../design-system/theme/ThemeProvider";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  KeywordRequestError,
  fetchKeywordWorkspace,
  generateKeywords,
  previewKeywords,
  saveKeywords,
  setKeywordRollover,
} from "./scraperKeywordApi";
import type {
  KeywordDiff,
  KeywordRollover,
  KeywordWorkspace,
} from "./scraperKeywordApi";

import "./ScraperKeywords.css";

const MAX_IMPORT_BYTES = 1_000_000;

const RAIL = [
  {
    label: "Status",
    href: "/app/admin/acquisition/scraper/workspace",
  },
  { label: "Keyword editor", href: "#keyword-editor", current: true },
  { label: "Automatic rollover", href: "#keyword-rollover" },
  { label: "Winner rankings", href: "#keyword-winners" },
  {
    label: "Database",
    href: "/app/admin/acquisition/scraper/workspace/database",
  },
  {
    label: "Campaign history",
    href: "/app/admin/acquisition/scraper/workspace/history",
  },
  {
    label: "Runtime configuration",
    href: "/app/admin/acquisition/scraper/workspace/runtime",
  },
  { label: "Exit to Acquisition", href: "/app/admin/acquisition" },
];

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The keyword request could not be completed.";
}

function integer(value: number): string {
  return value.toLocaleString();
}

function rate(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function DiffPreview({ diff }: { diff: KeywordDiff }) {
  return (
    <section className="keyword-diff" aria-label="Keyword change preview">
      <div className="keyword-diff__summary">
        <div>
          <strong>{diff.added.length}</strong>
          <span>Added</span>
        </div>
        <div>
          <strong>{diff.removed.length}</strong>
          <span>Removed</span>
        </div>
        <div>
          <strong>{diff.unchanged.length}</strong>
          <span>Unchanged</span>
        </div>
      </div>
      <Grid minColumnWidth="14rem" gap={3}>
        <div>
          <h3>Added</h3>
          {diff.added.length ? (
            <ul className="keyword-diff__list" aria-label="Keywords added">
              {diff.added.map((keyword) => (
                <li className="keyword-diff__item keyword-diff__item--added" key={keyword}>
                  + {keyword}
                </li>
              ))}
            </ul>
          ) : (
            <Text size="sm" tone="muted">None</Text>
          )}
        </div>
        <div>
          <h3>Removed</h3>
          {diff.removed.length ? (
            <ul className="keyword-diff__list" aria-label="Keywords removed">
              {diff.removed.map((keyword) => (
                <li className="keyword-diff__item keyword-diff__item--removed" key={keyword}>
                  − {keyword}
                </li>
              ))}
            </ul>
          ) : (
            <Text size="sm" tone="muted">None</Text>
          )}
        </div>
      </Grid>
    </section>
  );
}

function RolloverPanel({
  rollover,
  aiEnabled,
  busy,
  onChange,
}: {
  rollover: KeywordRollover;
  aiEnabled: boolean;
  busy: boolean;
  onChange: () => void;
}) {
  return (
    <Card as="section" id="keyword-rollover" aria-label="Automatic keyword rollover">
      <Stack gap={4}>
        <div className="keyword-rollover__heading">
          <div>
            <Text size="sm" tone="muted">Automatic batches</Text>
            <Text weight="bold">{rollover.label}</Text>
            <Text size="sm">{rollover.detail}</Text>
          </div>
          <span className={`keyword-rollover__state keyword-rollover__state--${rollover.state}`}>
            {rollover.enabled ? "Enabled" : "Disabled"}
          </span>
        </div>
        <div>
          <div className="keyword-rollover__meter-label">
            <span>Current coverage</span>
            <strong>{rollover.percent_complete}%</strong>
          </div>
          <progress
            max={100}
            value={rollover.percent_complete}
            aria-label="Current keyword coverage"
          >
            {rollover.percent_complete}%
          </progress>
        </div>
        <Grid minColumnWidth="11rem" gap={3}>
          <div>
            <Text size="sm" tone="muted">Coverage jobs</Text>
            <Text weight="bold">
              {rollover.posted_jobs === null || rollover.expected_jobs === null
                ? "Complete"
                : `${integer(rollover.posted_jobs)} / ${integer(rollover.expected_jobs)}`}
            </Text>
          </div>
          <div>
            <Text size="sm" tone="muted">Last rollover</Text>
            <Text weight="bold">
              {rollover.last_status
                ? `${rollover.last_status.charAt(0).toUpperCase()}${rollover.last_status.slice(1)}`
                : "None yet"}
            </Text>
            <Text size="sm">{rollover.last_event ?? "Awaiting current batch"}</Text>
          </div>
        </Grid>
        <div>
          <Button
            variant={rollover.enabled ? "secondary" : "primary"}
            busy={busy}
            busyLabel="Updating automatic batches…"
            disabled={!rollover.enabled && !aiEnabled}
            onClick={onChange}
          >
            {rollover.enabled ? "Disable automatic rollover" : "Enable automatic rollover"}
          </Button>
        </div>
      </Stack>
    </Card>
  );
}

export function ScraperKeywordsRoute() {
  const initial = useLoaderData<KeywordWorkspace>();
  const navigate = useNavigate();
  const [workspace, setWorkspace] = useState(initial);
  const [text, setText] = useState(initial.current.join("\n"));
  const [preview, setPreview] = useState<KeywordDiff | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [generationId, setGenerationId] = useState<string | null>(null);
  const [generationNotice, setGenerationNotice] = useState("");
  const [enqueue, setEnqueue] = useState(false);
  const [busy, setBusy] = useState<
    "preview" | "save" | "generate" | "rollover" | "reload" | null
  >(null);
  const [failure, setFailure] = useState("");
  const [outcome, setOutcome] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  useRouteTheme("terminal", "jawnix");
  useDocumentTitle("Scraper Keywords");

  const previewIsCurrent = preview !== null && previewText === text;

  async function preparePreview() {
    setBusy("preview");
    setFailure("");
    setOutcome("");
    try {
      const next = await previewKeywords(text);
      setPreview(next);
      setPreviewText(text);
      setOutcome("Preview ready. Review the exact additions and removals before saving.");
    } catch (error) {
      if (error instanceof KeywordRequestError && error.status === 401) {
        await navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setPreview(null);
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!previewIsCurrent || !preview) {
      setFailure("Preview the current draft before saving.");
      return;
    }
    setBusy("save");
    setFailure("");
    setOutcome("");
    try {
      const result = await saveKeywords({
        text,
        expected_version: preview.expected_version,
        review_token: preview.review_token,
        enqueue,
        ...(generationId ? { generation_id: generationId } : {}),
      });
      setWorkspace((current) => ({
        ...current,
        current: result.current,
        version: result.version,
      }));
      setText(result.current.join("\n"));
      setPreview(null);
      setPreviewText("");
      setGenerationId(null);
      setGenerationNotice("");
      setOutcome(
        `Saved ${result.current.length.toLocaleString()} keywords${
          result.enqueued ? "; enqueue requested." : "."
        }`,
      );
    } catch (error) {
      if (error instanceof KeywordRequestError && error.status === 401) {
        await navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function generate(mode: "broad" | "adjacent", seed?: string) {
    setBusy("generate");
    setFailure("");
    setOutcome("");
    try {
      const draft = await generateKeywords(mode, seed);
      setText(draft.keywords.join("\n"));
      setGenerationId(draft.generation_id);
      setGenerationNotice(
        `${draft.keywords.length} ${
          mode === "adjacent" ? `keywords adjacent to ${seed}` : "broad local-business keywords"
        } are ready for review. ${draft.excluded_count} candidates were filtered. Nothing has been saved or enqueued.`,
      );
      setPreview(null);
      setPreviewText("");
      document.getElementById("keyword-editor")?.scrollIntoView?.();
    } catch (error) {
      if (error instanceof KeywordRequestError && error.status === 401) {
        await navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function updateRollover() {
    setBusy("rollover");
    setFailure("");
    setOutcome("");
    try {
      const rollover = await setKeywordRollover(
        workspace.rollover.enabled ? "disable" : "enable",
      );
      setWorkspace((current) => ({ ...current, rollover }));
      setOutcome(
        `Automatic keyword rollover ${rollover.enabled ? "enabled" : "disabled"}.`,
      );
    } catch (error) {
      if (error instanceof KeywordRequestError && error.status === 401) {
        await navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function reloadCurrent() {
    setBusy("reload");
    setFailure("");
    try {
      const current = await fetchKeywordWorkspace();
      setWorkspace(current);
      setPreview(null);
      setPreviewText("");
      setOutcome(
        "Current active keywords reloaded. Your draft is still in the editor; preview it again.",
      );
    } catch (error) {
      if (error instanceof KeywordRequestError && error.status === 401) {
        await navigate("/admin/acquisition/scraper", { replace: true });
        return;
      }
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function importFile(file: File | undefined) {
    if (!file) return;
    setFailure("");
    setOutcome("");
    if (
      file.size > MAX_IMPORT_BYTES ||
      (!file.name.toLowerCase().endsWith(".txt") && file.type !== "text/plain")
    ) {
      setFailure("Choose a plain-text .txt file no larger than 1 MB.");
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
    try {
      const imported = await file.text();
      setText(imported);
      setGenerationId(null);
      setGenerationNotice("");
      setPreview(null);
      setPreviewText("");
      setOutcome(
        `Imported ${file.name} into the editor. Preview the intended changes before saving.`,
      );
    } catch {
      setFailure("That text file could not be read. Choose it again.");
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <Page
      title="Scraper Keywords"
      description="Edit campaign inputs, review generated drafts, compare winners, and control automatic rollover."
    >
      <TerminalWorkspace
        status="KEYWORDS / PRIVILEGED"
        destinations={RAIL}
      >
        <Stack gap={5}>
          {failure ? (
            <ErrorState
              title="Keyword action not completed"
              description={failure}
              retryLabel="Reload current keywords"
              onRetry={() => void reloadCurrent()}
            />
          ) : null}
          {outcome ? (
            <p className="keyword-notice keyword-notice--ok" role="status">
              {outcome}
            </p>
          ) : null}
          {!workspace.ai_enabled ? (
            <p className="keyword-notice" role="status">
              AI generation is unavailable until an OpenRouter key is configured.
              Direct editing and text-file import remain available.
            </p>
          ) : null}
          {generationNotice ? (
            <p className="keyword-notice keyword-notice--draft" role="status">
              <strong>Draft ready:</strong> {generationNotice}
            </p>
          ) : null}

          <Section
            title="Keyword editor"
            description={`${workspace.current.length.toLocaleString()} keywords are active. Blank lines, comments beginning with #, and case-insensitive duplicates are removed.`}
          >
            <div id="keyword-editor" className="keyword-editor">
              <Stack gap={4}>
                <Field
                  id="keyword-list"
                  label="Keyword list"
                  description="One keyword per line. Changes here are a draft until previewed and saved."
                  required
                >
                  <Textarea
                    rows={18}
                    spellCheck={false}
                    value={text}
                    onChange={(event) => {
                      setText(event.currentTarget.value);
                      setFailure("");
                      if (previewText !== event.currentTarget.value) {
                        setOutcome("");
                      }
                    }}
                  />
                </Field>
                <Grid minColumnWidth="15rem" gap={3}>
                  <Field
                    id="keyword-import"
                    label="Import a text file"
                    description=".txt or text/plain, up to 1 MB. Import replaces the editor draft, not the active list."
                  >
                    <Input
                      ref={fileInput}
                      type="file"
                      accept=".txt,text/plain"
                      onChange={(event) =>
                        void importFile(event.currentTarget.files?.[0])
                      }
                    />
                  </Field>
                  <label className="keyword-enqueue">
                    <input
                      type="checkbox"
                      checked={enqueue}
                      onChange={(event) => setEnqueue(event.currentTarget.checked)}
                    />
                    <span>
                      <strong>Request enqueue after save</strong>
                      <small>The Scraper keeps its current queue and deduplication rules.</small>
                    </span>
                  </label>
                </Grid>
                <Cluster gap={2}>
                  <Button
                    busy={busy === "generate"}
                    busyLabel="Generating 25 unused keywords…"
                    disabled={!workspace.ai_enabled || busy !== null}
                    onClick={() => void generate("broad")}
                  >
                    Generate 25 broad keywords
                  </Button>
                  <Button
                    busy={busy === "preview"}
                    busyLabel="Preparing preview…"
                    disabled={busy !== null}
                    onClick={() => void preparePreview()}
                  >
                    Preview changes
                  </Button>
                  <Button
                    variant="primary"
                    busy={busy === "save"}
                    busyLabel="Saving reviewed list…"
                    disabled={!previewIsCurrent || busy !== null}
                    onClick={() => void save()}
                  >
                    Save reviewed list
                  </Button>
                </Cluster>
              </Stack>
              <Card as="aside" aria-label="Keyword preview">
                {previewIsCurrent && preview ? (
                  <DiffPreview diff={preview} />
                ) : (
                  <div className="keyword-preview__empty">
                    <Text weight="bold">No reviewed changes</Text>
                    <Text size="sm">
                      Preview the editor draft to see its exact additions and removals.
                    </Text>
                  </div>
                )}
              </Card>
            </div>
          </Section>

          <Section
            title="Automatic rollover"
            description="The Scraper advances only after the current batch reaches its existing completion conditions."
          >
            <RolloverPanel
              rollover={workspace.rollover}
              aiEnabled={workspace.ai_enabled}
              busy={busy === "rollover"}
              onChange={() => void updateRollover()}
            />
          </Section>

          <Section
            title="Winner rankings"
            description="Keywords with at least 100 posted cells, ranked by phones per cell. Adjacent generation creates a review draft only."
          >
            <div id="keyword-winners">
              {workspace.winners.length ? (
                <div className="keyword-winners">
                  <table>
                    <thead>
                      <tr>
                        <th>Rank</th>
                        <th>Keyword</th>
                        <th>Phone leads</th>
                        <th>Businesses</th>
                        <th>Posted cells</th>
                        <th>Phones / cell</th>
                        <th>Phone rate</th>
                        <th><VisuallyHidden>Actions</VisuallyHidden></th>
                      </tr>
                    </thead>
                    <tbody>
                      {workspace.winners.map((winner) => (
                        <tr key={winner.keyword}>
                          <td data-label="Rank">{winner.rank}</td>
                          <td data-label="Keyword">
                            <strong>{winner.keyword}</strong>
                            <small>Last used {winner.last_used}</small>
                          </td>
                          <td data-label="Phone leads">{integer(winner.phone_businesses)}</td>
                          <td data-label="Businesses">{integer(winner.businesses)}</td>
                          <td data-label="Posted cells">{integer(winner.posted_cells)}</td>
                          <td data-label="Phones / cell">
                            <strong>{winner.phones_per_cell.toFixed(2)}</strong>
                          </td>
                          <td data-label="Phone rate">{rate(winner.phone_rate)}</td>
                          <td data-label="Action">
                            <Button
                              busy={busy === "generate"}
                              busyLabel="Generating adjacent keywords…"
                              disabled={!workspace.ai_enabled || busy !== null}
                              onClick={() =>
                                void generate("adjacent", winner.keyword)
                              }
                            >
                              Generate adjacent
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Text>No keyword has at least 100 posted cells yet.</Text>
              )}
            </div>
          </Section>
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}
