import { describe, expect, it } from "vitest";
import type { LoaderFunctionArgs } from "react-router";

import {
  feedbackRedirectLoader,
  isBatchReference,
  requestsRedirectLoader,
} from "./batchRequests";

const ID = "10171017-1017-4017-8017-101710171017";

function args(path: string): LoaderFunctionArgs {
  return {
    request: new Request(`https://jawnix.test/app${path}`),
    params: {},
  } as LoaderFunctionArgs;
}

function location(response: Response): string | null {
  expect(response.status).toBe(302);
  return response.headers.get("Location");
}

describe("old /requests links", () => {
  it("open the batch an emailed link names", () => {
    expect(location(requestsRedirectLoader(args(`/requests?request=${ID}`)))).toBe(`/batches/${ID}`);
    expect(location(requestsRedirectLoader(args("/requests?request=not-a-batch")))).toBe("/batches");
  });

  it("open the same page of Batches", () => {
    expect(location(requestsRedirectLoader(args("/requests?page=3")))).toBe("/batches?page=3");
  });

  it("carry an unfinished request and a Stripe outcome to New request", () => {
    expect(
      location(
        requestsRedirectLoader(
          args("/requests?quantity=750&states=FL,TX&rows_per_file=300&purchase=success&next=x"),
        ),
      ),
    ).toBe("/new-request?quantity=750&states=FL,TX&rows_per_file=300&purchase=success");
    expect(location(requestsRedirectLoader(args("/requests")))).toBe("/new-request");
  });
});

describe("old /feedback links", () => {
  it("open the batch they were scoped to, or Batches", () => {
    expect(location(feedbackRedirectLoader(args(`/feedback?request=${ID}`)))).toBe(`/batches/${ID}`);
    expect(location(feedbackRedirectLoader(args("/feedback")))).toBe("/batches");
  });
});

describe("batch references", () => {
  it("are a batch number or a UUID", () => {
    expect(isBatchReference("1042")).toBe(true);
    expect(isBatchReference(ID)).toBe(true);
    expect(isBatchReference("1234567890")).toBe(false);
    expect(isBatchReference("events")).toBe(false);
    expect(isBatchReference(undefined)).toBe(false);
  });
});
