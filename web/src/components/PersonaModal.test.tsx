import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PersonaModal } from "./PersonaModal";

describe("PersonaModal", () => {
  it("blocks confirmation until a persona is picked", () => {
    const onConfirm = vi.fn();
    render(<PersonaModal onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("persona-modal-confirm"));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("persona-modal-error")).toHaveTextContent(
      /pick a persona/i,
    );
  });

  it("confirms dispatcher with no service region", () => {
    const onConfirm = vi.fn();
    render(<PersonaModal onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("persona-option-dispatcher"));
    fireEvent.click(screen.getByTestId("persona-modal-confirm"));

    expect(onConfirm).toHaveBeenCalledWith("dispatcher", null);
  });

  it("confirms owner with no service region", () => {
    const onConfirm = vi.fn();
    render(<PersonaModal onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("persona-option-owner"));
    fireEvent.click(screen.getByTestId("persona-modal-confirm"));

    expect(onConfirm).toHaveBeenCalledWith("owner", null);
  });

  it("requires a non-empty service region for technician_lead", () => {
    const onConfirm = vi.fn();
    render(<PersonaModal onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("persona-option-technician_lead"));
    const regionInput = screen.getByTestId(
      "service-region-input",
    ) as HTMLInputElement;
    fireEvent.change(regionInput, { target: { value: "  " } });
    fireEvent.click(screen.getByTestId("persona-modal-confirm"));

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByTestId("persona-modal-error")).toHaveTextContent(
      /service region/i,
    );
  });

  it("confirms technician_lead with a trimmed region", () => {
    const onConfirm = vi.fn();
    render(<PersonaModal onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("persona-option-technician_lead"));
    const regionInput = screen.getByTestId(
      "service-region-input",
    ) as HTMLInputElement;
    fireEvent.change(regionInput, { target: { value: "  north-phoenix " } });
    fireEvent.click(screen.getByTestId("persona-modal-confirm"));

    expect(onConfirm).toHaveBeenCalledWith("technician_lead", "north-phoenix");
  });

  it("renders cancel button only when cancellable is true", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(<PersonaModal onConfirm={onConfirm} />);
    expect(screen.queryByTestId("persona-modal-cancel")).toBeNull();

    rerender(
      <PersonaModal
        onConfirm={onConfirm}
        onCancel={onCancel}
        cancellable
        defaultRole="owner"
      />,
    );
    fireEvent.click(screen.getByTestId("persona-modal-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("respects defaultRole on first render", () => {
    const onConfirm = vi.fn();
    render(
      <PersonaModal defaultRole="owner" onConfirm={onConfirm} />,
    );
    const ownerInput = screen.getByTestId(
      "persona-option-owner",
    ) as HTMLInputElement;
    expect(ownerInput.checked).toBe(true);
  });
});
