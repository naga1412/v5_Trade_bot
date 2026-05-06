import { TabNav } from "@/components/layout/TabNav";
import { ImpersonationBanner } from "@/components/layout/ImpersonationBanner";
import { PausedBanner } from "@/components/layout/PausedBanner";
import { useHashRoute } from "@/lib/useHashRoute";
import { useCurrentUser } from "@/hooks/useCurrentUser";
import { api } from "@/lib/api";
import { Tab1LivePrediction } from "@/tabs/Tab1LivePrediction";
import { BotStatus } from "@/tabs/BotStatus";
import { Tab3Scanner } from "@/tabs/Tab3Scanner";
import { Settings } from "@/tabs/Settings";
import { Admin } from "@/tabs/Admin";

export default function App() {
  const { tab, setTab } = useHashRoute("live-prediction");
  const { user, isAdmin, isImpersonating, reload } = useCurrentUser();

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

  return (
    <div className="h-screen flex flex-col bg-bg-base text-text-primary">
      <PausedBanner />
      {isImpersonating && user !== null && (
        <ImpersonationBanner
          viewingEmail={user.email}
          onExit={handleExitImpersonation}
        />
      )}
      <TabNav active={tab} onChange={setTab} adminVisible={isAdmin} />
      <div className="flex-1 min-h-0">
        {tab === "live-prediction" ? <Tab1LivePrediction /> :
         tab === "bot-status" ? <BotStatus /> :
         tab === "scanner" ? <Tab3Scanner /> :
         tab === "settings" ? <Settings /> :
         tab === "admin" && isAdmin ? <Admin /> : null}
      </div>
    </div>
  );
}
