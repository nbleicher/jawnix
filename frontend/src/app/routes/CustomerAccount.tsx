import { useEffect, useMemo, useState } from "react";
import { useLoaderData, useRevalidator } from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { Field, Fieldset, Input } from "../../design-system/primitives/form";
import {
  Card,
  Cluster,
  Page,
  Section,
  Stack,
} from "../../design-system/primitives/layout";
import { StatusBadge } from "../../design-system/primitives/status";
import {
  Heading,
  LabelText,
  Text,
} from "../../design-system/primitives/typography";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import {
  LicensedStateRequestError,
  applyLicensedStateReview,
  previewLicensedStates,
} from "./licensedStates";
import type {
  LicensedStateImpact,
  LicensedStateReview,
  LicensedStateWorkspace,
} from "./licensedStates";

import "./CustomerAccount.css";

function formatStates(states: string[]): string {
  return states.length ? states.join(", ") : "None";
}

function sameStates(left: string[], right: string[]): boolean {
  return left.join(",") === right.join(",");
}

function Impact({ impact }: { impact: LicensedStateImpact }) {
  return (
    <Card as="li" padding={4} className="licensed-state-review__impact">
      <Stack gap={3}>
        <Cluster justify="space-between" align="flex-start">
          <Heading level={3} size="sm">
            {`${impact.lead_count.toLocaleString()}-lead Batch Request`}
          </Heading>
          <StatusBadge tone={impact.action === "canceled" ? "danger" : "warning"}>
            {impact.action === "canceled" ? "Will be canceled" : "Will be narrowed"}
          </StatusBadge>
        </Cluster>
        <Text size="sm" tone="muted">
          {impact.status}
        </Text>
        <dl className="licensed-state-review__scope">
          <div>
            <dt>
              <LabelText>Current scope</LabelText>
            </dt>
            <dd>{formatStates(impact.current_states)}</dd>
          </div>
          <div>
            <dt>
              <LabelText>After saving</LabelText>
            </dt>
            <dd>{formatStates(impact.resulting_states)}</dd>
          </div>
        </dl>
        {impact.action === "canceled" ? (
          <Text tone="danger" weight="semibold">
            Removing its final requested state will cancel this Batch Request.
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}

function ReviewContents({ review }: { review: LicensedStateReview }) {
  return (
    <Stack gap={4}>
      <dl className="licensed-state-review__summary">
        <div>
          <dt>
            <LabelText>Added</LabelText>
          </dt>
          <dd>{formatStates(review.added_states)}</dd>
        </div>
        <div>
          <dt>
            <LabelText>Removed</LabelText>
          </dt>
          <dd>{formatStates(review.removed_states)}</dd>
        </div>
      </dl>

      {review.added_states.length ? (
        <Text>
          Added states apply only to future Batch Requests. Existing requests
          will not be expanded.
        </Text>
      ) : null}

      {review.impacts.length ? (
        <Stack gap={3}>
          <Heading level={3}>
            {`${review.impacts.length} affected Batch ${
              review.impacts.length === 1 ? "Request" : "Requests"
            }`}
          </Heading>
          <Stack as="ul" gap={3} className="licensed-state-review__impacts">
            {review.impacts.map((impact) => (
              <Impact key={impact.request_id} impact={impact} />
            ))}
          </Stack>
        </Stack>
      ) : (
        <Card padding={4}>
          <Text>
            No unallocated Batch Requests will be narrowed or canceled.
          </Text>
        </Card>
      )}
    </Stack>
  );
}

export function CustomerAccountRoute() {
  const loaded = useLoaderData<LicensedStateWorkspace>();
  const revalidator = useRevalidator();
  const [account, setAccount] = useState(loaded);
  const [selected, setSelected] = useState(loaded.states);
  const [query, setQuery] = useState("");
  const [review, setReview] = useState<LicensedStateReview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [saved, setSaved] = useState("");
  useDocumentTitle("Account");

  useEffect(() => {
    if (loaded.version === account.version) return;
    setAccount(loaded);
    setSelected(loaded.states);
  }, [account.version, loaded]);

  const filteredOptions = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return account.options;
    return account.options.filter(
      (option) =>
        option.code.toLocaleLowerCase().includes(needle)
        || option.name.toLocaleLowerCase().includes(needle),
    );
  }, [account.options, query]);

  const changed = !sameStates(selected, account.states);

  function toggleState(code: string) {
    setSaved("");
    setFailure("");
    setSelected((current) =>
      current.includes(code)
        ? current.filter((state) => state !== code)
        : [...current, code].sort(),
    );
  }

  async function beginReview() {
    if (!changed || previewBusy) return;
    setPreviewBusy(true);
    setFailure("");
    setSaved("");
    try {
      setReview(
        await previewLicensedStates(selected, account.version),
      );
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "The impact review could not be prepared. Please try again.",
      );
      if (
        caught instanceof LicensedStateRequestError
        && caught.status === 409
      ) {
        void revalidator.revalidate();
      }
    } finally {
      setPreviewBusy(false);
    }
  }

  async function confirmReview() {
    if (!review || applyBusy) return;
    setApplyBusy(true);
    setFailure("");
    try {
      const result = await applyLicensedStateReview(review.review_token);
      setAccount(result.account);
      setSelected(result.account.states);
      setReview(null);
      setSaved(
        "Licensed States saved. Overview and Batch Requests are up to date.",
      );
      void revalidator.revalidate();
    } catch (caught) {
      setReview(null);
      setFailure(
        caught instanceof Error
          ? caught.message
          : "Licensed States could not be updated. Please try again.",
      );
      if (
        caught instanceof LicensedStateRequestError
        && caught.status === 409
      ) {
        void revalidator.revalidate();
      }
    } finally {
      setApplyBusy(false);
    }
  }

  return (
    <Page
      title="Account"
      description="Keep the states where you are licensed accurate and review every Batch Request consequence before saving."
    >
      {failure ? (
        <div className="licensed-state-page__message licensed-state-page__message--error" role="alert">
          {failure}
        </div>
      ) : null}
      {saved ? (
        <div className="licensed-state-page__message licensed-state-page__message--success" role="status">
          {saved}
        </div>
      ) : null}

      <Section
        title="Licensed States"
        description="Search by state name or code, then select every state where you are currently licensed."
      >
        <Card>
          <Stack gap={5}>
            <Field
              label="Search states"
              description="Examples: Texas, TX, or Carolina."
            >
              <Input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                placeholder="Search by name or code"
              />
            </Field>

            <Fieldset
              legend="States where you are licensed"
              description={`${selected.length} selected. Removing a state may narrow or cancel unallocated Batch Requests.`}
            >
              {filteredOptions.length ? (
                <div className="licensed-state-selector">
                  {filteredOptions.map((option) => (
                    <label key={option.code} className="licensed-state-selector__choice">
                      <input
                        type="checkbox"
                        checked={selected.includes(option.code)}
                        onChange={() => toggleState(option.code)}
                      />
                      <span>
                        <strong>{option.name}</strong>
                        <span className="licensed-state-selector__code">
                          {option.code}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              ) : (
                <Text role="status">
                  No states match “{query}”.
                </Text>
              )}
            </Fieldset>

            <Card padding={4} className="licensed-state-page__future-note">
              <Stack gap={2}>
                <Heading level={3} size="sm">
                  Additions affect future requests only
                </Heading>
                <Text size="sm">
                  Adding a state will make it available for future Batch
                  Requests. It will not expand any request you already submitted.
                </Text>
              </Stack>
            </Card>

            <Cluster justify="space-between">
              <Text size="sm" tone="muted">
                {changed
                  ? `Proposed: ${formatStates(selected)}`
                  : `Saved: ${formatStates(account.states)}`}
              </Text>
              <Button
                variant="primary"
                disabled={!changed}
                busy={previewBusy}
                busyLabel="Preparing review…"
                onClick={() => void beginReview()}
              >
                Review changes
              </Button>
            </Cluster>
          </Stack>
        </Card>
      </Section>

      <ConfirmDialog
        open={review !== null}
        onClose={() => {
          if (!applyBusy) setReview(null);
        }}
        onConfirm={() => void confirmReview()}
        title="Review Licensed State changes"
        consequence="Nothing changes until you confirm. Saving applies the Licensed States and every request update below together."
        confirmLabel="Confirm and save"
        cancelLabel="Keep editing"
        destructive={Boolean(review?.removed_states.length)}
        busy={applyBusy}
      >
        {review ? <ReviewContents review={review} /> : null}
      </ConfirmDialog>
    </Page>
  );
}
