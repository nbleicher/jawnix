import type { Page, Route } from "@playwright/test";

/**
 * A seeded Customer Feedback flow.
 *
 * The catalog mirrors `jawnix/dispositions.py`, whose consequence strings are
 * derived from `feedback.REPORT_DISPOSITIONS` / `HOLD_DISPOSITIONS`. Keep the
 * three consequence-bearing entries in step with that module: the Python side
 * has `tests/test_feedback_catalog.py` pinning them to the enforcement rule.
 */

export const HOLD_CONSEQUENCE =
  "Submitting this files a Lead Report and places an Eligibility Hold, which withdraws this Lead from future batches. Only an administrator can release the hold.";

export const REPORT_ONLY_CONSEQUENCE =
  "Submitting this files a Lead Report for an administrator to review. It does not place an Eligibility Hold, so this Lead stays eligible for future batches.";

function option(
  disposition: string,
  label: string,
  description: string,
  extra: Partial<{
    requiresNote: boolean;
    createsReport: boolean;
    createsHold: boolean;
    consequence: string;
  }> = {},
) {
  return {
    disposition,
    label,
    description,
    requiresNote: false,
    createsReport: false,
    createsHold: false,
    consequence: "",
    ...extra,
  };
}

export const CATALOG = {
  groups: [
    {
      group: "contact_result",
      label: "Contact result",
      options: [
        option("no_contact", "No Contact", "You could not reach anyone."),
        option(
          "not_interested",
          "Not Interested",
          "You reached them and they declined.",
        ),
      ],
    },
    {
      group: "positive_progress",
      label: "Positive progress",
      options: [
        option(
          "positive_response",
          "Positive Response",
          "They were interested but nothing is scheduled yet.",
        ),
      ],
    },
    {
      group: "appointment_follow_up",
      label: "Appointment follow-up",
      options: [
        option(
          "appointment_booked",
          "Appointment Booked",
          "You scheduled a meeting.",
        ),
        option(
          "appointment_canceled",
          "Appointment Canceled",
          "A scheduled meeting was called off.",
        ),
        option(
          "appointment_no_show",
          "Appointment No-show",
          "They did not attend a scheduled meeting.",
        ),
      ],
    },
    {
      group: "data_compliance_problem",
      label: "Data or compliance problem",
      options: [
        option(
          "invalid_phone",
          "Invalid Phone",
          "The number is disconnected or not a working line.",
          {
            createsReport: true,
            createsHold: true,
            consequence: HOLD_CONSEQUENCE,
          },
        ),
        option(
          "wrong_business",
          "Wrong Business",
          "The number reaches a different business than listed.",
          {
            createsReport: true,
            createsHold: false,
            consequence: REPORT_ONLY_CONSEQUENCE,
          },
        ),
        option(
          "do_not_contact",
          "Do Not Contact",
          "They asked not to be contacted again.",
          {
            createsReport: true,
            createsHold: true,
            consequence: HOLD_CONSEQUENCE,
          },
        ),
      ],
    },
    {
      group: "other",
      label: "Other",
      options: [
        option("other", "Other", "Anything the choices above do not cover.", {
          requiresNote: true,
        }),
      ],
    },
  ],
  qualityRating: {
    optional: true,
    description:
      "Optional, and separate from your answer above. It records how good the Lead was and changes nothing else.",
    options: [
      {
        value: "good",
        label: "Good",
        description:
          "Counts this Lead as good quality in your feedback history.",
      },
      {
        value: "poor",
        label: "Poor",
        description:
          "Counts this Lead as poor quality in your feedback history. Nothing is withdrawn and nothing is filed for review.",
      },
    ],
  },
} as const;

export const DELIVERED_LEAD = {
  distributionEventId: 7,
  businessName: "Acme Roofing",
  phone: "2145550001",
  deliveredAt: "2026-07-20T15:00:00Z",
  batchId: "11111111-1111-4111-8111-111111111111",
  currentDisposition: null,
} as const;

export interface FeedbackCall {
  path: string;
  body: Record<string, unknown>;
}

export interface FeedbackMockOptions {
  /** When false, every lookup fails — whatever the reason would have been. */
  lookupSucceeds?: boolean;
  history?: unknown[];
}

function json(route: Route, value: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(value),
  });
}

export async function mockFeedback(
  page: Page,
  options: FeedbackMockOptions = {},
): Promise<FeedbackCall[]> {
  const settings = { lookupSucceeds: true, history: [], ...options };
  const calls: FeedbackCall[] = [];
  const history = [...settings.history];

  await page.route(/\/api\/me\/feedback\/dispositions$/, (route) =>
    json(route, CATALOG),
  );

  await page.route(/\/api\/me\/feedback\/lookup$/, (route) => {
    calls.push({
      path: "/api/me/feedback/lookup",
      body: (route.request().postDataJSON() ?? {}) as Record<string, unknown>,
    });
    // One failure shape for every cause, exactly as the backend answers.
    return settings.lookupSucceeds
      ? json(route, DELIVERED_LEAD)
      : json(route, { detail: "No delivered Lead found." }, 404);
  });

  await page.route(/\/api\/me\/distributions\/\d+\/dispositions$/, (route) =>
    json(route, history),
  );

  await page.route(/\/api\/me\/feedback$/, (route) => {
    const body = (route.request().postDataJSON() ?? {}) as Record<
      string,
      unknown
    >;
    calls.push({ path: "/api/me/feedback", body });
    const transition = {
      id: `t-${history.length + 1}`,
      distributionEventId: 7,
      disposition: body["disposition"],
      note: body["note"] ?? "",
      actorUserId: "u-1",
      previousTransitionId: history.length ? `t-${history.length}` : null,
      createdAt: "2026-07-28T10:00:00Z",
    };
    // Append, never replace — the history endpoint reflects that next read.
    history.push(transition);
    // Mirrors the real nested response, including the echoed controls.
    // `tests/test_feedback_privacy.py::TestSubmissionResponseShape` pins it.
    const disposition = String(body["disposition"]);
    const createsHold =
      disposition === "invalid_phone" || disposition === "do_not_contact";
    const createsReport = createsHold || disposition === "wrong_business";
    return json(
      route,
      {
        distributionEventId: 7,
        currentDisposition: disposition,
        transition,
        qualityRating: body["quality_rating"]
          ? {
              id: `q-${history.length}`,
              kind: body["quality_rating"],
              note: "",
            }
          : null,
        reportId: createsReport ? "r-1" : null,
        eligibilityHoldId: createsHold ? "h-1" : null,
      },
      201,
    );
  });

  return calls;
}
