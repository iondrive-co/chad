import { useEffect, useRef, useState, useCallback } from "react";
import { ChadAPI, ChadWebSocket } from "chad-client";
import type { StreamEvent, WSMessage } from "chad-client";

export interface TerminalChunk {
  text: string;
  seq: number | null;
}

/**
 * Hook to manage a WebSocket stream for a Chad session.
 * Decodes base64 terminal output and collects structured events.
 *
 * Uses WebSocket instead of SSE so streaming works through Cloudflare tunnels.
 *
 * @param sessionId   - Session to stream from (null = disconnected)
 * @param sinceSeq    - Skip events before this sequence number.
 * @param apiBaseUrl  - API base URL (for remote/tunnel connections)
 * @param token       - Bearer token for authenticated connections
 */
export function useStream(
  sessionId: string | null,
  sinceSeq?: number,
  apiBaseUrl?: string,
  token?: string,
) {
  const wsRef = useRef<ChadWebSocket | null>(null);
  const [terminalOutput, setTerminalOutput] = useState("");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const utf8Decoder = useRef<TextDecoder | null>(null);
  const completedRef = useRef(false);
  const sinceSeqRef = useRef(sinceSeq);
  sinceSeqRef.current = sinceSeq;

  const decodeTerminal = useCallback((data: string, isText: boolean): string => {
    const normalize = (text: string) => text.replace(/\r\n?/g, "\n");

    if (isText) {
      return normalize(data);
    }

    try {
      const binary = atob(data);
      const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
      if (!utf8Decoder.current) utf8Decoder.current = new TextDecoder("utf-8");
      return normalize(utf8Decoder.current.decode(bytes));
    } catch {
      return normalize(data);
    }
  }, []);

  const reset = useCallback(() => {
    setTerminalOutput("");
    setEvents([]);
    setCompleted(false);
    setError(null);
    completedRef.current = false;
  }, []);

  useEffect(() => {
    if (!sessionId) return;

    reset();
    const baseUrl = apiBaseUrl || "";
    let cancelled = false;
    const ws = new ChadWebSocket(baseUrl, token);

    const handleMessage = (msg: WSMessage) => {
      const seq = typeof msg.data?.seq === "number" ? msg.data.seq : null;

      if (msg.type === "terminal") {
        const raw = msg.data.data as string | undefined;
        const isText = Boolean(msg.data.text);
        if (!raw) return;
        const decoded = decodeTerminal(raw, isText);
        setTerminalOutput((prev) => prev + decoded);
      } else if (msg.type === "event") {
        const event: StreamEvent = { event_type: "event", data: msg.data, seq };
        setEvents((prev) => [...prev, event]);
      } else if (msg.type === "complete") {
        completedRef.current = true;
        setCompleted(true);
      } else if (msg.type === "error") {
        if (completedRef.current) return;
        setError((msg.data.error as string) ?? "Stream error");
      }
    };

    ws.onMessage("any", handleMessage);

    const connect = async () => {
      let ticket: string | undefined;
      if (token) {
        const api = new ChadAPI(baseUrl, token);
        const result = await api.getWebSocketTicket(sessionId);
        ticket = result.ticket;
      }

      if (cancelled) return;
      wsRef.current = ws;
      ws.connect(sessionId, { sinceSeq: sinceSeqRef.current, ticket });
    };

    connect().catch(() => {
      if (!cancelled) setError("Stream error");
    });

    return () => {
      cancelled = true;
      ws.disconnect();
      wsRef.current = null;
    };
  }, [sessionId, apiBaseUrl, token, reset, decodeTerminal]);

  return { terminalOutput, events, completed, error, reset };
}
