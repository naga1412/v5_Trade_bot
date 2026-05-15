import { useState } from "react";
import { Panel } from "@/components/ui/Panel";
import {
  api,
  type TestTradeOpenResponse,
  type TestTradeCloseAllResponse,
} from "@/lib/api";

// Admin -> Test Trade sub-page.
//
// Single-purpose UI for the operator's live-dispatch smoke test. Hits
// the backend admin endpoint that synthesizes a small signal and runs
// it through the real dispatcher with use_testnet=True hard-coded.
//
// Workflow:
//   1. Operator clicks "Open BTCUSDT LONG (testnet)"
//   2. Backend dispatches -> Telegram message arrives with Approve button
//   3. Operator taps Approve in Telegram
//   4. telegram_poller_task places the order on Binance Futures testnet
//   5. Operator verifies at testnet.binancefuture.com
//   6. Operator clicks "Close All Testnet Positions" here -> done.
//
// The endpoint is hard-coded to use_testnet=True; this UI is just the
// button. Live-money trades are NOT possible from this surface.

type OpenInProgress = { symbol: string; direction: string } | null;

export function TestTrade() {
  const [openInProgress, setOpenInProgress] = useState<OpenInProgress>(null);
  const [closeInProgress, setCloseInProgress] = useState<boolean>(false);
  const [openResp, setOpenResp] = useState<TestTradeOpenResponse | null>(null);
  const [closeResp, setCloseResp] = useState<TestTradeCloseAllResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  async function open(
    symbol: "BTCUSDT" | "ETHUSDT",
    direction: "LONG" | "SHORT",
  ): Promise<void> {
    setError(null);
    setOpenResp(null);
    setOpenInProgress({ symbol, direction });
    try {
      const r = await api.adminTestTradeOpen({
        symbol,
        direction,
        notional_usdt: 20,
      });
      setOpenResp(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpenInProgress(null);
    }
  }

  async function closeAll(): Promise<void> {
    setError(null);
    setCloseResp(null);
    setCloseInProgress(true);
    try {
      const r = await api.adminTestTradeCloseAll();
      setCloseResp(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCloseInProgress(false);
    }
  }

  const busy = openInProgress !== null || closeInProgress;

  return (
    <div className="space-y-3">
      <Panel title="LIVE DISPATCH SMOKE TEST · BINANCE FUTURES TESTNET">
        <div className="space-y-3 p-3">
          <div className="text-[10px] text-text-secondary leading-relaxed">
            Validates the full chain: vault → dispatcher → Telegram approval →
            testnet order. <span className="text-text-primary">No real money.</span>
          </div>
          <div className="text-[11px] text-text-secondary leading-relaxed">
            Click an Open button below. A small signal (20 USDT notional) is
            synthesized and sent through the real dispatcher with
            <span className="text-text-primary"> use_testnet=True</span> hard-coded.
            You will receive a Telegram message with Approve/Reject buttons.
            Tap Approve to place the order on Binance Futures testnet, then
            verify at{" "}
            <a
              href="https://testnet.binancefuture.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-text-primary underline"
            >
              testnet.binancefuture.com
            </a>
            . Use Close All when finished.
          </div>

          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => open("BTCUSDT", "LONG")}
              className="h-10 px-3 text-xs font-mono uppercase tracking-wide bg-green/15 text-green border border-green/40 rounded hover:bg-green/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {openInProgress?.symbol === "BTCUSDT" &&
              openInProgress?.direction === "LONG"
                ? "Dispatching…"
                : "Open BTCUSDT LONG"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => open("BTCUSDT", "SHORT")}
              className="h-10 px-3 text-xs font-mono uppercase tracking-wide bg-red/15 text-red border border-red/40 rounded hover:bg-red/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {openInProgress?.symbol === "BTCUSDT" &&
              openInProgress?.direction === "SHORT"
                ? "Dispatching…"
                : "Open BTCUSDT SHORT"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => open("ETHUSDT", "LONG")}
              className="h-10 px-3 text-xs font-mono uppercase tracking-wide bg-green/15 text-green border border-green/40 rounded hover:bg-green/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {openInProgress?.symbol === "ETHUSDT" &&
              openInProgress?.direction === "LONG"
                ? "Dispatching…"
                : "Open ETHUSDT LONG"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => open("ETHUSDT", "SHORT")}
              className="h-10 px-3 text-xs font-mono uppercase tracking-wide bg-red/15 text-red border border-red/40 rounded hover:bg-red/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {openInProgress?.symbol === "ETHUSDT" &&
              openInProgress?.direction === "SHORT"
                ? "Dispatching…"
                : "Open ETHUSDT SHORT"}
            </button>
          </div>

          <button
            type="button"
            disabled={busy}
            onClick={closeAll}
            className="w-full h-10 px-3 text-xs font-mono uppercase tracking-wide bg-bg-elevated text-text-primary border border-border rounded hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {closeInProgress ? "Closing…" : "Close All Testnet Positions"}
          </button>

          {error !== null && (
            <div className="p-2 bg-red/10 border border-red/30 rounded text-xs text-red font-mono">
              <div className="font-bold uppercase tracking-wide mb-1">Error</div>
              <div className="whitespace-pre-wrap break-words">{error}</div>
            </div>
          )}

          {openResp !== null && (
            <div
              data-testid="open-response"
              className="p-2 bg-bg-elevated border border-border rounded text-xs font-mono space-y-1"
            >
              <div className="text-text-secondary uppercase tracking-wide">
                Open response
              </div>
              <div>
                outcome:{" "}
                <span
                  className={
                    openResp.outcome === "sent_telegram"
                      ? "text-green"
                      : "text-yellow-400"
                  }
                >
                  {openResp.outcome}
                </span>
              </div>
              <div>signal_id: {openResp.signal_id ?? "—"}</div>
              <div>
                entry: {openResp.entry_price.toFixed(2)} / sl:{" "}
                {openResp.stop_loss_price.toFixed(2)} / tp:{" "}
                {openResp.take_profit_price.toFixed(2)}
              </div>
              <div className="pt-1 text-text-primary border-t border-border">
                <span className="text-text-secondary">next: </span>
                {openResp.next_step}
              </div>
            </div>
          )}

          {closeResp !== null && (
            <div
              data-testid="close-response"
              className="p-2 bg-bg-elevated border border-border rounded text-xs font-mono space-y-1"
            >
              <div className="text-text-secondary uppercase tracking-wide">
                Close response
              </div>
              <div>
                closed: <span className="text-green">{closeResp.closed.length}</span>{" "}
                · skipped:{" "}
                <span className="text-text-secondary">
                  {closeResp.skipped.length}
                </span>
              </div>
              {closeResp.closed.map((c) => (
                <div key={c.binance_order_id} className="text-text-primary">
                  ✓ {c.symbol} qty={c.qty} @ {c.avg_fill_price.toFixed(2)}{" "}
                  (order #{c.binance_order_id})
                </div>
              ))}
              {closeResp.skipped.map((s) => (
                <div key={s.symbol} className="text-text-secondary">
                  · {s.symbol}: {s.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>
    </div>
  );
}
