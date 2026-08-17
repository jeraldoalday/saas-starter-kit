import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Input } from "@/components/ui/Input";

describe("Input", () => {
  it("associates the label with the control via id", () => {
    render(<Input id="email" label="Email" />);

    // getByLabelText only resolves if htmlFor/id actually line up, so this
    // asserts the a11y wiring rather than just that a label rendered.
    expect(screen.getByLabelText("Email")).toBe(screen.getByRole("textbox"));
  });

  it("renders without a label", () => {
    render(<Input id="bare" />);

    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(screen.queryByRole("label")).not.toBeInTheDocument();
  });

  it("shows an error message when one is given", () => {
    render(<Input id="email" label="Email" error="Email is required" />);

    expect(screen.getByText("Email is required")).toBeInTheDocument();
  });

  it("omits the error element when there is no error", () => {
    render(<Input id="email" label="Email" />);

    expect(screen.queryByText(/required/)).not.toBeInTheDocument();
  });

  it("forwards arbitrary input attributes", () => {
    render(<Input id="password" type="password" required minLength={8} />);

    const input = document.getElementById("password");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toBeRequired();
    expect(input).toHaveAttribute("minlength", "8");
  });

  it("forwards its ref to the underlying input", () => {
    const ref = createRef<HTMLInputElement>();

    render(<Input id="email" ref={ref} />);

    expect(ref.current).toBe(screen.getByRole("textbox"));
  });
});
