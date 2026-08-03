import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminMFAStatus } from "../auth/adminMFA";
import { formatCode } from "../auth/adminMFA";
import {
  AdminMFAChallengeRoute,
  AdminMFAEnrollmentRoute,
} from "./AdminMFA";

vi.mock("../auth/providerSession", () => ({
  getProviderSession: vi.fn(async () => ({
    access_token: "provider-aal1-access-token-long-enough",
    refresh_token: "provider-refresh-token-long-enough",
  })),
  storeProviderSession: vi.fn(async () => undefined),
}));

const primary = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Jawnix primary",
  status: "verified",
  type: "totp",
  createdAt: "2026-07-28T12:00:00Z",
  lastUsedAt: "2026-07-28T12:30:00Z",
  lastUsedFrom: null,
};

function status(overrides: Partial<AdminMFAStatus> = {}): AdminMFAStatus {
  return {
    assurance: "aal1",
    enforced: true,
    stage: "complete",
    factors: [
      primary,
      {
        ...primary,
        id: "22222222-2222-4222-8222-222222222222",
        name: "Jawnix backup",
      },
    ],
    throttled: false,
    lockedUntil: null,
    next: "/app/admin/mfa/challenge",
    ...overrides,
  };
}

function renderRoute(
  element: React.ReactNode,
  data: AdminMFAStatus,
) {
  const router = createMemoryRouter(
    [
      {
        id: "mfa-test",
        path: "/admin/mfa/test",
        element,
        loader: () => data,
      },
      { path: "/admin/overview", element: <h1>Operations overview</h1> },
    ],
    {
      initialEntries: ["/admin/mfa/test"],
      hydrationData: { loaderData: { "mfa-test": data } },
    },
  );
  render(<RouterProvider router={router} />);
}

describe("administrator MFA", () => {
  beforeEach(() => {
    sessionStorage.clear();
    document.cookie = "jawnix_csrf=test-csrf";
    vi.restoreAllMocks();
  });

  it("normalizes pasted and formatted authenticator codes", () => {
    expect(formatCode(" 12 34-56 ")).toBe("123456");
    expect(formatCode("123456789")).toBe("123456");
  });

  it("preselects the primary and reveals the backup on request", async () => {
    const user = userEvent.setup();
    renderRoute(<AdminMFAChallengeRoute />, status());

    expect(screen.getByText("Jawnix primary", { exact: true })).toBeVisible();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.queryByText("Jawnix backup", { exact: true })).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "I cannot use my primary authenticator",
      }),
    );
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(radios[0]).toBeChecked();
    expect(screen.getByText("Jawnix backup", { exact: true })).toBeVisible();
  });

  it("treats a replacement as primary when the named primary is absent", () => {
    renderRoute(
      <AdminMFAChallengeRoute />,
      status({
        factors: [
          { ...primary, name: "Jawnix backup" },
          {
            ...primary,
            id: "33333333-3333-4333-8333-333333333333",
            name: "Jawnix replacement",
          },
        ],
      }),
    );

    expect(screen.getByText("Jawnix replacement", { exact: true })).toBeVisible();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.queryByText("Jawnix backup", { exact: true })).not.toBeInTheDocument();
  });

  it("associates a non-revealing challenge error with the code field", async () => {
    const user = userEvent.setup();
    renderRoute(<AdminMFAChallengeRoute />, status());

    const input = await screen.findByLabelText(
      "Six-digit authenticator code (required)",
    );
    await user.type(input, "12 34");
    await user.click(
      screen.getByRole("button", { name: "Verify and continue" }),
    );

    expect(
      screen.getByRole("alert"),
    ).toHaveTextContent(
      "Choose an authenticator and enter its six-digit code.",
    );
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("renders enrollment secrets only after the explicit setup action", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          factorId: "33333333-3333-4333-8333-333333333333",
          slot: "primary",
          qrCode: "<svg>qr</svg>",
          manualKey: "MANUAL-KEY",
          uri: "otpauth://totp/Jawnix",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderRoute(
      <AdminMFAEnrollmentRoute />,
      status({
        enforced: false,
        stage: "idle",
        factors: [],
        next: "/app/admin/mfa/enroll",
      }),
    );

    expect(screen.queryByText("MANUAL-KEY")).not.toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", {
        name: "Set up primary authenticator",
      }),
    );

    expect(await screen.findByText("MANUAL-KEY")).toBeVisible();
    expect(
      screen.getByAltText("QR code for the primary authenticator"),
    ).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0]?.[1]?.body).not.toContain("MANUAL-KEY");
  });
});
