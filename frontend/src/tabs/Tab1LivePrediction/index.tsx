import { TimeframeRow } from "@/components/layout/TimeframeRow";
import { Sidebar } from "@/components/layout/Sidebar";
import { TVChart } from "@/components/chart/TVChart";
import { PredictionAccuracyBadge } from "@/components/PredictionAccuracyBadge";
import { useLivePrediction } from "@/hooks/useLivePrediction";
import { useHashRoute } from "@/lib/useHashRoute";

// Tab 1 sidebar panels — order follows MASTER_PLAN §9 lines 280-298.
// TradeSignalCard is a new top-of-sidebar summary that consolidates the
// most important fields into one SMC-Pro-style card. The 17 detail
// panels below remain unchanged.
import { TradeSignalCard } from "./panels/TradeSignalCard";
import { TradeStatusBar } from "./panels/TradeStatusBar";
import { MasterBiasScore } from "./panels/MasterBiasScore";
import { FinalValue } from "./panels/FinalValue";
import { LongShortRatio } from "./panels/LongShortRatio";
import { DeepLearningSupervisor } from "./panels/DeepLearningSupervisor";
import { HtfBiasStructure } from "./panels/HtfBiasStructure";
import { VolumeProfile } from "./panels/VolumeProfile";
import { MomentumIndicators } from "./panels/MomentumIndicators";
import { MarketMicrostructure } from "./panels/MarketMicrostructure";
import { LiquiditySweep } from "./panels/LiquiditySweep";
import { OiFundingRate } from "./panels/OiFundingRate";
import { IntermarketAnalysis } from "./panels/IntermarketAnalysis";
import { SentimentFearGreed } from "./panels/SentimentFearGreed";
import { GhostCandlePrediction } from "./panels/GhostCandlePrediction";
import { TradeSetup } from "./panels/TradeSetup";
import { KeyLevels } from "./panels/KeyLevels";
import { NewsMacroImpact } from "./panels/NewsMacroImpact";

export type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

interface Props {
  symbol: string;
  timeframe: Tf;
  onTimeframeChange: (tf: Tf) => void;
  drawerOpen: boolean;
  onDrawerClose: () => void;
}

export function Tab1LivePrediction({
  symbol,
  timeframe,
  onTimeframeChange,
  drawerOpen,
  onDrawerClose,
}: Props) {
  const { query } = useHashRoute();
  const signalId = query.signal;
  const { data, err } = useLivePrediction(symbol, timeframe, signalId);

  return (
    <div className="h-full flex flex-col min-h-0">
      {err !== null ? (
        <div
          role="alert"
          className="bg-red/20 border-b border-red text-text-primary text-[10px] px-2 py-1 font-mono"
        >
          ⚠ {err}
        </div>
      ) : null}
      <TimeframeRow active={timeframe} onChange={onTimeframeChange} />
      <main className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0 flex flex-col">
          <div className="px-2 py-1 border-b border-border bg-bg-panel/50">
            <PredictionAccuracyBadge
              symbolPath={symbol.replace("/", "-")}
              timeframe={timeframe}
            />
          </div>
          <div className="flex-1 min-h-0">
            <TVChart
              symbol={symbol}
              timeframe={timeframe}
              {...(data?.price != null ? { livePrice: data.price } : {})}
              {...(data?.ts != null ? { liveTs: data.ts } : {})}
              signalMarkers={data?.signal_markers ?? null}
              ghost={data?.ghost ?? null}
            />
          </div>
        </div>
        {/* MASTER_PLAN §9 panels in order. DeepLearningSupervisor (#5)
            renders null when no L8 alert; all other slots are permanent. */}
        <Sidebar open={drawerOpen} onClose={onDrawerClose}>
          <TradeSignalCard data={data} />
          <TradeStatusBar data={data} />
          <MasterBiasScore data={data} />
          <FinalValue data={data} />
          <LongShortRatio data={data} />
          <DeepLearningSupervisor data={data} />
          <HtfBiasStructure data={data} />
          <VolumeProfile data={data} />
          <MomentumIndicators data={data} />
          <MarketMicrostructure data={data} />
          <LiquiditySweep data={data} />
          <OiFundingRate data={data} />
          <IntermarketAnalysis data={data} />
          <SentimentFearGreed data={data} />
          <GhostCandlePrediction data={data} />
          <TradeSetup data={data} />
          <KeyLevels data={data} />
          <NewsMacroImpact data={data} />
        </Sidebar>
      </main>
    </div>
  );
}
