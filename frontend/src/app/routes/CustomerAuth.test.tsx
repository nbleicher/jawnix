import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { passwordResetLoader, signInLoader } from "../auth/customerAuth";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";

const provider = vi.hoisted(() => ({
  requestProviderPasswordReset: vi.fn(() => Promise.resolve()),
  updateProviderPassword: vi.fn(() => Promise.resolve()),
}));

vi.mock("../auth/providerSession", () => ({
  ...provider,
  signInWithPassword: vi.fn(),
  verifyProviderSignIn: vi.fn(),
  resendVerificationEmail: vi.fn(),
  MFARequired: class extends Error {},
  EmailVerificationRequired: class extends Error {},
}));

const { ResetPasswordRoute, SignInRoute } = await import("./CustomerAuth");

function renderAt(path: string) {
  const router = createMemoryRouter(
    [
      {
        path: "/sign-in",
        loader: signInLoader,
        element: <SignInRoute />,
        HydrateFallback: () => null,
      },
      {
        path: "/reset-password",
        loader: passwordResetLoader,
        element: <ResetPasswordRoute />,
        HydrateFallback: () => null,
      },
    ],
    { initialEntries: [path] },
  );
  render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
  return router;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("sign-in after a password change", () => {
  it("confirms the new password when a reset returns here", async () => {
    renderAt("/sign-in?password=updated");

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Your password was changed. Sign in with your new password.",
    );
  });

  it("says nothing extra on an ordinary visit", async () => {
    renderAt("/sign-in");

    await screen.findByRole("heading", { name: "Sign in" });
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("asking for a password link", () => {
  it("explains why the reset button waits for an email address", async () => {
    const user = userEvent.setup();
    renderAt("/sign-in");

    const reset = await screen.findByRole("button", {
      name: "Set or reset password",
    });
    expect(reset).toBeDisabled();
    expect(reset).toHaveAccessibleDescription(
      "Enter your email address above to get a link for setting a new password.",
    );

    await user.type(
      screen.getByLabelText("Email address (required)"),
      "customer@example.com",
    );
    expect(reset).toBeEnabled();
    expect(
      screen.queryByText(/Enter your email address above/),
    ).toBeNull();

    await user.click(reset);
    expect(provider.requestProviderPasswordReset).toHaveBeenCalledWith(
      "customer@example.com",
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "a link to choose a new password is on its way",
    );
  });
});

describe("the password reset page", () => {
  it("uses reset words, never invitation words", async () => {
    renderAt("/reset-password?token=one-time-token");

    expect(
      await screen.findByRole("heading", { name: "Choose a new password" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save new password" })).toBeVisible();
    expect(document.body).not.toHaveTextContent(/invitation/i);
  });

  it("returns to sign-in with the confirmation after saving", async () => {
    const user = userEvent.setup();
    const router = renderAt("/reset-password?token=one-time-token");

    await user.type(
      await screen.findByLabelText("Password (required)", { exact: true }),
      "new-known-password-48",
    );
    await user.type(
      screen.getByLabelText("Confirm password (required)", { exact: true }),
      "new-known-password-48",
    );
    await user.click(screen.getByRole("button", { name: "Save new password" }));

    expect(provider.updateProviderPassword).toHaveBeenCalledWith(
      "new-known-password-48",
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Your password was changed.",
    );
    expect(router.state.location.search).toBe("?password=updated");
  });

  it.each([
    ["no token", "/reset-password"],
    ["a provider error", "/reset-password?error=INVALID_TOKEN"],
  ])("explains an unusable link with %s", async (_case, path) => {
    renderAt(path);

    const recovery = await screen.findByRole("alert");
    expect(recovery).toHaveTextContent("This password link has expired");
    expect(recovery).toHaveTextContent(
      "Password links work once and expire after an hour.",
    );
    expect(recovery).not.toHaveTextContent(/invitation/i);
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute(
      "href",
      "/sign-in",
    );
  });

  it("explains a link the provider refuses on submit", async () => {
    const user = userEvent.setup();
    provider.updateProviderPassword.mockRejectedValueOnce(new Error("expired"));
    renderAt("/reset-password?token=used-token");

    await user.type(
      await screen.findByLabelText("Password (required)", { exact: true }),
      "new-known-password-48",
    );
    await user.type(
      screen.getByLabelText("Confirm password (required)", { exact: true }),
      "new-known-password-48",
    );
    await user.click(screen.getByRole("button", { name: "Save new password" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This password link has expired",
    );
  });
});
