import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { TabNav } from "@/components/layout/TabNav";
import { useHashRoute } from "@/lib/useHashRoute";

beforeEach(() => {
  window.location.hash = "";
});

describe("TabNav", () => {
  test("renders both tab labels in correct order", () => {
    const onChange = vi.fn();
    render(<TabNav active="live-prediction" onChange={onChange} />);
    const buttons = screen.getAllByRole("tab");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveTextContent(/live prediction/i);
    expect(buttons[1]).toHaveTextContent(/bot status/i);
  });

  test("clicking a tab calls onChange with the right id", () => {
    const onChange = vi.fn();
    render(<TabNav active="live-prediction" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: /bot status/i }));
    expect(onChange).toHaveBeenCalledWith("bot-status");

    fireEvent.click(screen.getByRole("tab", { name: /live prediction/i }));
    expect(onChange).toHaveBeenCalledWith("live-prediction");
  });

  test("active tab carries data-active='true'; inactive tabs do not", () => {
    render(<TabNav active="bot-status" onChange={() => {}} />);
    const live = screen.getByRole("tab", { name: /live prediction/i });
    const bot = screen.getByRole("tab", { name: /bot status/i });
    expect(bot).toHaveAttribute("data-active", "true");
    expect(live).toHaveAttribute("data-active", "false");
    expect(bot).toHaveAttribute("aria-selected", "true");
    expect(live).toHaveAttribute("aria-selected", "false");
  });

  test("tab buttons meet 44px touch target (h-11)", () => {
    render(<TabNav active="live-prediction" onChange={() => {}} />);
    for (const btn of screen.getAllByRole("tab")) {
      expect(btn.className).toMatch(/\bh-11\b/);
    }
  });
});

describe("useHashRoute", () => {
  test("falls back to default tab when hash is empty", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("live-prediction");
  });

  test("parses initial hash like '#/bot-status'", () => {
    window.location.hash = "#/bot-status";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("bot-status");
  });

  test("falls back to default when hash is invalid", () => {
    window.location.hash = "#/garbage";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("live-prediction");
  });

  test("setTab updates state and writes hash", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    act(() => {
      result.current.setTab("bot-status");
    });
    expect(result.current.tab).toBe("bot-status");
    expect(window.location.hash).toBe("#/bot-status");
  });

  test("responds to external hashchange events", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("live-prediction");
    act(() => {
      window.location.hash = "#/bot-status";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current.tab).toBe("bot-status");
  });

  test("query is empty object when hash has no query string", () => {
    window.location.hash = "#/live-prediction";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.query).toEqual({});
  });

  test("parses single query param after '?'", () => {
    window.location.hash = "#/live-prediction?signal=abc123";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("live-prediction");
    expect(result.current.query).toEqual({ signal: "abc123" });
  });

  test("parses multiple query params", () => {
    window.location.hash = "#/live-prediction?signal=xyz&foo=bar";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.query).toEqual({ signal: "xyz", foo: "bar" });
  });

  test("URL-decodes special characters in query values", () => {
    window.location.hash = "#/live-prediction?signal=" + encodeURIComponent("a/b c");
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.query.signal).toBe("a/b c");
  });

  test("query updates on external hashchange", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.query).toEqual({});
    act(() => {
      window.location.hash = "#/live-prediction?signal=newid";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current.query).toEqual({ signal: "newid" });
  });
});
