import { useCallback, useEffect, useState } from "react";

import { ApiRequestError } from "../services/api";
import { getBackendHealth } from "../services/healthService";
import type { BackendHealth } from "../types/health";

export type BackendHealthState =
  | { status: "CHECKING" }
  | { status: "CONNECTED"; health: BackendHealth }
  | { status: "DISCONNECTED"; error: string };

const INITIAL_STATE: BackendHealthState = { status: "CHECKING" };

export function useBackendHealth() {
  const [state, setState] = useState<BackendHealthState>(INITIAL_STATE);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setState({ status: "CHECKING" });

    getBackendHealth(controller.signal)
      .then((health) => {
        if (active) {
          setState({ status: "CONNECTED", health });
        }
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted) {
          return;
        }

        const message =
          error instanceof ApiRequestError
            ? error.message
            : "An unexpected error occurred while checking Flask.";

        if (import.meta.env.DEV) {
          console.debug("[CodeAtlas] Backend health check failed:", message);
        }
        setState({ status: "DISCONNECTED", error: message });
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setState({ status: "CHECKING" });
    setAttempt((current) => current + 1);
  }, []);

  return { state, retry };
}
