import { useEffect, useRef, useState, useCallback } from "react";
import { ChadStream } from "chad-client";
import type { StreamEvent } from "chad-client";

export interface TerminalChunk {
  text: string;
  seq: number | null;
}

/**
 * Hook to manage an SSE stream for a Chad session.
 * Decodes base64 terminal output and collects structured events.
 */
export function useStream(sessionId: string | null) {
  const streamRef = useRef<ChadStream | null>(null);
  const [terminalOutput, setTerminalOutput] = useState("");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const utf8Decoder = useRef<TextDecoder | null>(null);

  const decodeTerminal = useCallback((data: string, isText: boolean): string => {
    const normalize = (text: string) => text.replace(/(?<!\r)\n/g, "\r\n");

    if (isText) {
      return normalize(data);
    }

    try {
      // Decode base64 → UTF-8 string
      const binary = atob(data);
      const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
      if (!utf8Decoder.current) utf8Decoder.current = new TextDecoder("utf-8");
      return normalize(utf8Decoder.current.decode(bytes));
    } catch {
      // Fallback: treat as plain text
      return normalize(data);
    }
  }, []);

  const reset = useCallback(() => {
    setTerminalOutput("");
    setEvents([]);
    setCompleted(false);
    setError(null);
  }, []);

  useEffect(() => {
    if (!sessionId) return;

    reset();
    const stream = new ChadStream("");
    streamRef.current = stream;

    stream.onTerminal((evt) => {
      const payload = evt.data as Record<string, unknown>;
      const raw = payload.data as string | undefined;
      const isText = Boolean(payload.text);
      if (!raw) return;
      const decoded = decodeTerminal(raw, isText);
      setTerminalOutput((prev) => prev + decoded);
    });

    stream.onEvent((evt) => {
      setEvents((prev) => [...prev, evt]);
    });

    stream.onComplete(() => {
      setCompleted(true);
    });

    stream.onError((evt) => {
      setError(
        (evt.data.error as string) ?? "Stream error",
      );
    });

    stream.connect(sessionId);

    return () => {
      stream.disconnect();
      streamRef.current = null;
    };
  }, [sessionId, reset]);

  return { terminalOutput, events, completed, error, reset };
}
