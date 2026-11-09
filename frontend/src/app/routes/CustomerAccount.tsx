import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  Link,
  Navigate,
  useLoaderData,
  useParams,
  useRevalidator,
  useRouteLoaderData,
} from "react-router";

import { Button } from "../../design-system/primitives/Button";
import { ConfirmDialog } from "../../design-system/primitives/Dialog";
import { Field, Input } from "../../design-system/primitives/form";
import { Cluster, Page, Section, Stack } from "../../design-system/primitives/layout";
import { Panel, RecordColumns } from "../../design-system/primitives/panel";
import { StatusBadge } from "../../design-system/primitives/status";
import { Tabs } from "../../design-system/primitives/tabs";
import { Mono, Text } from "../../design-system/primitives/typography";
import { useBilledWallet } from "../billing/CreditWalletContext";
import { ACCOUNT_TABS, accountTabPath } from "../customer/account";
import type { AccountTab } from "../customer/account";
import { AppointedStatesPicker, stateNames } from "../customer/AppointedStatesPicker";
import type { StateOption } from "../customer/AppointedStatesPicker";
import { batchLabel } from "../customer/batchLabel";
import { CustomerSessionActions, SUPPORT_HREF } from "../customer/CustomerSessionActions";
import { formatStates, plural } from "../customer/format";
import type { CustomerHomeData } from "../customer/home";
import { useDocumentTitle } from "../shell/useDocumentTitle";
import { CustomerAccountBilling } from "./CustomerAccountBilling";
import { CustomerAccountPassword } from "./CustomerAccountPassword";
import { CustomerExclusionListsSection } from "./CustomerExclusionLists";
import {
  LicensedStateRequestError,
  applyLicensedStateReview,
  previewLicensedStates,
  updateCustomerProfile,
} from "./licensedStates";
import type {
  CustomerAccountIdentity,
  CustomerAccountWorkspace,
  LicensedStateImpact,
  LicensedStateReview,
  LicensedStateWorkspace,
} from "./licensedStates";

import "./CustomerAccount.css";

// --- Tabs ---------------------------------------------------------------------

function accountTab(slug: string | undefined): AccountTab | null {
  if (slug === undefined) return "profile";
  const match = ACCOUNT_TABS.find((tab) => tab.key === slug && tab.key !== "profile");
  return match ? match.key : null;
}

/** "River Morgan · Northstar Insurance". */
function accountOwner(identity: CustomerAccountIdentity, customerName: string | undefined): string {
  const person = `${identity.first_name} ${identity.last_name}`.trim();
  if (!person) return customerName || identity.email;
  const company = customerName?.trim();
  if (company && company.toLocaleLowerCase() !== person.toLocaleLowerCase()) {
    return `${person} · ${company}`;
  }
  return `${person} · ${identity.email}`;
}

function Message({ tone, children }: { tone: "error" | "success"; children: ReactNode }) {
  return (
    <div
      className={`account-tab__message account-tab__message--${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

// --- Profile ------------------------------------------------------------------

interface SetupProblem {
  key: "mapping_unconfirmed" | "no_appointed_states";
  name: string;
  next: ReactNode;
}

function setupProblems(identity: CustomerAccountIdentity, states: string[]): SetupProblem[] {
  const problems: SetupProblem[] = [];
  if (identity.customer_id === null || identity.mapping_confirmed_at === null) {
    problems.push({
      key: "mapping_unconfirmed",
      name: "Your account is being set up",
      next: "Noah is connecting this sign-in to your account. You can request batches as soon as that is done; there is nothing you need to do.",
    });
  }
  if (!states.length) {
    problems.push({
      key: "no_appointed_states",
      name: "No appointed states",
      next: (
        <>
          Add at least one on <Link to={accountTabPath("states")}>Appointed states</Link>.
          You can request batches once it is saved.
        </>
      ),
    });
  }
  return problems;
}

function SetupStatus({ problems }: { problems: SetupProblem[] }) {
  if (!problems.length) return null;
  return (
    <Section title="Before you can request batches">
      <ul className="account-setup">
        {problems.map((problem) => (
          <li key={problem.key} className="account-setup__problem">
            <Cluster justify="space-between" align="baseline">
              <strong>{problem.name}</strong>
              <StatusBadge tone="danger">Needs setup</StatusBadge>
            </Cluster>
            <Text size="sm">{problem.next}</Text>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function YourDetails({
  identity,
  states,
  onSaved,
}: {
  identity: CustomerAccountIdentity;
  states: string[];
  onSaved: (next: CustomerAccountIdentity) => void;
}) {
  const [firstName, setFirstName] = useState(identity.first_name);
  const [lastName, setLastName] = useState(identity.last_name);
  const [phone, setPhone] = useState(identity.phone);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    setFirstName(identity.first_name);
    setLastName(identity.last_name);
    setPhone(identity.phone);
  }, [identity.first_name, identity.last_name, identity.phone]);

  const changed =
    firstName.trim() !== identity.first_name
    || lastName.trim() !== identity.last_name
    || phone.trim() !== identity.phone;

  function edit(set: (value: string) => void) {
    return (value: string) => {
      setSaved("");
      setFailure("");
      set(value);
    };
  }

  async function save() {
    if (!changed || busy) return;
    setBusy(true);
    setFailure("");
    setSaved("");
    try {
      const next = await updateCustomerProfile({
        first_name: firstName,
        last_name: lastName,
        phone,
        licensed_states: states,
      });
      onSaved({
        ...identity,
        first_name: next.first_name,
        last_name: next.last_name,
        phone: next.phone,
      });
      setSaved("Profile saved.");
    } catch (caught) {
      setFailure(
        caught instanceof Error ? caught.message : "Profile could not be saved. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Your details" description="The person who signs in to this account.">
      <form
        className="account-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="account-form__fields">
          <Field label="First name">
            <Input
              value={firstName}
              autoComplete="given-name"
              onChange={(event) => edit(setFirstName)(event.currentTarget.value)}
            />
          </Field>
          <Field label="Last name">
            <Input
              value={lastName}
              autoComplete="family-name"
              onChange={(event) => edit(setLastName)(event.currentTarget.value)}
            />
          </Field>
          <Field label="Phone">
            <Input
              type="tel"
              value={phone}
              autoComplete="tel"
              onChange={(event) => edit(setPhone)(event.currentTarget.value)}
            />
          </Field>
          <Field label="Sign-in email">
            <Input value={identity.email} disabled readOnly />
          </Field>
        </div>
        <Text size="sm" tone="muted">
          To change your sign-in email, email noah@jawnix.com.
        </Text>
        {failure ? <Message tone="error">{failure}</Message> : null}
        {saved ? <Message tone="success">{saved}</Message> : null}
        <div>
          <Button type="submit" variant="primary" disabled={!changed} busy={busy} busyLabel="Saving…">
            Save profile
          </Button>
        </div>
      </form>
    </Section>
  );
}

/**
 * The colophon from the brand book (ch1/ch10): the account names the one
 * accountable person, and replies reach him.
 */
function YourContact() {
  return (
    <Panel title="Your contact" className="account-contact">
      <Text size="sm">
        JAWNIX is run by <strong>Noah Bleicher</strong>. Questions, billing,
        and bad leads come to me.
      </Text>
      <Text size="sm">
        <Mono>
          <a href={SUPPORT_HREF}>noah@jawnix.com</a>
        </Mono>
      </Text>
    </Panel>
  );
}

function ProfileTab({
  identity,
  states,
  onSaved,
}: {
  identity: CustomerAccountIdentity;
  states: string[];
  onSaved: (next: CustomerAccountIdentity) => void;
}) {
  return (
    <RecordColumns
      main={
        <Stack gap={6}>
          <SetupStatus problems={setupProblems(identity, states)} />
          <YourDetails identity={identity} states={states} onSaved={onSaved} />
        </Stack>
      }
      aside={<YourContact />}
    />
  );
}

// --- Appointed states ---------------------------------------------------------

function sameStates(left: string[], right: string[]): boolean {
  return [...left].sort().join(",") === [...right].sort().join(",");
}

function namesOrNone(options: StateOption[], codes: string[]): string {
  const names = stateNames(options, codes);
  return names.length ? names.join(", ") : "None";
}

function Impact({ impact }: { impact: LicensedStateImpact }) {
  const canceled = impact.action === "canceled";
  return (
    <li className="account-review__impact">
      <Cluster justify="space-between" align="baseline">
        <strong>Batch {batchLabel({ id: impact.request_id, number: impact.number ?? null })}</strong>
        <StatusBadge tone={canceled ? "danger" : "info"}>
          {canceled ? "Will be canceled" : "Will be narrowed"}
        </StatusBadge>
      </Cluster>
      <Text size="sm">
        {`${plural(impact.lead_count, "lead")} · ${impact.status} · ${formatStates(impact.current_states)}`}
        {canceled ? "" : ` → ${formatStates(impact.resulting_states)}`}
      </Text>
      {canceled ? (
        <Text size="sm">None of its states would remain, so it would be canceled.</Text>
      ) : null}
    </li>
  );
}

function ReviewContents({ review, options }: { review: LicensedStateReview; options: StateOption[] }) {
  return (
    <Stack gap={4}>
      <dl className="account-review__summary">
        <div>
          <dt>Adding</dt>
          <dd>{namesOrNone(options, review.added_states)}</dd>
        </div>
        <div>
          <dt>Removing</dt>
          <dd>{namesOrNone(options, review.removed_states)}</dd>
        </div>
      </dl>
      {review.added_states.length ? (
        <Text size="sm">New states apply to new requests; batches already requested keep their states.</Text>
      ) : null}
      <Stack gap={2}>
        <Text weight="semibold">
          {review.impacts.length === 1
            ? "This batch in progress changes:"
            : `These ${review.impacts.length} batches in progress change:`}
        </Text>
        <ul className="account-review__impacts">
          {review.impacts.map((impact) => (
            <Impact key={impact.request_id} impact={impact} />
          ))}
        </ul>
      </Stack>
    </Stack>
  );
}

function StatesTab({ account }: { account: LicensedStateWorkspace }) {
  const revalidator = useRevalidator();
  const [saved, setSaved] = useState(account);
  const [selected, setSelected] = useState(account.states);
  const [review, setReview] = useState<LicensedStateReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [notice, setNotice] = useState("");

  // New loader data (after saving, or after another session changed them)
  // resets the selection to what is saved. Only a new read does this; the
  // save's own answer must not be undone while that read is on its way.
  const [loadedVersion, setLoadedVersion] = useState(account.version);
  if (account.version !== loadedVersion) {
    setLoadedVersion(account.version);
    setSaved(account);
    setSelected(account.states);
  }

  const changed = !sameStates(selected, saved.states);

  function refused(caught: unknown, fallback: string) {
    setFailure(caught instanceof Error ? caught.message : fallback);
    if (caught instanceof LicensedStateRequestError && caught.status === 409) {
      void revalidator.revalidate();
    }
  }

  async function apply(token: string) {
    const result = await applyLicensedStateReview(token);
    setSaved(result.account);
    setSelected(result.account.states);
    setReview(null);
    setNotice("Appointed states saved. New requests use them from now on.");
    void revalidator.revalidate();
  }

  async function save() {
    if (!changed || busy) return;
    setBusy(true);
    setFailure("");
    setNotice("");
    try {
      const prepared = await previewLicensedStates(selected, saved.version);
      // Nothing in progress changes (adding a state never does): save now.
      if (!prepared.impacts.length) await apply(prepared.review_token);
      else setReview(prepared);
    } catch (caught) {
      refused(caught, "Appointed states could not be saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!review || busy) return;
    setBusy(true);
    setFailure("");
    try {
      await apply(review.review_token);
    } catch (caught) {
      setReview(null);
      refused(caught, "Appointed states could not be saved. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {failure ? <Message tone="error">{failure}</Message> : null}
      {notice ? <Message tone="success">{notice}</Message> : null}
      <form
        className="account-states"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <AppointedStatesPicker
          options={saved.options}
          selected={selected}
          onChange={(next) => {
            setNotice("");
            setFailure("");
            setSelected(next);
          }}
        />
        <div className="account-states__save">
          <Button type="submit" variant="primary" disabled={!changed} busy={busy && !review} busyLabel="Saving…">
            Save
          </Button>
          <Text size="xs" tone="muted">
            New states apply to new requests. Removing a state asks you to
            confirm if it affects a request in progress.
          </Text>
        </div>
      </form>
      <ConfirmDialog
        open={review !== null}
        onClose={() => {
          if (!busy) setReview(null);
        }}
        onConfirm={() => void confirm()}
        title="Confirm appointed states"
        consequence="Nothing changes until you confirm. Saving updates your appointed states and these batches together."
        confirmLabel="Confirm and save"
        cancelLabel="Keep editing"
        destructive={Boolean(review?.impacts.some((impact) => impact.action === "canceled"))}
        busy={busy}
      >
        {review ? <ReviewContents review={review} options={saved.options} /> : null}
      </ConfirmDialog>
    </>
  );
}

// --- The page -----------------------------------------------------------------

export function CustomerAccountRoute() {
  const loaded = useLoaderData<CustomerAccountWorkspace>();
  const home = useRouteLoaderData<CustomerHomeData>("customer");
  const params = useParams();
  const billed = useBilledWallet() !== null;
  const [identity, setIdentity] = useState(loaded.identity);
  const tab = accountTab(params.tab);
  const label = ACCOUNT_TABS.find((item) => item.key === tab)?.label ?? "Profile";
  useDocumentTitle(tab === "profile" || tab === null ? "Account" : `${label} · Account`);

  useEffect(() => {
    setIdentity(loaded.identity);
  }, [loaded.identity]);

  if (tab === null) return <Navigate to="/account" replace />;

  // Billing appears for billed Customers, and for anyone who opens its address.
  const items = ACCOUNT_TABS.filter((item) => item.key !== "billing" || billed || tab === "billing").map(
    (item) => ({ key: item.key, label: item.label, href: `/app${accountTabPath(item.key)}` }),
  );

  return (
    <Page title="Account" width="data" description={accountOwner(identity, home?.customer_name)}>
      {/* Phones keep Support and Sign out here; the header has no room (V8). */}
      <div className="account-session">
        <CustomerSessionActions />
      </div>
      <Tabs label="Account" items={items} current={tab} />
      {tab === "profile" ? (
        <ProfileTab identity={identity} states={loaded.licensed_states.states} onSaved={setIdentity} />
      ) : null}
      {tab === "states" ? <StatesTab account={loaded.licensed_states} /> : null}
      {tab === "billing" ? <CustomerAccountBilling /> : null}
      {tab === "exclusion-lists" ? <CustomerExclusionListsSection /> : null}
      {tab === "password" ? <CustomerAccountPassword email={identity.email} /> : null}
    </Page>
  );
}
