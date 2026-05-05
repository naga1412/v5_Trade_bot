import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { TabNav } from "@/components/layout/TabNav";
import { useHashRoute } from "@/lib/useHashRoute";

beforeEach(() => {
  window.location.hash = "";
});

describe("TabNav", () => {
  test("renders 4 tabs by default (admin hidden), Scanner + Settings visible", () => {
    const onChange = vi.fn();
    render(<TabNav active="live-prediction" onChange={onChange} />);
    const buttons = screen.getAllByRole("tab");
    expect(buttons).toHaveLength(4);
    expect(buttons[0]).toHaveTextContent(/live prediction/i);
    expect(buttons[1]).toHaveTextContent(/bot status/i);
    expect(buttons[2]).toHaveTextContent(/scanner/i);
    expect(buttons[3]).toHaveTextContent(/settings/i);
    expect(screen.queryByRole("tab", { name: /admin/i })).toBeNull();
  });

  test("renders 5 tabs when adminVisible=true with Admin last", () => {
    const onChange = vi.fn();
    render(
      <TabNav active="live-prediction" onChange={onChange} adminVisible />,
    );
    const buttons = screen.getAllByRole("tab");
    expect(buttons).toHaveLength(5);
    expect(buttons[4]).toHaveTextContent(/admin/i);
  });

  test("renders Scanner tab between Bot Status and Settings", () => {
    const onChange = vi.fn();
    render(<TabNav active="live-prediction" onChange={onChange} adminVisible={false} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual([
      "Live Prediction", "Bot Status", "Scanner", "Settings",
    ]);
  });

  test("Scanner tab is always visible (not admin-gated)", () => {
    render(<TabNav active="scanner" onChange={vi.fn()} adminVisible={false} />);
    expect(screen.getByRole("tab", { name: /scanner/i })).toBeVisible();
  });

  test("clicking a tab calls onChange with the right id", () => {
    const onChange = vi.fn();
    render(<TabNav active="live-prediction" onChange={onChange} adminVisible />);
    fireEvent.click(screen.getByRole("tab", { name: /bot status/i }));
    expect(onChange).toHaveBeenCalledWith("bot-status");

    fireEvent.click(screen.getByRole("tab", { name: /scanner/i }));
    expect(onChange).toHaveBeenCalledWith("scanner");

    fireEvent.click(screen.getByRole("tab", { name: /settings/i }));
    expect(onChange).toHaveBeenCalledWith("settings");

    fireEvent.click(screen.getByRole("tab", { name: /admin/i }));
    expect(onChange).toHaveBeenCalledWith("admin");

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
    render(<TabNav active="live-prediction" onChange={() => {}} adminVisible />);
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

  test("parses '#/settings' as the settings tab", () => {
    window.location.hash = "#/settings";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("settings");
  });

  test("parses '#/admin' as the admin tab", () => {
    window.location.hash = "#/admin";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("admin");
  });

  test("parses '#/scanner' as the scanner tab", () => {
    window.location.hash = "#/scanner";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("scanner");
  });

  test("setTab('scanner') writes the scanner hash", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    act(() => {
      result.current.setTab("scanner");
    });
    expect(result.current.tab).toBe("scanner");
    expect(window.location.hash).toBe("#/scanner");
  });

  test("?signal=X deeplink still works after Scanner addition", () => {
    window.location.hash = "#/live-prediction?signal=abc123";
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    expect(result.current.tab).toBe("live-prediction");
    expect(result.current.query).toEqual({ signal: "abc123" });
  });

  test("setTab('admin') writes the admin hash", () => {
    const { result } = renderHook(() => useHashRoute("live-prediction"));
    act(() => {
      result.current.setTab("admin");
    });
    expect(result.current.tab).toBe("admin");
    expect(window.location.hash).toBe("#/admin");
  });
});
