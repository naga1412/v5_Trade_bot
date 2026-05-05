import { useState } from "react";
import { TopNav } from "@/components/layout/TopNav";
import { TimeframeRow } from "@/components/layout/TimeframeRow";
import { Sidebar } from "@/components/layout/Sidebar";
import { TVChart } from "@/components/chart/TVChart";
import { useLivePrediction } from "@/hooks/useLivePrediction";
import { useHashRoute } from "@/lib/useHashRoute";

// Tab 1 sidebar panels — order follows MASTER_PLAN §9 lines 280-298.
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

type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export function Tab1LivePrediction() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState<Tf>("1h");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { query } = useHashRoute();
  const signalId = query.signal;
  const { data } = useLivePrediction(symbol, timeframe, signalId);

  return (
    <div className="h-full flex flex-col min-h-0">
      <TopNav
        symbol={symbol}
        onSymbolChange={(s) => setSymbol(s.includes("/") ? s : s.replace(/(USDT|USDC|BUSD)$/, "/$1"))}
        onMenuClick={() => setDrawerOpen(true)}
      />
      <TimeframeRow active={timeframe} onChange={(tf) => setTimeframe(tf)} />
      <main className="flex-1 flex min-h-0">
        <div className="flex-1 min-w-0">
          <TVChart
            symbol={symbol}
            timeframe={timeframe}
            {...(data?.price != null ? { livePrice: data.price } : {})}
            {...(data?.ts != null ? { liveTs: data.ts } : {})}
            signalMarkers={data?.signal_markers ?? null}
            ghost={data?.ghost ?? null}
          />
        </div>
        {/* MASTER_PLAN §9 panels in order. DeepLearningSupervisor (#5)
            renders null when no L8 alert; all other slots are permanent. */}
        <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)}>
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
