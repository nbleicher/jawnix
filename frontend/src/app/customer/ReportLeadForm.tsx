import { useRef, useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/primitives/Button";
import { Field, Input, Select, Textarea } from "../../design-system/primitives/form";
import { Text } from "../../design-system/primitives/typography";
import { findBatchLead, reportLead } from "../routes/batchRequests";
import type { LeadReportReason } from "../routes/batchRequests";
import { batchName } from "./batchLabel";
import type { BatchIdentity } from "./batchLabel";

/** What can be wrong with a lead, in the Customer's words. */
export const REPORT_REASONS: { value: LeadReportReason; label: string }[] = [
  { value: "invalid_phone", label: "Number does not work" },
  { value: "wrong_business_or_title", label: "Wrong business" },
  { value: "wrong_state", label: "Wrong state" },
  { value: "duplicate", label: "Duplicate lead" },
  { value: "do_not_contact_or_legal", label: "Asked not to be contacted" },
  { value: "other", label: "Something else" },
];

function digits(value: string): string {
  return value.replace(/\D+/g, "");
}

/**
 * "Report a bad lead" on a delivered batch's page.
 *
 * The phone number is looked up within this batch only, then the report is
 * filed through the existing lead report endpoint. Noah reviews every report;
 * nothing here promises a credit.
 */
export function ReportLeadForm({ batch }: { batch: BatchIdentity }) {
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState<LeadReportReason>("invalid_phone");
  const [details, setDetails] = useState("");
  const [phoneError, setPhoneError] = useState("");
  const [failure, setFailure] = useState("");
  const [sent, setSent] = useState("");
  const [busy, setBusy] = useState(false);
  const phoneRef = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure("");
    setSent("");
    const number = digits(phone);
    if (number.length < 10 || number.length > 11) {
      setPhoneError("Enter the lead's 10-digit phone number.");
      phoneRef.current?.focus();
      return;
    }
    setPhoneError("");
    setBusy(true);
    try {
      const lead = await findBatchLead(batch.id, phone.trim());
      if (!lead) {
        setPhoneError(
          `That number is not in ${batchName(batch)}. Check the number and try again.`,
        );
        phoneRef.current?.focus();
        return;
      }
      await reportLead(lead.distributionEventId, reason, details.trim());
      setSent(
        `Report sent for ${lead.businessName || lead.phone}. Noah reviews every report and replies by email.`,
      );
      setPhone("");
      setDetails("");
      setReason("invalid_phone");
    } catch (caught) {
      setFailure(
        caught instanceof Error
          ? caught.message
          : "We could not send this report. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="customer-report" onSubmit={(event) => void submit(event)} noValidate>
      <div className="customer-report__fields">
        <Field label="Phone number" {...(phoneError ? { error: phoneError } : {})}>
          <Input
            ref={phoneRef}
            type="tel"
            inputMode="tel"
            autoComplete="off"
            placeholder="(555) 555-0100"
            value={phone}
            onChange={(event) => {
              setPhone(event.currentTarget.value);
              setPhoneError("");
            }}
          />
        </Field>
        <Field label="What is wrong?">
          <Select
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value as LeadReportReason)}
          >
            {REPORT_REASONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Details (optional)">
        <Textarea
          rows={3}
          maxLength={2000}
          value={details}
          onChange={(event) => setDetails(event.currentTarget.value)}
        />
      </Field>
      {failure ? (
        <Text size="sm" tone="danger" role="alert">
          {failure}
        </Text>
      ) : null}
      <div className="customer-report__submit">
        <Button type="submit" variant="secondary" busy={busy} busyLabel="Sending…">
          Send report
        </Button>
      </div>
      <Text size="xs" tone="muted" role="status">
        {sent || "Noah reviews every report and replies by email."}
      </Text>
    </form>
  );
}
