import type { WSMessage, WSServerMessageType } from "./types.js";

export type WSCallback = (message: WSMessage) => void;

/**
 * WebSocket client for bidirectional communication with Chad PTY sessions.
 *
 * Sends: input (base64), resize, cancel, ping
 * Receives: terminal, event, complete, error, pong, status
 */
export class ChadWebSocket {
  private ws: WebSocket | null = null;
  private callbacks: Partial<Record<WSServerMessageType | "any", WSCallback[]>> = {};
  private pingInterval: ReturnType<typeof setInterval> | null = null;

  // _token is unused: authentication happens via the short-lived ticket passed
  // to connect(). Kept only so existing call sites (useStream) keep compiling.
  constructor(private baseUrl: string, _token?: string) {
    // Convert http(s) to ws(s)
    this.baseUrl = baseUrl
      .replace(/\/+$/, "")
      .replace(/^http/, "ws");
  }

  /** Connect to a session's WebSocket endpoint. */
  connect(sessionId: string, options?: { sinceSeq?: number; ticket?: string }): void {
    this.disconnect();

    const params = new URLSearchParams();
    if (options?.ticket) {
      params.set("ticket", options.ticket);
    }
    if (options?.sinceSeq != null) params.set("since_seq", String(options.sinceSeq));
    const qs = params.toString();
    const ws = new WebSocket(
      `${this.baseUrl}/api/v1/ws/${sessionId}${qs ? `?${qs}` : ""}`,
    );
    this.ws = ws;

    // Surface silent stream death: if the socket errors or closes before a
    // "complete" message was seen, notify handlers with a synthetic error so
    // consumers don't hang forever waiting on a dead stream.
    let completeSeen = false;
    let errorNotified = false;
    const notifyStreamDeath = (error: string) => {
      if (completeSeen || errorNotified) return;
      errorNotified = true;
      const msg: WSMessage = { type: "error", session_id: sessionId, data: { error } };
      this.emit("error", msg);
      this.emit("any" as WSServerMessageType, msg);
    };

    ws.onmessage = (e) => {
      let msg: WSMessage;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "complete") completeSeen = true;
      this.emit(msg.type, msg);
      this.emit("any" as WSServerMessageType, msg);
    };

    ws.onerror = () => {
      notifyStreamDeath("websocket error");
    };

    ws.onclose = () => {
      this.stopPing();
      notifyStreamDeath("connection closed");
    };

    ws.onopen = () => {
      this.startPing();
    };
  }

  /** Send raw input bytes (base64 encoded) to the PTY. */
  sendInput(base64Data: string): void {
    this.send({ type: "input", data: base64Data });
  }

  /** Resize the PTY terminal. */
  sendResize(rows: number, cols: number): void {
    this.send({ type: "resize", rows, cols });
  }

  /** Request task/PTY cancellation. */
  sendCancel(): void {
    this.send({ type: "cancel" });
  }

  /** Send a ping keepalive. */
  sendPing(): void {
    this.send({ type: "ping" });
  }

  /** Register a callback for a specific message type. */
  onMessage(type: WSServerMessageType | "any", cb: WSCallback): this {
    if (!this.callbacks[type]) this.callbacks[type] = [];
    this.callbacks[type]!.push(cb);
    return this;
  }

  /** Disconnect the WebSocket. */
  disconnect(): void {
    this.stopPing();
    if (this.ws) {
      // Intentional close: drop the handlers first so no synthetic
      // "connection closed" error reaches the message handlers.
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
  }

  /** Whether currently connected. */
  get connected(): boolean {
    return this.ws != null && this.ws.readyState === WebSocket.OPEN;
  }

  // ── private ──

  private send(data: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  private emit(type: WSServerMessageType | "any", msg: WSMessage): void {
    const cbs = this.callbacks[type];
    if (cbs) {
      for (const cb of cbs) cb(msg);
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingInterval = setInterval(() => this.sendPing(), 30_000);
  }

  private stopPing(): void {
    if (this.pingInterval != null) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }
}
