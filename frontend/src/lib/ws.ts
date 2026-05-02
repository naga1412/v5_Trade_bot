type Listener = (msg: unknown) => void;

export class TradingRadarSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private backoff = 1000;
  private url: string;
  private subscription: { channel: string; params: Record<string, unknown> } | null = null;
  private alive = false;
  private heartbeatTimer?: number;

  constructor(clientId: string) {
    const wsBase = (import.meta.env.VITE_WS_URL ?? "/ws/v1") as string;
    this.url = wsBase.startsWith("ws")
      ? `${wsBase}/${clientId}`
      : `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${wsBase}/${clientId}`;
  }

  connect(): void {
    if (this.alive) return;
    this.alive = true;
    this.open();
  }

  private open(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.backoff = 1000;
      if (this.subscription) {
        this.send({ action: "subscribe", ...this.subscription });
      }
      this.scheduleHeartbeatGuard();
    };
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === "ping") {
          this.send({ action: "pong" });
          this.scheduleHeartbeatGuard();
          return;
        }
        for (const l of this.listeners) l(data);
      } catch {
        // ignore malformed
      }
    };
    this.ws.onclose = () => {
      window.clearTimeout(this.heartbeatTimer);
      if (!this.alive) return;
      window.setTimeout(() => this.open(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 30000);
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private scheduleHeartbeatGuard(): void {
    window.clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = window.setTimeout(() => this.ws?.close(), 45_000);
  }

  subscribe(channel: string, params: Record<string, unknown>): void {
    this.subscription = { channel, params };
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({ action: "subscribe", channel, params });
    }
  }

  on(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  close(): void {
    this.alive = false;
    this.ws?.close();
    window.clearTimeout(this.heartbeatTimer);
  }

  private send(payload: unknown): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload));
    }
  }
}
