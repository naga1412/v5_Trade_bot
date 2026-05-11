import { render, screen } from "@testing-library/react";
import { TradeSignalCard } from "@/tabs/Tab1LivePrediction/panels/TradeSignalCard";
import type { LivePrediction } from "@/lib/api";

const baseMock: LivePrediction = {
  symbol: "BTC/USDT",
  timeframe: "1h",
  ts: "2026-05-12T12:00:00Z",
  price: 81000,
  final: { score: 0, direction: "NEUTRAL", confidence: 0, contributing_layers: [] },
  layer_scores: {},
  trade_setup: {
    direction: "NEUTRAL",
    entry: null,
    stop_loss: null,
    take_profit: null,
    risk_reward: null,
  },
  momentum: { rsi: null, macd_line: null, macd_signal: null, macd_hist: null },
  cold_start: false,
  inputs_hash: "x",
};

test("renders placeholder when data is null", () => {
  render(<TradeSignalCard data={null} />);
  expect(screen.getByLabelText("Trade Signal")).toBeInTheDocument();
});

test("LONG direction renders LONG badge with green styling token", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.75, contributing_layers: [] },
      }}
    />,
  );
  expect(screen.getByText("LONG")).toBeInTheDocument();
});

test("SHORT direction renders SHORT badge", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: -0.6, direction: "SHORT", confidence: 0.7, contributing_layers: [] },
      }}
    />,
  );
  expect(screen.getByText("SHORT")).toBeInTheDocument();
});

test("displays signed AI score (e.g. +40, -60)", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.6, contributing_layers: [] },
      }}
    />,
  );
  expect(screen.getByText("+40")).toBeInTheDocument();
});

test("shows high-probability badge when conf>=0.65 and |score|>=0.15", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.75, contributing_layers: [] },
      }}
    />,
  );
  expect(screen.getByText(/High-probability setup/)).toBeInTheDocument();
});

test("does NOT show high-probability badge when confidence is low", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.4, contributing_layers: [] },
      }}
    />,
  );
  expect(screen.queryByText(/High-probability setup/)).not.toBeInTheDocument();
});

test("renders layer breakdown rows when layer_scores present", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.7, contributing_layers: [] },
        layer_scores: {
          smc: { direction: "LONG", strength: 0.8, confidence: 0.7, notes: "" },
          wyckoff: { direction: "LONG", strength: 0.3, confidence: 0.6, notes: "" },
          momentum: { direction: "SHORT", strength: 0.2, confidence: 0.5, notes: "" },
        },
      }}
    />,
  );
  expect(screen.getByText("Layer Breakdown")).toBeInTheDocument();
  expect(screen.getByText("SMC")).toBeInTheDocument();
  expect(screen.getByText("Wyckoff")).toBeInTheDocument();
});

test("R/R formatted as 1 : N when present", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.7, contributing_layers: [] },
        trade_setup: {
          direction: "LONG",
          entry: 81000,
          stop_loss: 80500,
          take_profit: 82000,
          risk_reward: 2.0,
        },
      }}
    />,
  );
  expect(screen.getByText("1 : 2.00")).toBeInTheDocument();
});

test("layer breakdown excludes null layer entries", () => {
  render(
    <TradeSignalCard
      data={{
        ...baseMock,
        final: { score: 0.4, direction: "LONG", confidence: 0.7, contributing_layers: [] },
        layer_scores: {
          smc: { direction: "LONG", strength: 0.8, confidence: 0.7, notes: "" },
          layer4_volume: null,
        },
      }}
    />,
  );
  expect(screen.getByText("SMC")).toBeInTheDocument();
  expect(screen.queryByText(/Volume/i)).not.toBeInTheDocument();
});
