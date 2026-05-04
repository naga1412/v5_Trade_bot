interface ImpersonationBannerProps {
  /** Email of the user being impersonated (effective user). */
  viewingEmail: string;
  /** Async callback fired when admin clicks Exit. */
  onExit: () => void | Promise<void>;
}

// SP-0.7 Phase I (task I2) per spec §9.2: red top bar above the TabNav with
// "Viewing as {target_email}" text + Exit button on the right. Click Exit ->
// DELETE /api/v1/admin/impersonate -> reload current user -> page reload.
export function ImpersonationBanner({ viewingEmail, onExit }: ImpersonationBannerProps) {
  return (
    <div
      role="banner"
      data-testid="impersonation-banner"
      className="flex items-center justify-between bg-red text-white px-3 py-2 border-b border-red"
    >
      <div className="text-sm font-mono">
        Viewing as <span className="font-semibold">{viewingEmail}</span>
      </div>
      <button
        type="button"
        onClick={() => {
          void onExit();
        }}
        className="min-h-[44px] md:min-h-0 md:h-8 px-3 text-xs uppercase tracking-wide bg-white/15 hover:bg-white/25 rounded border border-white/30"
      >
        Exit Impersonation
      </button>
    </div>
  );
}
