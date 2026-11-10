import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ErrorPage } from "@/components/soc/ErrorPage";

describe("ErrorPage", () => {
  it("shows an error message as literal text, never as HTML", () => {
    const message = "<img src=x onerror=alert(1)>";

    const { container } = render(<ErrorPage error={new Error(message)} onRetry={() => {}} />);

    expect(screen.getByText(message)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the plain error page for a thrown value that is not an Error", () => {
    render(<ErrorPage error="<b>boom</b>" onRetry={() => {}} />);

    expect(screen.getByText("<b>boom</b>")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "This page didn't load" })).toBeInTheDocument();
  });
});
