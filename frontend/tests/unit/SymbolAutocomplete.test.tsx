/**
 * SP-9 Phase A — SymbolAutocomplete tests.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { SymbolAutocomplete } from "@/components/layout/SymbolAutocomplete";
import * as apiMod from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// Real timers — DEBOUNCE_MS is 180ms in the component; waitFor's
// default 1s window is plenty.
async function flushDebounce() {
  await new Promise((r) => setTimeout(r, 250));
}

describe("SymbolAutocomplete", () => {
  test("debounced typing fires symbolSearch and shows hits", async () => {
    const spy = vi.spyOn(apiMod.api, "symbolSearch").mockResolvedValue({
      query: "shib",
      hits: [
        { symbol: "SHIB/USDT", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: 12_345_678 },
        { symbol: "1000SHIB/USDT", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: 9_876 },
      ],
    });
    const onSelect = vi.fn();
    render(
      <SymbolAutocomplete initialValue="BTC/USDT" onSelect={onSelect} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "shib" } });
    await flushDebounce();
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith("SHIB", { limit: 10 });
    });
    // Hits render in the listbox.
    expect(await screen.findByText("SHIB/USDT")).toBeInTheDocument();
    expect(screen.getByText("1000SHIB/USDT")).toBeInTheDocument();
  });

  test("clicking a suggestion commits via onSelect", async () => {
    vi.spyOn(apiMod.api, "symbolSearch").mockResolvedValue({
      query: "shib",
      hits: [
        { symbol: "SHIB/USDT", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: null },
      ],
    });
    const onSelect = vi.fn();
    render(
      <SymbolAutocomplete initialValue="BTC/USDT" onSelect={onSelect} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "shib" } });
    await flushDebounce();
    const opt = await screen.findByRole("option", { name: /shib\/usdt/i });
    fireEvent.mouseDown(opt);
    expect(onSelect).toHaveBeenCalledWith("SHIB/USDT");
  });

  test("ArrowDown + Enter selects the highlighted suggestion", async () => {
    vi.spyOn(apiMod.api, "symbolSearch").mockResolvedValue({
      query: "btc",
      hits: [
        { symbol: "BTC/USDT", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: null },
        { symbol: "BTC/BUSD", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: null },
      ],
    });
    const onSelect = vi.fn();
    render(
      <SymbolAutocomplete initialValue="" onSelect={onSelect} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "btc" } });
    await flushDebounce();
    await screen.findByRole("option", { name: /btc\/usdt/i });
    // ArrowDown moves to index 1 (initial active is 0).
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("BTC/BUSD");
  });

  test("Enter with no hits commits the typed literal value", async () => {
    vi.spyOn(apiMod.api, "symbolSearch").mockResolvedValue({
      query: "ZZZ",
      hits: [],
    });
    const onSelect = vi.fn();
    render(
      <SymbolAutocomplete initialValue="" onSelect={onSelect} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "zzz" } });
    await flushDebounce();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("ZZZ");
  });

  test("Escape closes the dropdown without committing", async () => {
    vi.spyOn(apiMod.api, "symbolSearch").mockResolvedValue({
      query: "shib",
      hits: [
        { symbol: "SHIB/USDT", exchange: "binance", asset_class: "crypto",
          quote_volume_24h_usdt: null },
      ],
    });
    const onSelect = vi.fn();
    render(
      <SymbolAutocomplete initialValue="" onSelect={onSelect} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "shib" } });
    await flushDebounce();
    await screen.findByRole("option", { name: /shib\/usdt/i });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  test("API error renders an empty list (no crash)", async () => {
    vi.spyOn(apiMod.api, "symbolSearch").mockRejectedValue(
      new Error("backend down"),
    );
    render(
      <SymbolAutocomplete initialValue="" onSelect={vi.fn()} />,
    );
    const input = screen.getByRole("combobox");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "shib" } });
    await flushDebounce();
    // Combobox stays mounted, no thrown render error.
    await waitFor(() => {
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    });
  });

  test("input value upper-cases automatically", () => {
    render(
      <SymbolAutocomplete initialValue="" onSelect={vi.fn()} />,
    );
    const input = screen.getByRole("combobox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "shib" } });
    expect(input.value).toBe("SHIB");
  });
});
