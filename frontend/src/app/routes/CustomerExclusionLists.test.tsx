import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CustomerExclusionListsSection } from "./CustomerExclusionLists";
import {
  listMyExclusionLists,
  uploadMyExclusionList,
  ExclusionListRequestError,
} from "./exclusionLists";
import type { ExclusionListStatus } from "./exclusionLists";

vi.mock("./exclusionLists", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./exclusionLists")>()),
  listMyExclusionLists: vi.fn(),
  uploadMyExclusionList: vi.fn(),
}));

function status(
  overrides: Partial<ExclusionListStatus> = {},
): ExclusionListStatus {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    type: "dnc",
    filename: "dnc.csv",
    status: "pending_confirmation",
    totalRows: 1_200,
    acceptedRows: 1_150,
    invalidRows: 30,
    duplicateRows: 20,
    poolImpact: 4,
    global: false,
    error: "",
    createdAt: "2026-08-04T00:00:00Z",
    ingestedAt: "2026-08-04T00:01:00Z",
    decidedAt: null,
    ...overrides,
  };
}

describe("Customer Exclusion Lists", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows each upload as protection regardless of the admin decision", async () => {
    vi.mocked(listMyExclusionLists).mockResolvedValue([
      status(),
      status({
        id: "22222222-2222-4222-8222-222222222222",
        filename: "denied.csv",
        status: "denied",
      }),
      status({
        id: "33333333-3333-4333-8333-333333333333",
        filename: "broken.csv",
        status: "failed",
        error: "The CSV holds more than 50,000 rows.",
        ingestedAt: null,
      }),
    ]);

    render(<CustomerExclusionListsSection />);

    // A denied list still protects the uploader (permanent uploader scope),
    // so both undecided and denied read identically here.
    expect(
      await screen.findAllByText("Protecting your batches"),
    ).toHaveLength(2);
    expect(screen.getByText("Failed")).toBeVisible();
    expect(
      screen.getByText("The CSV holds more than 50,000 rows."),
    ).toBeVisible();
    expect(screen.getAllByText("1,150", { exact: false })[0]).toBeVisible();
  });

  it("uploads a typed CSV and prepends the new list", async () => {
    const user = userEvent.setup();
    vi.mocked(listMyExclusionLists).mockResolvedValue([]);
    vi.mocked(uploadMyExclusionList).mockResolvedValue(
      status({ filename: "landlines.csv", type: "landline", status: "queued" }),
    );

    render(<CustomerExclusionListsSection />);
    expect(await screen.findByText("No Exclusion Lists yet")).toBeVisible();

    await user.selectOptions(screen.getByLabelText(/Type/), "landline");
    const file = new File(["phone\n2155550000\n"], "landlines.csv", {
      type: "text/csv",
    });
    await user.upload(screen.getByLabelText(/CSV file/), file);
    await user.click(screen.getByRole("button", { name: "Upload list" }));

    await waitFor(() =>
      expect(uploadMyExclusionList).toHaveBeenCalledWith(file, "landline"),
    );
    expect(await screen.findByText("landlines.csv")).toBeVisible();
    expect(screen.getByText("Queued")).toBeVisible();
  });

  it("defaults the type to mixed, since real files span all three reasons", async () => {
    vi.mocked(listMyExclusionLists).mockResolvedValue([]);

    render(<CustomerExclusionListsSection />);
    await screen.findByText("No Exclusion Lists yet");

    expect(screen.getByLabelText(/Type/)).toHaveValue("mixed");
  });

  it("refuses to submit without a file", async () => {
    const user = userEvent.setup();
    vi.mocked(listMyExclusionLists).mockResolvedValue([]);

    render(<CustomerExclusionListsSection />);
    await screen.findByText("No Exclusion Lists yet");
    await user.click(screen.getByRole("button", { name: "Upload list" }));

    expect(
      await screen.findByText("Choose a CSV file to upload."),
    ).toBeVisible();
    expect(uploadMyExclusionList).not.toHaveBeenCalled();
  });

  it("surfaces the backend refusal on a rejected upload", async () => {
    const user = userEvent.setup();
    vi.mocked(listMyExclusionLists).mockResolvedValue([]);
    vi.mocked(uploadMyExclusionList).mockRejectedValue(
      new ExclusionListRequestError("Upload a CSV file.", 422),
    );

    render(<CustomerExclusionListsSection />);
    await screen.findByText("No Exclusion Lists yet");
    await user.upload(
      screen.getByLabelText(/CSV file/),
      new File(["x"], "phones.csv", { type: "text/csv" }),
    );
    await user.click(screen.getByRole("button", { name: "Upload list" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Upload a CSV file.",
    );
  });

  it("offers a retry when the list cannot be loaded", async () => {
    const user = userEvent.setup();
    vi.mocked(listMyExclusionLists)
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([status()]);

    render(<CustomerExclusionListsSection />);
    expect(
      await screen.findByText(
        "Your Exclusion Lists could not be loaded right now.",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("dnc.csv")).toBeVisible();
  });
});
