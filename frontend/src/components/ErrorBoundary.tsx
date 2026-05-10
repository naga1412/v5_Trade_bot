/**
 * SP-9 — generic React error boundary.
 *
 * Wraps each tab so that a single panel crash doesn't blank the
 * entire screen. The user sees a clear error card with a "Retry"
 * button (which forces a remount) instead of a blank black void.
 *
 * Class component because hooks can't catch render errors in
 * descendants — that's React's contract.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Human-readable name shown in the fallback ("Bot Status crashed"). */
  label: string;
  /** Optional callback for reporting errors to a logger. */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log so the browser console shows the stack — invaluable
    // for the operator triaging in DevTools.
    // eslint-disable-next-line no-console
    console.error(`[ErrorBoundary:${this.props.label}]`, error, info);
    this.props.onError?.(error, info);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error !== null) {
      return (
        <div
          className="h-full flex items-center justify-center p-4"
          role="alert"
        >
          <div className="max-w-md text-center space-y-2">
            <p className="text-[12px] uppercase tracking-wide text-text-bearish">
              {this.props.label} crashed
            </p>
            <pre className="text-[10px] text-text-secondary whitespace-pre-wrap text-left bg-bg-elevated border border-border rounded p-2 max-h-48 overflow-auto">
              {this.state.error.message}
            </pre>
            <p className="text-[9px] text-text-tertiary">
              Other tabs still work. Tap Retry to remount this tab,
              or refresh the whole page.
            </p>
            <div className="flex gap-1 justify-center">
              <button
                type="button"
                onClick={this.reset}
                className="px-2 py-1 text-[10px] uppercase rounded bg-text-primary text-bg-base"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="px-2 py-1 text-[10px] uppercase rounded border border-border text-text-secondary"
              >
                Hard refresh
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
