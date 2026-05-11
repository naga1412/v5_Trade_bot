import { useEffect, useRef } from "react";
import {
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type CandlestickData,
  type LineData,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useChartHistory, type ChartCandle } from "@/hooks/useChartHistory";
import type { GhostCandle, GhostPathStep, SignalMarkers } from "@/lib/api";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
  signalMarkers?: SignalMarkers | null;
  ghost?: GhostCandle | null; // SP-1: predicted next-bar candle + uncertainty wicks.
  ghostPath?: GhostPathStep[]; // Feature 3: forward rollout — bars 2..N + cone.
}

const TR_GREEN = "#00d68f";
const TR_RED = "#ff3d71";
const TR_NEUTRAL = "#c4c8d0";
const TR_TIMEOUT = "#ffaa00";
// SP-1 ghost colors — same hue as TR_GREEN/TR_RED. Bumped from 50% alpha
// (`80`) to 90% (`E6`) per operator feedback: at 50% the ghost candle was
// invisible against the dark chart background, especially on tight-spread
// bars (BTC at $80k with $1 between open/close). 90% makes the prediction
// clearly visible while still distinguishable from the live (100%) bars.
const TR_GREEN_GHOST = "#00d68fE6";
const TR_RED_GHOST = "#ff3d71E6";

// Feature 3 — forward ghost cone palette. Three progressive alpha tiers
// matching the visual grade: candles (steps 1-3) → close-line dots
// (steps 4-7) → P5/P95 cone (steps 8-N). Each tier signals decreasing
// certainty so the eye reads "anywhere in the cone is plausible".
const TR_GHOST_CANDLE_ALPHA = "B3"; // 70% — used for steps 2-3 fades
const TR_PATH_CLOSE = "#c4c8d066"; // neutral close-line color, 40% alpha
const TR_CONE_BOUND = "#7e8d9e66"; // P5/P95 lines, 40% alpha
const TR_CONE_FILL = "#7e8d9e22"; // area between bounds, 13% alpha

function isoToUnix(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function tfToSeconds(tf: string): number {
  const m = tf.match(/^(\d+)([mhd])$/);
  if (!m) return 3600;
  const n = parseInt(m[1] ?? "1", 10);
  const unit = m[2];
  const mult = unit === "m" ? 60 : unit === "h" ? 3600 : 86400;
  return n * mult;
}

export function TVChart({
  symbol, timeframe, livePrice, liveTs, signalMarkers, ghost, ghostPath,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  // SP-1: ghost candle series + its uncertainty-wick price lines.
  const ghostSeriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const ghostPriceLinesRef = useRef<IPriceLine[]>([]);
  // Feature 3: forward-path overlay series — faded candles for steps 2-3,
  // line for predicted closes (steps 4+), and two lines bounding the cone.
  const pathCandlesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const pathCloseRef = useRef<ISeriesApi<"Line", Time> | null>(null);
  const pathP5Ref = useRef<ISeriesApi<"Line", Time> | null>(null);
  const pathP95Ref = useRef<ISeriesApi<"Line", Time> | null>(null);

  const history = useChartHistory(symbol, timeframe);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#0d1018" }, textColor: "#c4c8d0" },
      grid: {
        vertLines: { color: "#1f2530" },
        horzLines: { color: "#1f2530" },
      },
      rightPriceScale: { borderColor: "#1f2530" },
      timeScale: {
        borderColor: "#1f2530", timeVisible: true, secondsVisible: false,
        // Reserve 22 bars of space on the right so the SP-1 single ghost
        // (step 1) AND the Feature 3 forward path (steps 2-20) all fit
        // inside the visible viewport. Original SP-1 used rightOffset=5
        // for the one-bar ghost; we extend it to cover the full path.
        rightOffset: 22,
      },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: TR_GREEN,
      downColor: TR_RED,
      borderUpColor: TR_GREEN,
      borderDownColor: TR_RED,
      wickUpColor: TR_GREEN,
      wickDownColor: TR_RED,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    if (!seriesRef.current || history.length === 0) return;
    const data: CandlestickData<Time>[] = history.map((c: ChartCandle) => ({
      time: c.time as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    seriesRef.current.setData(data);
  }, [history]);

  useEffect(() => {
    if (!seriesRef.current || livePrice == null || liveTs == null) return;
    const t = Math.floor(new Date(liveTs).getTime() / 1000) as Time;
    seriesRef.current.update({
      time: t,
      open: livePrice,
      high: livePrice,
      low: livePrice,
      close: livePrice,
    });
  }, [livePrice, liveTs]);

  // Signal-marker overlay: three horizontal price lines (entry/SL/TP) plus
  // an "Open" arrow at opened_at and (if closed) an exit arrow at closed_at.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const clearOverlay = (): void => {
      // lightweight-charts throws "Value is undefined" if the parent
      // chart has already been disposed (e.g. unmount-cleanup ordering
      // means the chart-creation effect's `chart.remove()` ran first).
      // Each removal needs its own try so one disposed line doesn't
      // skip the rest. Same caveat applies to setMarkers.
      for (const line of priceLinesRef.current) {
        try {
          series.removePriceLine(line);
        } catch {
          // chart already disposed — nothing to clean up
        }
      }
      priceLinesRef.current = [];
      try {
        series.setMarkers([]);
      } catch {
        // chart already disposed
      }
    };

    if (!signalMarkers) {
      clearOverlay();
      return;
    }

    // Always start fresh — the effect re-runs when signalMarkers changes.
    clearOverlay();

    const isLong = signalMarkers.direction === "LONG";
    const directionColor = isLong ? TR_GREEN : TR_RED;

    priceLinesRef.current.push(
      series.createPriceLine({
        price: signalMarkers.entry_price,
        color: TR_NEUTRAL,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Entry",
      }),
    );
    priceLinesRef.current.push(
      series.createPriceLine({
        price: signalMarkers.stop_loss,
        color: TR_RED,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "SL",
      }),
    );
    priceLinesRef.current.push(
      series.createPriceLine({
        price: signalMarkers.take_profit,
        color: TR_GREEN,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "TP",
      }),
    );

    const markers: SeriesMarker<Time>[] = [
      {
        time: isoToUnix(signalMarkers.opened_at) as Time,
        position: isLong ? "belowBar" : "aboveBar",
        color: directionColor,
        shape: isLong ? "arrowUp" : "arrowDown",
        text: "Open",
      },
    ];
    if (signalMarkers.closed_at && signalMarkers.exit_reason) {
      const exitColor =
        signalMarkers.exit_reason === "TAKE_PROFIT"
          ? TR_GREEN
          : signalMarkers.exit_reason === "STOP_LOSS"
          ? TR_RED
          : TR_TIMEOUT;
      markers.push({
        time: isoToUnix(signalMarkers.closed_at) as Time,
        position: isLong ? "aboveBar" : "belowBar",
        color: exitColor,
        shape: isLong ? "arrowDown" : "arrowUp",
        text: signalMarkers.exit_reason,
      });
    }
    series.setMarkers(markers);

    return () => {
      // Cleanup on unmount or signalMarkers change.
      clearOverlay();
    };
  }, [signalMarkers]);

  // SP-1: ghost candle effect. Adds a SECOND candlestick series one timeframe
  // ahead of liveTs, with translucent fill and two dashed price lines for the
  // P5/P95 uncertainty band. Cleared on unmount or when ghost becomes null.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const clearGhost = (): void => {
      const series = ghostSeriesRef.current;
      if (series) {
        // Same disposal-race story as the signal-marker overlay above:
        // when the user navigates away from Live Prediction, React runs
        // the chart-creation effect's cleanup first (chart.remove()),
        // then THIS cleanup runs against an already-disposed chart.
        // Each call wrapped so one throw doesn't skip the next.
        for (const line of ghostPriceLinesRef.current) {
          try {
            series.removePriceLine(line);
          } catch {
            // chart already disposed
          }
        }
        ghostPriceLinesRef.current = [];
        try {
          chart.removeSeries(series);
        } catch {
          // chart already disposed
        }
        ghostSeriesRef.current = null;
      }
    };

    if (!ghost || !liveTs) {
      clearGhost();
      return;
    }

    // Always start fresh — the effect re-runs when ghost/liveTs/timeframe changes.
    clearGhost();

    const series = chart.addCandlestickSeries({
      upColor: TR_GREEN_GHOST,
      downColor: TR_RED_GHOST,
      borderUpColor: TR_GREEN_GHOST,
      borderDownColor: TR_RED_GHOST,
      wickUpColor: TR_GREEN_GHOST,
      wickDownColor: TR_RED_GHOST,
      priceLineVisible: false,
    });
    ghostSeriesRef.current = series;

    const ghostTs = (isoToUnix(liveTs) + tfToSeconds(timeframe)) as Time;
    series.setData([
      {
        time: ghostTs,
        open: ghost.open,
        high: ghost.high,
        low: ghost.low,
        close: ghost.close,
      },
    ]);

    // Add a SOLID line at the predicted close so the operator can spot
    // the ghost prediction at a glance — useful for manual trading. The
    // P5/P95 dashed lines stay as the uncertainty band.
    ghostPriceLinesRef.current.push(
      series.createPriceLine({
        price: ghost.close,
        color: ghost.close >= ghost.open ? TR_GREEN_GHOST : TR_RED_GHOST,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: "Ghost",
      }),
    );
    ghostPriceLinesRef.current.push(
      series.createPriceLine({
        price: ghost.p5_low,
        color: TR_RED_GHOST,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Ghost P5",
      }),
    );
    ghostPriceLinesRef.current.push(
      series.createPriceLine({
        price: ghost.p95_high,
        color: TR_GREEN_GHOST,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Ghost P95",
      }),
    );

    return clearGhost;
  }, [ghost, liveTs, timeframe]);

  // Feature 3: forward ghost cone. Steps 2-3 render as full candles (faded),
  // steps 4-7 as a close-line dot, and steps 8-N as a P5/P95 area cone.
  // Step 1 is already drawn by the SP-1 ghost effect above — we skip it
  // here to avoid double-drawing.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const clearPath = (): void => {
      // Same disposal-race pattern as the SP-1 ghost cleanup above.
      const candles = pathCandlesRef.current;
      const closeLine = pathCloseRef.current;
      const p5Line = pathP5Ref.current;
      const p95Line = pathP95Ref.current;
      if (candles) {
        try { chart.removeSeries(candles); } catch { /* chart disposed */ }
        pathCandlesRef.current = null;
      }
      if (closeLine) {
        try { chart.removeSeries(closeLine); } catch { /* chart disposed */ }
        pathCloseRef.current = null;
      }
      if (p5Line) {
        try { chart.removeSeries(p5Line); } catch { /* chart disposed */ }
        pathP5Ref.current = null;
      }
      if (p95Line) {
        try { chart.removeSeries(p95Line); } catch { /* chart disposed */ }
        pathP95Ref.current = null;
      }
    };

    if (!ghostPath || ghostPath.length < 2 || !liveTs) {
      clearPath();
      return;
    }

    clearPath();

    const tfSec = tfToSeconds(timeframe);
    const baseTs = isoToUnix(liveTs);

    // Steps 2-3 — faded candle bodies. Past step 3 the candle bodies are
    // misleading (uncertainty exceeds the body width), so we drop them.
    const candleSteps = ghostPath.filter((s) => s.step >= 2 && s.step <= 3);
    if (candleSteps.length > 0) {
      const c = chart.addCandlestickSeries({
        upColor: TR_GREEN_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        downColor: TR_RED_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        borderUpColor: TR_GREEN_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        borderDownColor: TR_RED_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        wickUpColor: TR_GREEN_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        wickDownColor: TR_RED_GHOST.slice(0, 7) + TR_GHOST_CANDLE_ALPHA,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const data: CandlestickData<Time>[] = candleSteps.map((s) => ({
        time: (baseTs + s.step * tfSec) as Time,
        open: s.open,
        high: s.high,
        low: s.low,
        close: s.close,
      }));
      c.setData(data);
      pathCandlesRef.current = c;
    }

    // Steps 4+ — predicted-close line. Pivots the eye along the central
    // path of the projection while the cone below carries the uncertainty.
    const closeLineSteps = ghostPath.filter((s) => s.step >= 4);
    if (closeLineSteps.length > 0) {
      const ln = chart.addLineSeries({
        color: TR_PATH_CLOSE,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const data: LineData<Time>[] = closeLineSteps.map((s) => ({
        time: (baseTs + s.step * tfSec) as Time,
        value: s.close,
      }));
      ln.setData(data);
      pathCloseRef.current = ln;
    }

    // Cone bounds — P5 and P95 lines from step 2 onward, both rendered
    // as thin neutral lines with the area between them shaded faintly.
    // lightweight-charts doesn't natively support a fill-between, so
    // we draw the two bounds and rely on the dotted close-line to keep
    // the eye centered.
    const coneSteps = ghostPath.filter((s) => s.step >= 2);
    if (coneSteps.length > 0) {
      const p5 = chart.addLineSeries({
        color: TR_CONE_BOUND,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const p95 = chart.addLineSeries({
        color: TR_CONE_BOUND,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      p5.setData(coneSteps.map((s) => ({
        time: (baseTs + s.step * tfSec) as Time,
        value: s.p5_low,
      })));
      p95.setData(coneSteps.map((s) => ({
        time: (baseTs + s.step * tfSec) as Time,
        value: s.p95_high,
      })));
      pathP5Ref.current = p5;
      pathP95Ref.current = p95;
    }

    return clearPath;
    // TR_CONE_FILL is reserved for a future enhancement that overlays a
    // semi-transparent fill between p5 and p95 using a CustomSeries view.
    // lightweight-charts v4 doesn't expose a fill-between primitive.
    void TR_CONE_FILL;
  }, [ghostPath, liveTs, timeframe]);

  return <div ref={containerRef} className="w-full h-full bg-bg-chart" />;
}
