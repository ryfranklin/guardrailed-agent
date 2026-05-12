import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SamplePrompts } from "./SamplePrompts";

describe("SamplePrompts", () => {
  it("renders dispatcher prompts that highlight LF enforcement", () => {
    render(<SamplePrompts role="dispatcher" onSubmit={() => {}} />);
    expect(
      screen.getByRole("button", { name: /show me a list of customers/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Lake Formation denies the PII columns/i))
      .toBeInTheDocument();
  });

  it("renders technician_lead prompts that reference the service region", () => {
    render(<SamplePrompts role="technician_lead" onSubmit={() => {}} />);
    expect(
      screen.getByRole("button", { name: /customers in my service region/i }),
    ).toBeInTheDocument();
  });

  it("renders owner prompts that exercise owner-only fields", () => {
    render(<SamplePrompts role="owner" onSubmit={() => {}} />);
    expect(
      screen.getByRole("button", { name: /revenue by service region/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /including deleted ones/i }),
    ).toBeInTheDocument();
  });

  it("invokes onSubmit with the prompt text when a prompt is clicked", () => {
    const onSubmit = vi.fn();
    render(<SamplePrompts role="dispatcher" onSubmit={onSubmit} />);
    fireEvent.click(
      screen.getByRole("button", { name: /show me a list of customers/i }),
    );
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("Show me a list of customers");
  });

  it("disables all prompts when disabled", () => {
    const onSubmit = vi.fn();
    render(
      <SamplePrompts role="owner" onSubmit={onSubmit} disabled />,
    );
    const buttons = screen.getAllByTestId("sample-prompt");
    expect(buttons).toHaveLength(3);
    buttons.forEach((b) => expect(b).toBeDisabled());
    fireEvent.click(buttons[0]);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
