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
import type { SignalMarkers } from "@/lib/api";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
  signalMarkers?: SignalMarkers | null;
}

const TR_GREEN = "#00d68f";
const TR_RED = "#ff3d71";
const TR_NEUTRAL = "#c4c8d0";
const TR_TIMEOUT = "#ffaa00";

function isoToUnix(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

export function TVChart({ symbol, timeframe, livePrice, liveTs, signalMarkers }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

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

  return <div ref={containerRef} className="w-full h-full bg-bg-chart" />;
}
