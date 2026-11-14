import { describe, expect, it } from "vitest";
import type { ShouldRevalidateFunctionArgs } from "react-router";

import { customerShouldRevalidate } from "./home";

/**
 * The shell loader doubles as Home's one read, so it must read again when
 * Home is arrived at — and only then. Moving between the other Customer
 * pages must not reload the full Home payload.
 */
function args(
  from: string,
  to: string,
  defaultShouldRevalidate = false,
): ShouldRevalidateFunctionArgs {
  return {
    currentUrl: new URL(`https://jawnix.test/app${from}`),
    nextUrl: new URL(`https://jawnix.test/app${to}`),
    defaultShouldRevalidate,
  } as unknown as ShouldRevalidateFunctionArgs;
}

describe("customerShouldRevalidate", () => {
  it("does not read Home again when moving between the other pages", () => {
    expect(customerShouldRevalidate(args("/home", "/batches"))).toBe(false);
    expect(customerShouldRevalidate(args("/batches", "/account"))).toBe(false);
    expect(customerShouldRevalidate(args("/account", "/new-request"))).toBe(false);
    expect(customerShouldRevalidate(args("/batches", "/batches/1042"))).toBe(false);
  });

  it("reads again on arrival at Home, which renders the shell's data", () => {
    expect(customerShouldRevalidate(args("/batches", "/home"))).toBe(true);
    expect(customerShouldRevalidate(args("/account", "/home"))).toBe(true);
    expect(customerShouldRevalidate(args("/batches/1042", "/home"))).toBe(true);
  });

  it("keeps the router's own reasons to read again", () => {
    expect(customerShouldRevalidate(args("/account", "/account", true))).toBe(true);
    expect(customerShouldRevalidate(args("/home", "/home", true))).toBe(true);
  });

  it("does not read again for a same-page move", () => {
    expect(customerShouldRevalidate(args("/home", "/home"))).toBe(false);
  });
});
