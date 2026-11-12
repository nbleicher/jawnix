import { useState } from "react";
import { useRevalidator } from "react-router";
import { api } from "../api";
import { Button } from "../../design-system/primitives/Button";
import { Dialog } from "../../design-system/primitives/Dialog";
import { Cluster, Section, Stack } from "../../design-system/primitives/layout";
import { Text } from "../../design-system/primitives/typography";

export interface PendingRegistration {
  id: string;
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  states: string[];
  matches: { id: number; name: string; slug: string; email: string }[];
}
type Decision = { decision: "new" | "restore" | "reject"; restore_customer_id?: number };

/** The three decisions on a registration, each behind its confirmation. */
export function RegistrationDecisions({ item, onDecided }: { item: PendingRegistration; onDecided?: () => void }) {
  const revalidator = useRevalidator();
  const [decision, setDecision] = useState<Decision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function confirm() {
    if (!decision || busy) return;
    setBusy(true); setError("");
    try {
      await api(`/api/admin/registrations/${item.id}/decision`, { method: "POST", body: JSON.stringify(decision) });
      setDecision(null);
      onDecided?.();
      await revalidator.revalidate();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "This registration could not be updated."); }
    finally { setBusy(false); }
  }
  return <>
    <Cluster gap={3}>
      {item.matches.map(match => <Button key={match.id} onClick={() => setDecision({ decision: "restore", restore_customer_id: match.id })}>
        Restore {match.slug} ({match.email || "No previous email"})
      </Button>)}
      <Button onClick={() => setDecision({ decision: "new" })}>Approve as new Customer</Button>
      <Button onClick={() => setDecision({ decision: "reject" })}>Reject registration</Button>
    </Cluster>
    <Dialog open={decision !== null} onClose={() => { if (!busy) setDecision(null); }} dismissOnBackdrop={false}
      title={decision?.decision === "restore" ? "Restore this Customer's history?" : decision?.decision === "reject" ? "Reject this registration?" : "Create a separate Customer?"}
      description={decision?.decision === "restore" ? `Grant ${item.email} access to the selected Customer's previous batches and history.` : decision?.decision === "reject" ? "This registration will remain blocked. Existing history is preserved." : "Activate this registration with a new Customer and no previous history."}
      footer={<Cluster gap={3}><Button disabled={busy} onClick={() => setDecision(null)}>Cancel</Button><Button variant="primary" busy={busy} onClick={() => { void confirm(); }}>Confirm decision</Button></Cluster>}>
      {error ? <p role="alert">{error}</p> : null}
    </Dialog>
  </>;
}

function RegistrationRow({ item }: { item: PendingRegistration }) {
  const name = `${item.firstName} ${item.lastName}`.trim() || item.email;
  return <Stack as="li" gap={3} className="registration-review__item" aria-label={name}>
    <Text><strong>{name}</strong> · {item.email} · {item.phone} · {item.states.join(", ") || "No states"}</Text>
    <Text size="sm" tone="muted">
      {item.matches.length
        ? "Compare the name and email before granting access to an archived Customer."
        : "No archived Customer matches this name."}
    </Text>
    <RegistrationDecisions item={item} />
  </Stack>;
}

/** Verified sign-ups waiting on the operator, reachable from Customers and
 *  Today. Hidden when none are waiting. */
export function RegistrationReview({ items }: { items: PendingRegistration[] }) {
  if (!items.length) return null;
  return <Section id="registrations" title="Registrations awaiting review" description="These verified registrations match archived customer names. Only the operator can decide whether to restore history.">
    <Stack as="ul" gap={5} className="registration-review">{items.map(item => <RegistrationRow key={item.id} item={item} />)}</Stack>
  </Section>;
}
