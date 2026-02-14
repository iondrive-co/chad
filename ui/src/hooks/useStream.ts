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
export function useStream(serverUrl: string, sessionId: string | null) {
  const streamRef = useRef<ChadStream | null>(null);
  const [terminalOutput, setTerminalOutput] = useState("");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [completed, setCompleted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = useCallback(() => {
    setTerminalOutput("");
    setEvents([]);
    setCompleted(false);
    setError(null);
  }, []);

  useEffect(() => {
    if (!sessionId || !serverUrl) return;

    reset();
    const stream = new ChadStream(serverUrl);
    streamRef.current = stream;

    stream.onTerminal((evt) => {
      const b64 = evt.data.data as string | undefined;
      if (b64) {
        try {
          const text = atob(b64);
          setTerminalOutput((prev) => prev + text);
        } catch {
          // not valid base64 — treat as raw text
          setTerminalOutput((prev) => prev + b64);
        }
      }
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
  }, [serverUrl, sessionId, reset]);

  return { terminalOutput, events, completed, error, reset };
}
