import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import HomePage from "./page";

describe("HomePage", () => {
  it("renders the product name", () => {
    render(<HomePage />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "AI Product Video Studio",
      }),
    ).toBeInTheDocument();
  });

  it("leads with the two primary user tasks required by taskbook §102", () => {
    render(<HomePage />);
    expect(
      screen.getByRole("heading", { level: 2, name: "Upload Product" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "Generate Video" }),
    ).toBeInTheDocument();
  });
});
