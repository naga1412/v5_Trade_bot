import { useEffect, useState } from "react";
import { TabNav } from "@/components/layout/TabNav";
import { TopNav } from "@/components/layout/TopNav";
import { ImpersonationBanner } from "@/components/layout/ImpersonationBanner";
import { PausedBanner } from "@/components/layout/PausedBanner";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { useHashRoute } from "@/lib/useHashRoute";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api } from "@/lib/api";
import { Tab1LivePrediction } from "@/tabs/Tab1LivePrediction";
import { BotStatus } from "@/tabs/BotStatus";
import { Tab3Scanner } from "@/tabs/Tab3Scanner";
import { Autonomous } from "@/tabs/Autonomous";
import { Settings } from "@/tabs/Settings";
import { Admin } from "@/tabs/Admin";

export type Tf = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

const VALID_TFS: ReadonlySet<string> = new Set([
  "1m",
  "5m",
  "15m",
  "1h",
  "4h",
  "1d",
]);

// Coerce a user-typed symbol to the canonical SYMBOL/QUOTE form.
// Accepts:
//   "BTCUSDT" -> "BTC/USDT"
//   "btc/usdt" -> "BTC/USDT"
//   "shib"    -> "SHIB/USDT"   (default to USDT pair)
function normaliseSymbol(raw: string): string {
  const s = raw.trim().toUpperCase();
  if (!s) return "BTC/USDT";
  if (s.includes("/")) return s;
  // Try to peel a known quote suffix.
  const quoted = s.replace(/(USDT|USDC|BUSD|FDUSD)$/, "/$1");
  if (quoted.includes("/")) return quoted;
  // No quote in input — default to /USDT (the most common Binance pair).
  return `${s}/USDT`;
}

export default function App() {
  const { tab, query, setTab } = useHashRoute("live-prediction");
  const { user, isAdmin, isImpersonating, reload } = useCurrentUser();

  // Hoisted from Tab1LivePrediction so the chosen symbol survives tab
  // switches AND can be set from anywhere via TopNav (which now lives
  // in the chrome above the tab content).
  const [symbol, setSymbol] = useState<string>("BTC/USDT");
  const [timeframe, setTimeframe] = useState<Tf>("1h");
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Deep-link sync: when the hash carries ?symbol=… and/or ?tf=…
  // (e.g. user clicked a card in Scanner or Bot Status to jump here),
  // adopt those into local state so the chart actually reflects them.
  useEffect(() => {
    if (query.symbol) {
      setSymbol(normaliseSymbol(query.symbol));
    }
  }, [query.symbol]);
  useEffect(() => {
    if (query.tf && VALID_TFS.has(query.tf)) {
      setTimeframe(query.tf as Tf);
    }
  }, [query.tf]);

  const handleExitImpersonation = async (): Promise<void> => {
    try {
      await api.adminImpersonateClear();
    } finally {
      // Always reload — page reload guarantees every cached query refetches
      // through the now-cleared impersonation context.
      await reload();
      window.location.reload();
    }
  };

  const handleSymbolChange = (raw: string): void => {
    setSymbol(normaliseSymbol(raw));
    // Selecting a symbol always means "I want to see this on the chart" —
    // navigate to Live Prediction if we're elsewhere.
    if (tab !== "live-prediction") {
      setTab("live-prediction");
    }
  };

  return (
    <div className="h-screen flex flex-col bg-bg-base text-text-primary">
      <PausedBanner />
      {isImpersonating && user !== null && (
        <ImpersonationBanner
          viewingEmail={user.email}
          onExit={handleExitImpersonation}
        />
      )}
      <TopNav
        symbol={symbol}
        onSymbolChange={handleSymbolChange}
        onMenuClick={() => setDrawerOpen(true)}
      />
      <TabNav active={tab} onChange={setTab} adminVisible={isAdmin} />
      <div className="flex-1 min-h-0">
        {/* Each tab in its own ErrorBoundary so a panel crash in
            one tab does not blank the whole dashboard. The boundary
            is keyed on `tab` so navigating to a fresh tab always
            resets the error state. */}
        <ErrorBoundary key={tab} label={tab.replace("-", " ")}>
          {tab === "live-prediction" ? (
            <Tab1LivePrediction
              symbol={symbol}
              timeframe={timeframe}
              onTimeframeChange={setTimeframe}
              drawerOpen={drawerOpen}
              onDrawerClose={() => setDrawerOpen(false)}
            />
          ) :
           tab === "bot-status" ? <BotStatus /> :
           tab === "scanner" ? <Tab3Scanner /> :
           tab === "autonomous" ? <Autonomous /> :
           tab === "settings" ? <Settings /> :
           tab === "admin" && isAdmin ? <Admin /> : null}
        </ErrorBoundary>
      </div>
    </div>
  );
}
