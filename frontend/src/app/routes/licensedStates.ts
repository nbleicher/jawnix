import { redirect } from "react-router";
import type { LoaderFunctionArgs } from "react-router";

import type { CustomerOverviewData } from "../auth/customerAuth";
import type { BatchRequestWorkspace } from "./batchRequests";

export interface LicensedStateOption {
  code: string;
  name: string;
}

export interface LicensedStateWorkspace {
  states: string[];
  options: LicensedStateOption[];
  version: string;
}

export interface CustomerAccountIdentity {
  user_id: string;
  email: string;
  first_name: string;
  last_name: string;
  phone: string;
  customer_id: number | null;
  mapping_confirmed_at: string | null;
}

export interface CustomerAccountWorkspace {
  identity: CustomerAccountIdentity;
  licensed_states: LicensedStateWorkspace;
}

export interface LicensedStateImpact {
  request_id: string;
  lead_count: number;
  status: string;
  current_states: string[];
  resulting_states: string[];
  action: "narrowed" | "canceled";
}

export interface LicensedStateReview {
  current_states: string[];
  proposed_states: string[];
  added_states: string[];
  removed_states: string[];
  additions_apply_to_future_requests_only: true;
  impacts: LicensedStateImpact[];
  review_token: string;
}

export interface LicensedStateApplyResult {
  account: LicensedStateWorkspace;
  overview: CustomerOverviewData;
  requests: BatchRequestWorkspace;
}

interface ErrorBody {
  detail?: string | { msg?: string }[];
}

export class LicensedStateRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export class ProfileUpdateError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function csrf(): string {
  const item = document.cookie
    .split(";")
    .map((value) => value.trim())
    .find((value) => value.startsWith("jawnix_csrf="));
  return decodeURIComponent(item?.split("=", 2)[1] ?? "");
}

function refusal(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === "string") return body.detail;
  const first = Array.isArray(body.detail) ? body.detail[0]?.msg : undefined;
  return first ?? fallback;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const parsed = (await response.json().catch(() => ({}))) as ErrorBody;
  if (!response.ok) {
    throw new LicensedStateRequestError(
      refusal(
        parsed,
        "Licensed States could not be updated. Please try again.",
      ),
      response.status,
    );
  }
  return parsed as T;
}

/** Update Customer identity fields. Licensed States stay on the review path. */
export async function updateCustomerProfile(input: {
  first_name: string;
  last_name: string;
  phone: string;
  licensed_states: string[];
}): Promise<CustomerAccountIdentity> {
  const response = await fetch("/api/me/profile", {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf(),
    },
    credentials: "same-origin",
    body: JSON.stringify(input),
  });
  const parsed = (await response.json().catch(() => ({}))) as ErrorBody &
    CustomerAccountIdentity;
  if (!response.ok) {
    throw new ProfileUpdateError(
      refusal(parsed, "Profile could not be updated. Please try again."),
      response.status,
    );
  }
  return parsed;
}

export async function customerAccountLoader({
  request,
}: LoaderFunctionArgs): Promise<CustomerAccountWorkspace> {
  const [identityResponse, licensedStatesResponse] = await Promise.all([
    fetch("/api/me/profile", { credentials: "same-origin" }),
    fetch("/api/me/licensed-states", { credentials: "same-origin" }),
  ]);
  if (
    identityResponse.status === 401
    || identityResponse.status === 403
    || licensedStatesResponse.status === 401
    || licensedStatesResponse.status === 403
  ) {
    const next = new URL(request.url).pathname;
    throw redirect(`/sign-in?next=${encodeURIComponent(next)}`);
  }
  if (!identityResponse.ok || !licensedStatesResponse.ok) {
    throw new Response("Account is temporarily unavailable.", {
      status: !identityResponse.ok
        ? identityResponse.status
        : licensedStatesResponse.status,
    });
  }
  const [identity, licensedStates] = await Promise.all([
    identityResponse.json() as Promise<CustomerAccountIdentity>,
    licensedStatesResponse.json() as Promise<LicensedStateWorkspace>,
  ]);
  return { identity, licensed_states: licensedStates };
}

export function previewLicensedStates(
  states: string[],
  expectedVersion: string,
): Promise<LicensedStateReview> {
  return post<LicensedStateReview>("/api/me/licensed-states/preview", {
    states,
    expected_version: expectedVersion,
  });
}

export function applyLicensedStateReview(
  reviewToken: string,
): Promise<LicensedStateApplyResult> {
  return post<LicensedStateApplyResult>("/api/me/licensed-states/apply", {
    review_token: reviewToken,
  });
}
