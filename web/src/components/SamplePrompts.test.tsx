import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SamplePrompts } from "./SamplePrompts";

describe("SamplePrompts", () => {
  it("renders dispatcher prompts that highlight LF enforcement", () => {
    render(<SamplePrompts role="dispatcher" onSubmit={() => {}} />);
    expect(
      screen.getByRole("button", { name: /show me a list of customers/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/dispatcher's tag policy denies/i))
      .toBeInTheDocument();
  });

  it("renders in sidebar layout by default (no centering wrapper)", () => {
    render(<SamplePrompts role="owner" onSubmit={() => {}} />);
    const wrapper = screen.getByTestId("sample-prompts");
    expect(wrapper.className).not.toContain("items-center");
    expect(wrapper.className).toContain("overflow-y-auto");
  });

  it("renders in centered layout when layout='centered'", () => {
    render(
      <SamplePrompts role="owner" onSubmit={() => {}} layout="centered" />,
    );
    const wrapper = screen.getByTestId("sample-prompts");
    expect(wrapper.className).toContain("items-center");
    expect(wrapper.className).toContain("justify-center");
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
