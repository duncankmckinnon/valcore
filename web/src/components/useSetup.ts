// The one hook every gateway-gated action (generation, runs) reads to decide whether to render
// disabled, and the shared string those call sites show when it does. Fetching setup status is a
// hint for the UI, not the enforcement — the server-side guard is what actually blocks the
// request — so a transient fetch failure or a slow initial load must never strand the user with
// every button disabled. That is why `gatewayReady` defaults to (and stays) true except in the
// one case where the loaded status says the key is actually unset.

import { useCallback, useEffect, useState } from "react";
import { setup } from "../api/client";
import type { SetupStatus } from "../api/types";

export const GATEWAY_BLOCKER =
  "Set the Pydantic AI Gateway key to generate or run — see Setup on the Overview page.";

export interface UseSetupResult {
  status: SetupStatus | null;
  gatewayReady: boolean;
  loading: boolean;
  error: unknown;
  refetch: () => void;
}

export function useSetup(): UseSetupResult {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  // Bumping this triggers the fetch effect on demand, driving both the initial load and refetch
  // through the same cancelled-flag-guarded effect rather than duplicating the fetch logic.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);

    setup
      .get()
      .then((result) => {
        // Guard against a stale in-flight response overwriting a newer one: a superseded fetch
        // has already had `cancelled` flipped by its cleanup before this resolves.
        if (!cancelled) {
          setStatus(result);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [version]);

  const refetch = useCallback(() => {
    setVersion((current) => current + 1);
  }, []);

  // Only a loaded, successful response reporting the key unset may disable gated actions —
  // loading, an error, or a not-yet-loaded status must all resolve to true.
  const gatewayKey = status?.keys.find((key) => key.name === "gateway_api_key");
  const gatewayReady = loading || error !== null || gatewayKey?.set !== false;

  return { status, gatewayReady, loading, error, refetch };
}
