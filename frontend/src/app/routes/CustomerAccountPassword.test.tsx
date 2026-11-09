import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  PasswordChangeError,
  changeProviderPassword,
  requestProviderPasswordReset,
} from "../auth/providerSession";
import { CustomerAccountPassword } from "./CustomerAccountPassword";

vi.mock("../auth/providerSession", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../auth/providerSession")>()),
  changeProviderPassword: vi.fn(),
  requestProviderPasswordReset: vi.fn(),
}));

async function fill(current: string, next: string, confirm = next) {
  const user = userEvent.setup();
  render(<CustomerAccountPassword email="river@northstar.example" />);
  if (current) await user.type(screen.getByLabelText("Current password"), current);
  if (next) await user.type(screen.getByLabelText("New password"), next);
  if (confirm) await user.type(screen.getByLabelText("Confirm new password"), confirm);
  await user.click(screen.getByRole("button", { name: "Change password" }));
  return user;
}

describe("Account Password tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("changes the password at the provider and clears the form", async () => {
    vi.mocked(changeProviderPassword).mockResolvedValue();

    await fill("old-password", "new-password-1");

    expect(changeProviderPassword).toHaveBeenCalledWith("old-password", "new-password-1");
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed.");
    expect(screen.getByLabelText("Current password")).toHaveValue("");
  });

  it("checks length and the confirmation before asking the provider", async () => {
    const user = await fill("old-password", "short", "short");
    expect(screen.getByText("Use at least 8 characters.")).toBeVisible();

    await user.clear(screen.getByLabelText("New password"));
    await user.type(screen.getByLabelText("New password"), "new-password-1");
    await user.clear(screen.getByLabelText("Confirm new password"));
    await user.type(screen.getByLabelText("Confirm new password"), "new-password-2");
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(screen.getByText("The two new passwords do not match.")).toBeVisible();
    expect(changeProviderPassword).not.toHaveBeenCalled();
  });

  it("puts a wrong current password on its field", async () => {
    vi.mocked(changeProviderPassword).mockRejectedValue(
      new PasswordChangeError("wrong_password", "Your current password is not correct."),
    );

    await fill("not-it", "new-password-1");

    expect(await screen.findByText("Your current password is not correct.")).toBeVisible();
    expect(screen.getByLabelText("Current password")).toHaveAttribute("aria-invalid", "true");
  });

  it("points an old sign-in to a reset link, and sends one", async () => {
    vi.mocked(changeProviderPassword).mockRejectedValue(
      new PasswordChangeError("session", "Your sign-in has expired for password changes."),
    );
    vi.mocked(requestProviderPasswordReset).mockResolvedValue();

    const user = await fill("old-password", "new-password-1");

    expect(await screen.findByRole("alert")).toHaveTextContent("Use a reset link instead.");
    await user.click(screen.getByRole("button", { name: "Email me a reset link" }));
    expect(requestProviderPasswordReset).toHaveBeenCalledWith("river@northstar.example");
    expect(await screen.findByText("We sent a link to river@northstar.example. It expires in one hour.")).toBeVisible();
  });
});
