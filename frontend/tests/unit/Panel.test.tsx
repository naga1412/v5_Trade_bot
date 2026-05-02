import { render, screen } from "@testing-library/react";
import { Panel } from "@/components/ui/Panel";

test("renders title and children", () => {
  render(<Panel title="Trade Setup">Body content</Panel>);
  expect(screen.queryByText("TRADE SETUP".toLowerCase()) ?? screen.getByText(/trade setup/i)).toBeTruthy();
  expect(screen.getByText("Body content")).toBeInTheDocument();
});

test("uses alert border when intensity=alert", () => {
  const { container } = render(
    <Panel title="alert panel" intensity="alert">x</Panel>
  );
  expect(container.firstChild).toHaveClass("border-red/60");
});
