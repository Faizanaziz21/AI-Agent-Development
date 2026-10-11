import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorBox, Modal, StatusBadge, TagInput } from "./ui";

describe("ui primitives", () => {
  it("renders human-readable status labels", () => {
    render(<StatusBadge status="WAITING_APPROVAL" />);
    expect(screen.getByText("Waiting Approval")).toBeTruthy();
  });

  it("shows nothing for an empty error and the message otherwise", () => {
    const { container, rerender } = render(<ErrorBox error={null} />);
    expect(container.innerHTML).toBe("");
    rerender(<ErrorBox error={new Error("budget exceeded")} />);
    expect(screen.getByRole("alert").textContent).toContain("budget exceeded");
  });

  it("closes a modal on Escape", () => {
    const onClose = vi.fn();
    render(<Modal open onClose={onClose} title="Edit">body</Modal>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("removes tags", () => {
    const onChange = vi.fn();
    render(<TagInput value={["web_search", "crm_lookup"]} onChange={onChange} options={["web_search", "crm_lookup", "email_send"]} />);
    fireEvent.click(screen.getByLabelText("Remove web_search"));
    expect(onChange).toHaveBeenCalledWith(["crm_lookup"]);
  });
});
