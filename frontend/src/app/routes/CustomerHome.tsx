import { useEffect, useId, useState } from "react";
import { useRevalidator, useRouteLoaderData } from "react-router";

import { ApplicationLink } from "../../design-system/primitives/ApplicationLink";
import { ActionLink, Button, ButtonLink } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { EmptyState } from "../../design-system/primitives/feedback";
import { Kpi, KpiRow } from "../../design-system/primitives/kpi";
import { Page, Section } from "../../design-system/primitives/layout";
import { Text, VisuallyHidden } from "../../design-system/primitives/typography";
import { useBilledWallet, useCreditWallet } from "../billing/CreditWalletContext";
import { PurchaseDialog } from "../billing/CreditWalletWidget";
import { formatCents } from "../billing/money";
import { batchLabel, batchName } from "../customer/batchLabel";
import { SUPPORT_HREF } from "../customer/CustomerSessionActions";
import {
  formatClock,
  formatCount,
  formatDay,
  formatExpiresIn,
  formatRatePerThousand,
  formatStates,
  formatStepTime,
  formatSubmitted,
  calendarDaysUntil,
  plural,
} from "../customer/format";
import type {
  CustomerHomeAlert,
  CustomerHomeBatch,
  CustomerHomeBilling,
  CustomerHomeData,
  CustomerHomeInProgress,
  CustomerHomeStep,
} from "../customer/home";
import { BATCH_HISTORY_HASH } from "../customer/navigation";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { cancelBatchRequest } from "./batchRequests";
import { useLiveRequests } from "./useLiveRequests";

import "./CustomerHome.css";

/** Until New request and Batches have pages of their own (C3). */
const NEW_REQUEST_HREF = "/app/requests";
const ALL_BATCHES_HREF = `/app/requests${BATCH_HISTORY_HASH}`;
const ACCOUNT_HREF = "/app/account";

/** More appointed states than this read as a count in the tile. */
const STATE_CODES_IN_TILE = 4;

// --- Alerts -------------------------------------------------------------------

function alertKey(alert: CustomerHomeAlert): string {
  return `${alert.kind}:${alert.batch?.id ?? alert.problem ?? ""}`;
}

function FileExpiringAlert({ batch, now }: { batch: CustomerHomeBatch; now: Date }) {
  const expiresAt = batch.file_expires_at ?? "";
  const from = batch.delivered_at ?? batch.submitted_at;
  const until =
    calendarDaysUntil(expiresAt, now) <= 0
      ? `${formatClock(expiresAt)} today`
      : formatDay(expiresAt, now);
  return (
    <>
      <p className="customer-alert__text">
        <strong>File expires {formatExpiresIn(expiresAt, now)}.</strong> Your{" "}
        {formatCount(batch.lead_count)}-lead batch from {formatDay(from, now)} is
        available until {until}.
      </p>
      {batch.download_href ? (
        <ButtonLink
          href={batch.download_href}
          variant="secondary"
          aria-label={`Download ${batchName(batch)}`}
        >
          Download
        </ButtonLink>
      ) : null}
    </>
  );
}

function LowBalanceAlert({
  billing,
  referenceLeadCount,
  onBuyCredits,
}: {
  billing: CustomerHomeBilling;
  referenceLeadCount: number | null;
  onBuyCredits: () => void;
}) {
  const rate = billing.lead_rate_cents_per_thousand;
  const covers =
    rate !== null && billing.covers_leads !== null
      ? ` covers ${plural(billing.covers_leads, "lead")} at ${formatRatePerThousand(rate)}`
      : " is available";
  return (
    <>
      <p className="customer-alert__text">
        <strong>Low balance.</strong> {formatCents(billing.available_balance_cents)}
        {covers}.{" "}
        {referenceLeadCount !== null
          ? `Your last batch was ${plural(referenceLeadCount, "lead")}.`
          : "Buy credits before you request a batch."}
      </p>
      <Button variant="secondary" onClick={onBuyCredits}>
        Buy credits
      </Button>
    </>
  );
}

function SetupAlert({ problem }: { problem: NonNullable<CustomerHomeAlert["problem"]> }) {
  if (problem === "no_appointed_states") {
    return (
      <>
        <p className="customer-alert__text">
          <strong>Setup incomplete.</strong> Add your appointed states before you
          request a batch.
        </p>
        <ButtonLink href={ACCOUNT_HREF} variant="secondary">
          Add appointed states
        </ButtonLink>
      </>
    );
  }
  return (
    <>
      <p className="customer-alert__text">
        {problem === "account_pending" ? (
          <>
            <strong>Setup incomplete.</strong> We are still setting up your
            account. You can request a batch as soon as it is ready.
          </>
        ) : (
          <>
            <strong>Account paused.</strong> New requests are paused on your
            account. Email us to start again.
          </>
        )}
      </p>
      <ButtonLink href={SUPPORT_HREF} variant="secondary">
        Email support
      </ButtonLink>
    </>
  );
}

function HomeAlert({
  alert,
  billing,
  now,
  onBuyCredits,
}: {
  alert: CustomerHomeAlert;
  billing: CustomerHomeBilling | null;
  now: Date;
  onBuyCredits: () => void;
}) {
  let body = null;
  if (alert.kind === "file_expiring" && alert.batch) {
    body = <FileExpiringAlert batch={alert.batch} now={now} />;
  } else if (alert.kind === "low_balance" && billing) {
    body = (
      <LowBalanceAlert
        billing={billing}
        referenceLeadCount={alert.reference_lead_count}
        onBuyCredits={onBuyCredits}
      />
    );
  } else if (alert.kind === "setup_incomplete" && alert.problem) {
    body = <SetupAlert problem={alert.problem} />;
  }
  if (!body) return null;
  // Red means the Customer has something to do (V4); a pending account only
  // waits on us.
  const tone = alert.problem === "account_pending" ? "neutral" : "action";
  return <li className={`customer-alert customer-alert--${tone}`}>{body}</li>;
}

// --- Numbers ------------------------------------------------------------------

function HomeNumbers({ home }: { home: CustomerHomeData }) {
  const { billing, appointed_states: states, month } = home;
  const collapsed = states.length > STATE_CODES_IN_TILE;
  const editVerb = states.length ? "Edit" : "Add";
  const edit = (
    <ApplicationLink
      href={ACCOUNT_HREF}
      className="customer-home__edit"
      aria-label={`${editVerb} appointed states`}
    >
      {editVerb}
    </ApplicationLink>
  );
  const heldFor = billing?.held_for ?? [];
  return (
    <KpiRow label="Account summary" columns={billing ? 4 : 2}>
      {billing ? (
        <Kpi
          label="Balance"
          value={formatCents(billing.available_balance_cents)}
          meta={
            billing.lead_rate_cents_per_thousand !== null
            && billing.covers_leads !== null
              ? `Covers ${plural(billing.covers_leads, "lead")} at ${formatRatePerThousand(billing.lead_rate_cents_per_thousand)}`
              : "Available for new requests"
          }
          alert={billing.available_balance_cents <= 0}
        />
      ) : null}
      <Kpi
        label="Appointed states"
        value={
          states.length === 0
            ? "None"
            : collapsed
              ? plural(states.length, "state")
              : formatStates(states)
        }
        meta={
          collapsed ? (
            <>
              {formatStates(states)} · {edit}
            </>
          ) : (
            edit
          )
        }
      />
      <Kpi
        label="Delivered this month"
        value={formatCount(month.delivered_leads)}
        meta={plural(month.delivered_batches, "batch", "batches")}
      />
      {billing ? (
        <Kpi
          label="Spent this month"
          value={formatCents(billing.spent_this_month_cents)}
          meta={
            billing.held_cents > 0
              ? `${formatCents(billing.held_cents)} held for ${
                  heldFor.length === 1 && heldFor[0]
                    ? batchLabel(heldFor[0])
                    : plural(heldFor.length, "batch", "batches")
                }`
              : "Nothing held"
          }
        />
      ) : null}
    </KpiRow>
  );
}

// --- Recent batches -------------------------------------------------------------

function BatchAction({ batch }: { batch: CustomerHomeBatch }) {
  if (batch.download_href) {
    return (
      <ButtonLink
        href={batch.download_href}
        variant="secondary"
        aria-label={`Download ${batchName(batch)}`}
      >
        Download
      </ButtonLink>
    );
  }
  // C3 replaces this with "Get a new copy".
  return (
    <ButtonLink
      href={batch.href}
      variant="secondary"
      aria-label={`View ${batchName(batch)}, file expired`}
    >
      View batch
    </ButtonLink>
  );
}

function RecentBatches({
  batches,
  total,
  showCost,
  now,
}: {
  batches: CustomerHomeBatch[];
  total: number;
  showCost: boolean;
  now: Date;
}) {
  return (
    <Section title="Recent batches">
      {batches.length ? (
        <div className="customer-batches">
          <table className="customer-batches__table" aria-label="Recent batches">
            <thead>
              <tr>
                <th scope="col">Batch</th>
                <th scope="col" className="customer-batches__numeric">
                  Leads
                </th>
                <th scope="col">States</th>
                <th scope="col">Delivered</th>
                {showCost ? (
                  <th scope="col" className="customer-batches__numeric">
                    Cost
                  </th>
                ) : null}
                <th scope="col">
                  <VisuallyHidden>File</VisuallyHidden>
                </th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => {
                const delivered = batch.delivered_at
                  ? formatDay(batch.delivered_at, now)
                  : "—";
                const cost =
                  batch.cost_cents === null ? "—" : formatCents(batch.cost_cents);
                return (
                  <tr key={batch.id}>
                    <td className="customer-batches__record">
                      <ApplicationLink href={batch.href}>
                        {batchLabel(batch)}
                      </ApplicationLink>
                      {/* Phones stack the row into labelled lines (V8). */}
                      <span className="customer-batches__stacked">
                        <span>
                          {plural(batch.lead_count, "lead")} ·{" "}
                          {formatStates(batch.states)}
                        </span>
                        <span>
                          Delivered {delivered}
                          {showCost ? ` · ${cost}` : ""}
                        </span>
                      </span>
                    </td>
                    <td className="customer-batches__numeric customer-batches__wide">
                      {formatCount(batch.lead_count)}
                    </td>
                    <td className="customer-batches__wide">
                      {formatStates(batch.states)}
                    </td>
                    <td className="customer-batches__wide">{delivered}</td>
                    {showCost ? (
                      <td className="customer-batches__numeric customer-batches__wide">
                        {cost}
                      </td>
                    ) : null}
                    <td className="customer-batches__action">
                      <BatchAction batch={batch} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Text size="sm" tone="muted">
          Nothing delivered yet. Your batches appear here when they are ready to
          download.
        </Text>
      )}
      <div>
        <ActionLink href={ALL_BATCHES_HREF}>All batches ({formatCount(total)})</ActionLink>
      </div>
    </Section>
  );
}

// --- In progress --------------------------------------------------------------

const STEP_STATE_WORDS: Record<CustomerHomeStep["state"], string> = {
  complete: "Done",
  current: "Now",
  upcoming: "Not yet",
};

function stepTime(step: CustomerHomeStep, now: Date): string {
  if (step.state === "current") return "Now";
  if (step.state === "complete" && step.occurred_at) {
    return formatStepTime(step.occurred_at, now);
  }
  return "";
}

function InProgressPanel({
  item,
  now,
  onCanceled,
}: {
  item: CustomerHomeInProgress;
  now: Date;
  onCanceled: () => Promise<void>;
}) {
  const { batch } = item;
  const titleId = useId();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");

  async function cancel() {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      await cancelBatchRequest(batch.id);
      setConfirming(false);
      await onCanceled();
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "This batch could not be canceled. Please try again.",
      );
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  }

  const held = item.held_cents !== null ? formatCents(item.held_cents) : null;
  return (
    <section className="customer-home-panel" aria-labelledby={titleId}>
      <h2 className="customer-home-panel__title" id={titleId}>
        In progress · {batchLabel(batch)}
      </h2>
      <Text size="sm">
        {plural(batch.lead_count, "lead")} · {formatStates(batch.states)} ·
        submitted {formatSubmitted(batch.submitted_at, now)}
      </Text>
      <ol className="customer-home-steps" aria-label={`Progress of ${batchName(batch)}`}>
        {item.steps.map((step) => (
          <li
            key={step.key}
            className={`customer-home-steps__step customer-home-steps__step--${step.state}`}
            aria-current={step.state === "current" ? "step" : undefined}
          >
            <span className="customer-home-steps__mark" aria-hidden="true" />
            <span>
              <VisuallyHidden>{STEP_STATE_WORDS[step.state]}: </VisuallyHidden>
              {step.label}
            </span>
            <span className="customer-home-steps__time" aria-hidden={step.state === "current" ? true : undefined}>
              {stepTime(step, now)}
            </span>
          </li>
        ))}
      </ol>
      <Text size="xs" tone="muted">
        {item.note}
        {held ? ` ${held} is held from your balance until then.` : ""}
      </Text>
      {failure ? (
        <Text size="sm" tone="danger" role="alert">
          {failure}
        </Text>
      ) : null}
      {item.can_cancel ? (
        <div>
          <Button variant="danger" onClick={() => setConfirming(true)}>
            Cancel batch
          </Button>
        </div>
      ) : null}
      <ConfirmDialog
        open={confirming}
        onClose={() => {
          if (!busy) setConfirming(false);
        }}
        onConfirm={() => void cancel()}
        title={`Cancel ${batchName(batch)}?`}
        consequence={
          held
            ? `Nothing has been delivered yet. Canceling releases the ${held} held from your balance and cannot be undone.`
            : "Nothing has been delivered yet. Canceling cannot be undone."
        }
        confirmLabel="Cancel batch"
        cancelLabel="Keep batch"
        busy={busy}
      />
    </section>
  );
}

// --- The route ------------------------------------------------------------------

export function CustomerHomeRoute() {
  const home = useRouteLoaderData<CustomerHomeData>("customer");
  useDocumentTitle("Home");
  if (!home) {
    throw new Error("Home was not loaded.");
  }
  return <HomePage home={home} />;
}

function HomePage({ home }: { home: CustomerHomeData }) {
  const revalidator = useRevalidator();
  const { refresh: refreshWallet } = useCreditWallet();
  const wallet = useBilledWallet();
  const [buying, setBuying] = useState(false);
  const now = new Date();

  // The header balance is polled while a purchase settles. When it moves,
  // read Home again so the tiles and alerts agree with it. Only a change in
  // the live balance is a reason to read again, so Home's own figure is not
  // a dependency.
  const homeBalance = home.billing?.available_balance_cents;
  const walletBalance = wallet?.availableBalanceCents;
  useEffect(() => {
    if (
      walletBalance === undefined
      || homeBalance === undefined
      || walletBalance === homeBalance
    ) {
      return;
    }
    void revalidator.revalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [walletBalance]);

  // Batches in progress move on their own: approval, preparation, delivery.
  useLiveRequests(
    "/api/me/batch-requests/events",
    home.in_progress.map((item) => item.batch.id),
    () => revalidator.revalidate(),
    () => void revalidator.revalidate(),
  );

  async function afterCancel() {
    await Promise.all([refreshWallet(), revalidator.revalidate()]);
  }

  const showCost = home.billing !== null
    || home.recent_batches.some((batch) => batch.cost_cents !== null);

  return (
    <div className="customer-home">
      <Page
        title="Home"
        density="data"
        description={home.customer_name}
        actions={
          <ButtonLink href={NEW_REQUEST_HREF} variant="primary">
            New request
          </ButtonLink>
        }
      >
        {home.alerts.length ? (
          <ul className="customer-alerts" aria-label="Needs your attention">
            {home.alerts.map((alert) => (
              <HomeAlert
                key={alertKey(alert)}
                alert={alert}
                billing={home.billing}
                now={now}
                onBuyCredits={() => setBuying(true)}
              />
            ))}
          </ul>
        ) : null}

        <HomeNumbers home={home} />

        {home.batch_count === 0 ? (
          <EmptyState
            title="No batches yet"
            description="Choose how many leads you want and which appointed states. We review each request and email you when your batch is ready."
            action={
              <ButtonLink href={NEW_REQUEST_HREF} variant="secondary">
                Request your first batch
              </ButtonLink>
            }
          />
        ) : (
          <div className="customer-home__columns">
            <RecentBatches
              batches={home.recent_batches}
              total={home.batch_count}
              showCost={showCost}
              now={now}
            />
            {home.in_progress.length ? (
              <div className="customer-home__aside">
                {home.in_progress.map((item) => (
                  <InProgressPanel
                    key={item.batch.id}
                    item={item}
                    now={now}
                    onCanceled={afterCancel}
                  />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </Page>
      {home.billing ? (
        <PurchaseDialog open={buying} onClose={() => setBuying(false)} />
      ) : null}
    </div>
  );
}
