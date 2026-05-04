import { TabNav } from "@/components/layout/TabNav";
import { useHashRoute } from "@/lib/useHashRoute";
import { Tab1LivePrediction } from "@/tabs/Tab1LivePrediction";
import { BotStatus } from "@/tabs/BotStatus";

export default function App() {
  const { tab, setTab } = useHashRoute("live-prediction");
  return (
    <div className="h-screen flex flex-col bg-bg-base text-text-primary">
      <TabNav active={tab} onChange={setTab} />
      <div className="flex-1 min-h-0">
        {tab === "live-prediction" ? <Tab1LivePrediction /> : <BotStatus />}
      </div>
    </div>
  );
}
