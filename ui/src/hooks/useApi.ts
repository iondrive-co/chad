import { useState, useCallback } from "react";
import { ChadAPI } from "chad-client";

/**
 * Hook to create and manage a ChadAPI instance.
 * Verifies connectivity via getStatus() before returning.
 */
export function useApi() {
  const [api, setApi] = useState<ChadAPI | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  const connect = useCallback(async (url: string) => {
    setConnecting(true);
    setError(null);
    try {
      const client = new ChadAPI(url);
      await client.getStatus();
      setApi(client);
      return client;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection failed");
      setApi(null);
      return null;
    } finally {
      setConnecting(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    setApi(null);
    setError(null);
  }, []);

  return { api, error, connecting, connect, disconnect };
}
