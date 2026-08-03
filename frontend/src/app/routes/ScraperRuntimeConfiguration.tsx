import { useMemo, useState } from "react";
import { useLoaderData, useNavigate } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ErrorState } from "../../design-system/primitives/feedback";
import { Field, Input } from "../../design-system/primitives/form";
import {
  Cluster,
  Grid,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { TerminalWorkspace } from "../../design-system/primitives/terminal";
import { useDocumentTitle } from "../shell/useDocumentTitle";

import {
  ScraperRuntimeRequestError,
  fetchRuntimeWorkspace,
  previewRuntimeConfiguration,
  saveRuntimeConfiguration,
} from "./scraperRuntimeApi";
import type {
  FieldBounds,
  QueueSettings,
  RuntimeBounds,
  RuntimeConfiguration,
  RuntimePreview,
  RuntimeSettings,
  RuntimeWorkspace,
  StateOverride,
} from "./scraperRuntimeApi";

import { WORKSPACE_ROOT, workspaceRail } from "./scraperWorkspaceNav";
import "./ScraperRuntimeConfiguration.css";


const RUNTIME_FIELDS: Array<{
  key: Exclude<keyof RuntimeSettings, "lang" | "fast_mode">;
  label: string;
  description: string;
}> = [
  { key: "zoom", label: "Zoom", description: "Map result zoom level." },
  { key: "radius", label: "Radius (m)", description: "Search radius in metres." },
  { key: "depth", label: "Depth", description: "Scrape traversal depth." },
  { key: "timeout", label: "Timeout (sec)", description: "Per-job timeout." },
];

const QUEUE_FIELDS: Array<{
  key: keyof QueueSettings;
  label: string;
}> = [
  { key: "target_depth", label: "Fallback depth" },
  { key: "target_per_worker", label: "Jobs per worker" },
  { key: "min_target_depth", label: "Minimum depth" },
  { key: "max_target_depth", label: "Maximum depth" },
  { key: "batch_size", label: "Batch size" },
  { key: "poll_secs", label: "Poll interval (sec)" },
  { key: "skip_recent_days", label: "Skip recent days" },
];

type Errors = Record<string, string>;

function clone(configuration: RuntimeConfiguration): RuntimeConfiguration {
  return structuredClone(configuration);
}

function label(name: string): string {
  return name
    .split("_")
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "The runtime configuration request could not be completed.";
}

function lastSuccess(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "never";
}

function rangeError(
  value: number,
  bounds: FieldBounds,
  fieldLabel: string,
): string {
  if (!Number.isFinite(value)) return `${fieldLabel} must be a number.`;
  if (value < bounds.minimum || value > bounds.maximum) {
    return `${fieldLabel} must be between ${bounds.minimum.toLocaleString()} and ${bounds.maximum.toLocaleString()}.`;
  }
  return "";
}

function validate(
  configuration: RuntimeConfiguration,
  bounds: RuntimeBounds,
): Errors {
  const errors: Errors = {};
  for (const { key, label: fieldLabel } of RUNTIME_FIELDS) {
    const error = rangeError(
      configuration.settings[key],
      bounds.runtime[key],
      fieldLabel,
    );
    if (error) errors[`runtime.${key}`] = error;
  }
  if (
    !configuration.settings.lang.trim() ||
    configuration.settings.lang.length > bounds.language_max_length
  ) {
    errors["runtime.lang"] =
      `Language is required and may contain at most ${bounds.language_max_length} characters.`;
  }
  for (const { key, label: fieldLabel } of QUEUE_FIELDS) {
    const error = rangeError(
      configuration.queue[key],
      bounds.queue[key],
      fieldLabel,
    );
    if (error) errors[`queue.${key}`] = error;
  }
  if (
    configuration.queue.min_target_depth >
    configuration.queue.max_target_depth
  ) {
    errors["queue.min_target_depth"] =
      "Minimum depth cannot exceed maximum depth.";
    errors["queue.max_target_depth"] =
      "Maximum depth must be at least the minimum depth.";
  }
  for (const [state, override] of Object.entries(configuration.overrides)) {
    if (override.cell_size_km !== undefined) {
      const error = rangeError(
        override.cell_size_km,
        bounds.override.cell_size_km,
        `${state} cell size`,
      );
      if (error) errors[`override.${state}.cell_size_km`] = error;
    }
    if (override.zoom !== undefined) {
      const error = rangeError(
        override.zoom,
        bounds.override.zoom,
        `${state} zoom`,
      );
      if (error) errors[`override.${state}.zoom`] = error;
    }
  }
  return errors;
}

function NumericField({
  id,
  label: fieldLabel,
  description,
  value,
  bounds,
  error,
  onChange,
}: {
  id: string;
  label: string;
  description?: string;
  value: number;
  bounds: FieldBounds;
  error?: string;
  onChange: (value: number) => void;
}) {
  return (
    <Field
      id={id}
      label={fieldLabel}
      {...(description ? { description } : {})}
      {...(error ? { error } : {})}
    >
      <Input
        type="number"
        min={bounds.minimum}
        max={bounds.maximum}
        step={bounds.step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
    </Field>
  );
}

function PreviewPanel({ preview }: { preview: RuntimePreview }) {
  const effects = preview.effects;
  const delta = effects.total_cell_delta;
  const changes = [
    ...effects.runtime_changes.map((name) => `Runtime: ${label(name)}`),
    ...effects.queue_changes.map((name) => `Queue: ${label(name)}`),
    ...effects.override_changes.map((state) => `Override: ${state}`),
  ];
  return (
    <section className="runtime-preview" aria-label="Runtime change preview">
      <div className="runtime-preview__summary">
        <div>
          <span>Current cells</span>
          <strong>{effects.current_total_cells.toLocaleString()}</strong>
        </div>
        <div>
          <span>Proposed cells</span>
          <strong>{effects.proposed_total_cells.toLocaleString()}</strong>
        </div>
        <div>
          <span>Calculated effect</span>
          <strong>
            {delta > 0 ? "+" : ""}
            {delta.toLocaleString()} cells
          </strong>
        </div>
      </div>
      <Grid minColumnWidth="12rem" gap={3}>
        <div>
          <h3>State effects</h3>
          <ul className="runtime-preview__list">
            {effects.cells.map((row) => (
              <li key={row.state}>
                <span>{row.state}</span>
                <strong>{row.cells.toLocaleString()} cells</strong>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Coverage changes</h3>
          <p>
            Added: {effects.states_added.join(", ") || "none"}
            <br />
            Removed: {effects.states_removed.join(", ") || "none"}
          </p>
        </div>
        <div>
          <h3>Setting changes</h3>
          <p>{changes.join(" · ") || "No setting values changed."}</p>
        </div>
      </Grid>
      <p className="runtime-preview__valid" role="status">
        Validation passed. This exact proposal may now be saved during the
        active privileged session.
      </p>
    </section>
  );
}

export function ScraperRuntimeConfigurationRoute() {
  const initial = useLoaderData<RuntimeWorkspace>();
  const navigate = useNavigate();
  const [workspace, setWorkspace] = useState(initial);
  const [draft, setDraft] = useState(() => clone(initial.current));
  const [preview, setPreview] = useState<RuntimePreview | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [reason, setReason] = useState("");
  const [enqueue, setEnqueue] = useState(false);
  const [busy, setBusy] = useState<"preview" | "save" | "reload" | null>(
    null,
  );
  const [failure, setFailure] = useState("");
  const [outcome, setOutcome] = useState("");

  useDocumentTitle("Scraper Runtime Configuration");

  const reviewed =
    preview !== null &&
    JSON.stringify(preview.configuration) === JSON.stringify(draft);
  const changed =
    JSON.stringify(workspace.current) !== JSON.stringify(draft);
  const activeStates = useMemo(
    () => workspace.all_states.filter((state) => draft.states.includes(state)),
    [workspace.all_states, draft.states],
  );

  if (workspace.service_state === "unavailable") {
    return (
      <Page
        title="Scraper Runtime Configuration"
        description="Tune Scale coverage, workers, and queue behavior with calculated review before activation."
      >
        <TerminalWorkspace
          status="OFFLINE / RUNTIME UNAVAILABLE"
          tone="offline"
          destinations={workspaceRail(`${WORKSPACE_ROOT}/runtime`)}
        >
          <ErrorState
            title="Runtime configuration unavailable"
            description={`The private service did not return its current runtime configuration, so review and save controls are unavailable. Last successful connection: ${lastSuccess(workspace.last_successful_at)}.`}
            onRetry={() => window.location.reload()}
          />
        </TerminalWorkspace>
      </Page>
    );
  }

  function edit(mutator: (next: RuntimeConfiguration) => void) {
    setDraft((current) => {
      const next = clone(current);
      mutator(next);
      return next;
    });
    setPreview(null);
    setErrors({});
    setFailure("");
    setOutcome("");
  }

  function handleExpired(error: unknown): boolean {
    if (
      error instanceof ScraperRuntimeRequestError &&
      error.status === 401
    ) {
      void navigate("/admin/acquisition/scraper");
      return true;
    }
    return false;
  }

  async function reloadCurrent(preserveDraft: boolean) {
    setBusy("reload");
    setFailure("");
    try {
      const next = await fetchRuntimeWorkspace();
      setWorkspace(next);
      if (!preserveDraft) setDraft(clone(next.current));
      setPreview(null);
      setOutcome(
        preserveDraft
          ? "Current Scale settings reloaded. Your draft is preserved; preview it again."
          : "Current Scale settings reloaded.",
      );
    } catch (error) {
      if (!handleExpired(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function runPreview() {
    const found = validate(draft, workspace.bounds);
    setErrors(found);
    setFailure("");
    setOutcome("");
    if (Object.keys(found).length) {
      setFailure("Correct the highlighted values before previewing.");
      return;
    }
    setBusy("preview");
    try {
      const next = await previewRuntimeConfiguration(draft);
      setDraft(clone(next.configuration));
      setPreview(next);
    } catch (error) {
      if (!handleExpired(error)) setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!preview || !reviewed || !reason.trim()) return;
    setBusy("save");
    setFailure("");
    setOutcome("");
    try {
      const result = await saveRuntimeConfiguration({
        configuration: draft,
        expected_version: preview.expected_version,
        review_token: preview.review_token,
        enqueue,
        reason: reason.trim(),
      });
      setWorkspace((current) => ({
        ...current,
        current: result.configuration,
        version: result.version,
        cells: result.effects.cells,
        total_cells: result.effects.proposed_total_cells,
      }));
      setDraft(clone(result.configuration));
      setPreview(null);
      setReason("");
      setOutcome(
        result.enqueued
          ? "Scale runtime configuration saved and enqueue requested."
          : "Scale runtime configuration saved.",
      );
    } catch (error) {
      if (handleExpired(error)) return;
      if (
        error instanceof ScraperRuntimeRequestError &&
        error.status === 409
      ) {
        setFailure(error.message);
        await reloadCurrent(true);
        return;
      }
      setFailure(message(error));
    } finally {
      setBusy(null);
    }
  }

  function toggleState(state: string, checked: boolean) {
    edit((next) => {
      next.states = checked
        ? [...next.states, state].sort()
        : next.states.filter((item) => item !== state);
      if (!checked) delete next.overrides[state];
    });
  }

  function setOverride(
    state: string,
    key: keyof StateOverride,
    raw: string,
  ) {
    edit((next) => {
      const override = { ...(next.overrides[state] ?? {}) };
      if (raw === "") delete override[key];
      else override[key] = Number(raw);
      if (Object.keys(override).length) next.overrides[state] = override;
      else delete next.overrides[state];
    });
  }

  return (
    <Page
      title="Scraper Runtime Configuration"
      description="Tune Scale coverage, workers, and queue behavior with calculated review before activation."
      actions={
        <Button
          onClick={() => void reloadCurrent(false)}
          busy={busy === "reload"}
          busyLabel="Reloading…"
        >
          Reload current
        </Button>
      }
    >
      <TerminalWorkspace
        status={busy ? "RUNTIME / WORKING" : "RUNTIME / PRIVILEGED"}
        tone={failure ? "warning" : "online"}
        destinations={workspaceRail(`${WORKSPACE_ROOT}/runtime`)}
      >
        <Stack gap={5}>
          <div className="runtime-boundary" role="note">
            <strong>Scale runtime controls only.</strong>
            <span>
              Saving here does not edit, publish, activate, or roll back a
              Jawnix Scraper Configuration version.
            </span>
          </div>

          {failure ? (
            <ErrorState
              title="Runtime action not completed"
              description={failure}
              retryLabel="Reload current settings"
              onRetry={() => void reloadCurrent(true)}
            />
          ) : null}
          {outcome ? (
            <p className="runtime-notice runtime-notice--ok" role="status">
              {outcome}
            </p>
          ) : null}

          <Section
            id="runtime-states"
            title="Active states"
            description={`${draft.states.length} of ${workspace.all_states.length} states are active. Changes alter the next Scale enqueue, not a published Jawnix version.`}
          >
            <fieldset className="runtime-state-picker">
              <legend className="runtime-sr-only">Select active states</legend>
              {workspace.all_states.map((state) => (
                <label key={state}>
                  <input
                    type="checkbox"
                    checked={draft.states.includes(state)}
                    onChange={(event) =>
                      toggleState(state, event.currentTarget.checked)
                    }
                  />
                  <span>{state}</span>
                </label>
              ))}
            </fieldset>
          </Section>

          <Section
            id="runtime-settings"
            title="Runtime settings"
            description="Current Scale scrape-process controls, enforced at the existing bounds."
          >
            <Grid minColumnWidth="13rem" gap={3}>
              {RUNTIME_FIELDS.map((field) => (
                <NumericField
                  key={field.key}
                  id={`runtime-${field.key}`}
                  label={field.label}
                  description={field.description}
                  value={draft.settings[field.key]}
                  bounds={workspace.bounds.runtime[field.key]}
                  {...(errors[`runtime.${field.key}`]
                    ? { error: errors[`runtime.${field.key}`] }
                    : {})}
                  onChange={(value) =>
                    edit((next) => {
                      next.settings[field.key] = value;
                    })
                  }
                />
              ))}
              <Field
                id="runtime-lang"
                label="Language"
                description="At most 10 characters."
                {...(errors["runtime.lang"]
                  ? { error: errors["runtime.lang"] }
                  : {})}
              >
                <Input
                  maxLength={workspace.bounds.language_max_length}
                  value={draft.settings.lang}
                  onChange={(event) =>
                    edit((next) => {
                      next.settings.lang = event.currentTarget.value;
                    })
                  }
                />
              </Field>
              <label className="runtime-toggle">
                <span>
                  <strong>Fast mode</strong>
                  <small>Use Scale’s faster scrape execution path.</small>
                </span>
                <input
                  type="checkbox"
                  checked={draft.settings.fast_mode}
                  onChange={(event) =>
                    edit((next) => {
                      next.settings.fast_mode =
                        event.currentTarget.checked;
                    })
                  }
                />
              </label>
            </Grid>
          </Section>

          <Section
            id="runtime-queue"
            title="Queue settings"
            description="River refill, batching, polling, and campaign deduplication controls."
          >
            <Grid minColumnWidth="13rem" gap={3}>
              {QUEUE_FIELDS.map((field) => (
                <NumericField
                  key={field.key}
                  id={`queue-${field.key}`}
                  label={field.label}
                  value={draft.queue[field.key]}
                  bounds={workspace.bounds.queue[field.key]}
                  {...(errors[`queue.${field.key}`]
                    ? { error: errors[`queue.${field.key}`] }
                    : {})}
                  onChange={(value) =>
                    edit((next) => {
                      next.queue[field.key] = value;
                    })
                  }
                />
              ))}
            </Grid>
          </Section>

          <Section
            id="runtime-overrides"
            title="Per-state overrides"
            description="Leave a value blank to use Scale’s default for that active state."
          >
            {activeStates.length ? (
              <div className="runtime-overrides">
                {activeStates.map((state) => {
                  const override = draft.overrides[state] ?? {};
                  return (
                    <fieldset key={state} className="runtime-override">
                      <legend>{state}</legend>
                      <Field
                        id={`override-cell-${state}`}
                        label="Cell size (km)"
                        {...(errors[`override.${state}.cell_size_km`]
                          ? {
                              error:
                                errors[
                                  `override.${state}.cell_size_km`
                                ],
                            }
                          : {})}
                      >
                        <Input
                          type="number"
                          min={
                            workspace.bounds.override.cell_size_km.minimum
                          }
                          max={
                            workspace.bounds.override.cell_size_km.maximum
                          }
                          step={workspace.bounds.override.cell_size_km.step}
                          value={override.cell_size_km ?? ""}
                          placeholder="Default"
                          onChange={(event) =>
                            setOverride(
                              state,
                              "cell_size_km",
                              event.currentTarget.value,
                            )
                          }
                        />
                      </Field>
                      <Field
                        id={`override-zoom-${state}`}
                        label="Zoom"
                        {...(errors[`override.${state}.zoom`]
                          ? {
                              error: errors[`override.${state}.zoom`],
                            }
                          : {})}
                      >
                        <Input
                          type="number"
                          min={workspace.bounds.override.zoom.minimum}
                          max={workspace.bounds.override.zoom.maximum}
                          step={workspace.bounds.override.zoom.step}
                          value={override.zoom ?? ""}
                          placeholder="Default"
                          onChange={(event) =>
                            setOverride(
                              state,
                              "zoom",
                              event.currentTarget.value,
                            )
                          }
                        />
                      </Field>
                    </fieldset>
                  );
                })}
              </div>
            ) : (
              <p className="runtime-muted">
                Select an active state to add a state-specific override.
              </p>
            )}
          </Section>

          <Section
            id="runtime-review"
            title="Preview and save"
            description="Preview validates this draft against current Scale settings and calculates the state-cell effect. Any later edit requires another preview."
          >
            <Stack gap={4}>
              <Cluster gap={3}>
                <Button
                  variant="primary"
                  onClick={() => void runPreview()}
                  busy={busy === "preview"}
                  busyLabel="Calculating…"
                >
                  Preview calculated effects
                </Button>
                <span className="runtime-draft-state">
                  {changed ? "Unsaved draft" : "Matches current settings"}
                </span>
              </Cluster>

              {preview && reviewed ? (
                <PreviewPanel preview={preview} />
              ) : (
                <p className="runtime-muted">
                  No current preview. Saving remains unavailable until this
                  exact draft passes validation and effect calculation.
                </p>
              )}

              <Field
                id="runtime-reason"
                label="Change reason"
                description="Recorded with safe before/after summaries in Jawnix Activity."
                required
                {...(reviewed && !reason.trim()
                  ? { error: "Record why you are changing runtime configuration." }
                  : {})}
              >
                <Input
                  maxLength={2000}
                  value={reason}
                  onChange={(event) => setReason(event.currentTarget.value)}
                />
              </Field>
              <label className="runtime-enqueue">
                <input
                  type="checkbox"
                  checked={enqueue}
                  onChange={(event) =>
                    setEnqueue(event.currentTarget.checked)
                  }
                />
                <span>
                  <strong>Request enqueue after save</strong>
                  <small>
                    Uses Scale’s current optional enqueue behavior after the
                    runtime file is saved.
                  </small>
                </span>
              </label>
              <Cluster gap={3}>
                <Button
                  variant="primary"
                  disabled={!reviewed || !reason.trim()}
                  busy={busy === "save"}
                  busyLabel="Saving…"
                  onClick={() => void save()}
                >
                  Save reviewed runtime configuration
                </Button>
                {!reviewed ? (
                  <span className="runtime-draft-state">
                    Preview this exact draft to enable save.
                  </span>
                ) : null}
              </Cluster>
            </Stack>
          </Section>
        </Stack>
      </TerminalWorkspace>
    </Page>
  );
}
