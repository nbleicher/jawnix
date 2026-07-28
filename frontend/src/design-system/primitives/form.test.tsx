import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Field, Input, Select, Textarea } from "./form";

describe("Field", () => {
  it("associates its label with the control", () => {
    render(
      <Field label="Delivery email">
        <Input name="email" />
      </Field>,
    );

    expect(screen.getByLabelText("Delivery email")).toBe(screen.getByRole("textbox"));
  });

  it("exposes the description to assistive technology", () => {
    render(
      <Field label="Quantity" description="Between 1 and 500 leads.">
        <Input name="quantity" />
      </Field>,
    );

    expect(screen.getByRole("textbox")).toHaveAccessibleDescription("Between 1 and 500 leads.");
  });

  it("marks the control invalid and announces the error when one is present", () => {
    render(
      <Field label="Quantity" error="Enter a quantity of 500 or fewer.">
        <Input name="quantity" />
      </Field>,
    );

    const control = screen.getByRole("textbox");
    expect(control).toHaveAttribute("aria-invalid", "true");
    expect(control).toHaveAccessibleDescription(/Enter a quantity of 500 or fewer\./);
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a quantity of 500 or fewer.");
  });

  it("keeps both the description and the error in the accessible description", () => {
    render(
      <Field label="Quantity" description="Between 1 and 500 leads." error="Too many.">
        <Input name="quantity" />
      </Field>,
    );

    expect(screen.getByRole("textbox")).toHaveAccessibleDescription("Between 1 and 500 leads. Too many.");
  });

  it("marks required fields for assistive technology and sighted users alike", () => {
    render(
      <Field label="Delivery email" required>
        <Input name="email" />
      </Field>,
    );

    const control = screen.getByRole("textbox");
    expect(control).toBeRequired();
    expect(screen.getByText("(required)")).toBeInTheDocument();
  });

  it("does not mark a valid field invalid", () => {
    render(
      <Field label="Delivery email">
        <Input name="email" />
      </Field>,
    );

    expect(screen.getByRole("textbox")).not.toHaveAttribute("aria-invalid");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("wires the same contract for Select and Textarea", () => {
    render(
      <>
        <Field label="State" error="Pick a licensed state.">
          <Select name="state">
            <option value="">Choose…</option>
          </Select>
        </Field>
        <Field label="Note" description="Explain the disposition.">
          <Textarea name="note" />
        </Field>
      </>,
    );

    expect(screen.getByLabelText("State")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Note")).toHaveAccessibleDescription("Explain the disposition.");
  });

  it("respects a caller-supplied id instead of generating one", () => {
    render(
      <Field label="Quantity" id="batch-quantity">
        <Input name="quantity" />
      </Field>,
    );

    expect(screen.getByRole("textbox")).toHaveAttribute("id", "batch-quantity");
  });
});
