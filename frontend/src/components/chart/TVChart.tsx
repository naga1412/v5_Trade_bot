import { useEffect, useRef } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type Time,
} from "lightweight-charts";
import { useChartHistory, type ChartCandle } from "@/hooks/useChartHistory";

interface Props {
  symbol: string;
  timeframe: string;
  livePrice?: number;
  liveTs?: string;
}

const TR_GREEN = "#00d68f";
const TR_RED = "#ff3d71";

export function TVChart({ symbol, timeframe, livePrice, liveTs }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);

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

  return <div ref={containerRef} className="w-full h-full bg-bg-chart" />;
}
