import { useEffect, useRef } from "react";
import {
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type CandlestickData,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useChartHistory, type ChartCandle } from "@/hooks/useChartHistory";
import type { GhostCandle, SignalMarkers } from "@/lib/api";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
  signalMarkers?: SignalMarkers | null;
  ghost?: GhostCandle | null; // SP-1: predicted next-bar candle + uncertainty wicks.
}

const TR_GREEN = "#00d68f";
const TR_RED = "#ff3d71";
const TR_NEUTRAL = "#c4c8d0";
const TR_TIMEOUT = "#ffaa00";
// SP-1 ghost colors — same hue as TR_GREEN/TR_RED, 50% alpha (8-digit hex).
const TR_GREEN_GHOST = "#00d68f80";
const TR_RED_GHOST = "#ff3d7180";

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

export function TVChart({ symbol, timeframe, livePrice, liveTs, signalMarkers, ghost }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  // SP-1: ghost candle series + its uncertainty-wick price lines.
  const ghostSeriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const ghostPriceLinesRef = useRef<IPriceLine[]>([]);

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
      timeScale: { borderColor: "#1f2530", timeVisible: true, secondsVisible: false },
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
      for (const line of priceLinesRef.current) {
        series.removePriceLine(line);
      }
      priceLinesRef.current = [];
      series.setMarkers([]);
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
        for (const line of ghostPriceLinesRef.current) {
          series.removePriceLine(line);
        }
        ghostPriceLinesRef.current = [];
        chart.removeSeries(series);
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

    ghostPriceLinesRef.current.push(
      series.createPriceLine({
        price: ghost.p5_low,
        color: TR_RED_GHOST,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: "P5",
      }),
    );
    ghostPriceLinesRef.current.push(
      series.createPriceLine({
        price: ghost.p95_high,
        color: TR_GREEN_GHOST,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: "P95",
      }),
    );

    return clearGhost;
  }, [ghost, liveTs, timeframe]);

  return <div ref={containerRef} className="w-full h-full bg-bg-chart" />;
}
