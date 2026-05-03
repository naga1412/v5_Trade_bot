import { useState } from "react";
import { TopNav } from "@/components/layout/TopNav";
import { TimeframeRow } from "@/components/layout/TimeframeRow";
import { Sidebar } from "@/components/layout/Sidebar";
import { TVChart } from "@/components/chart/TVChart";
import { useLivePrediction } from "@/hooks/useLivePrediction";
import { useHashRoute } from "@/lib/useHashRoute";
import { TradeStatusBar } from "./panels/TradeStatusBar";
import { MasterBiasScore } from "./panels/MasterBiasScore";
import { MomentumIndicators } from "./panels/MomentumIndicators";
import { TradeSetup } from "./panels/TradeSetup";

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
          />
        </div>
        <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)}>
          <TradeStatusBar data={data} />
          <MasterBiasScore data={data} />
          <MomentumIndicators data={data} />
          <TradeSetup data={data} />
        </Sidebar>
      </main>
    </div>
  );
}
