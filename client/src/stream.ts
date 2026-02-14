import type { StreamEvent, StreamEventType } from "./types.js";

export type StreamCallback = (event: StreamEvent) => void;

export interface ChadStreamOptions {
  sinceSeq?: number;
  includeTerminal?: boolean;
  includeEvents?: boolean;
}

/**
 * SSE streaming client for Chad task output.
 *
 * Uses the native EventSource API (works in all browsers).
 * Handles reconnection with `since_seq` for resume.
 */
export class ChadStream {
  private eventSource: EventSource | null = null;
  private callbacks: Partial<Record<StreamEventType, StreamCallback[]>> = {};
  private lastSeq = 0;

  constructor(private baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  /** Start streaming events for a session. */
  connect(sessionId: string, options: ChadStreamOptions = {}): void {
    this.disconnect();

    const params = new URLSearchParams();
    const sinceSeq = options.sinceSeq ?? this.lastSeq;
    if (sinceSeq > 0) params.set("since_seq", String(sinceSeq));
    if (options.includeTerminal === false) {
      params.set("include_terminal", "false");
    }
    if (options.includeEvents === false) {
      params.set("include_events", "false");
    }

    const qs = params.toString();
    const url = `${this.baseUrl}/api/v1/sessions/${sessionId}/stream${qs ? `?${qs}` : ""}`;
    const es = new EventSource(url);
    this.eventSource = es;

    const eventTypes: StreamEventType[] = [
      "terminal",
      "event",
      "ping",
      "complete",
      "error",
    ];

    for (const type of eventTypes) {
      es.addEventListener(type, (e: MessageEvent) => {
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(e.data);
        } catch {
          data = { raw: e.data };
        }

        const seq =
          typeof data.seq === "number" ? data.seq : null;
        if (seq != null && seq > this.lastSeq) {
          this.lastSeq = seq;
        }

        const event: StreamEvent = { event_type: type, data, seq };
        this.emit(type, event);
      });
    }
  }

  /** Register a callback for terminal output events. */
  onTerminal(cb: StreamCallback): this {
    return this.on("terminal", cb);
  }

  /** Register a callback for structured events. */
  onEvent(cb: StreamCallback): this {
    return this.on("event", cb);
  }

  /** Register a callback for task completion. */
  onComplete(cb: StreamCallback): this {
    return this.on("complete", cb);
  }

  /** Register a callback for errors. */
  onError(cb: StreamCallback): this {
    return this.on("error", cb);
  }

  /** Register a callback for ping/keepalive events. */
  onPing(cb: StreamCallback): this {
    return this.on("ping", cb);
  }

  /** Disconnect from the SSE stream. */
  disconnect(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  /** Whether currently connected. */
  get connected(): boolean {
    return (
      this.eventSource != null &&
      this.eventSource.readyState !== EventSource.CLOSED
    );
  }

  /** Last received sequence number (for resume). */
  get seq(): number {
    return this.lastSeq;
  }

  // ── private ──

  private on(type: StreamEventType, cb: StreamCallback): this {
    if (!this.callbacks[type]) this.callbacks[type] = [];
    this.callbacks[type]!.push(cb);
    return this;
  }

  private emit(type: StreamEventType, event: StreamEvent): void {
    const cbs = this.callbacks[type];
    if (cbs) {
      for (const cb of cbs) cb(event);
    }
  }
}
