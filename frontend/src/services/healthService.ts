import { ApiRequestError, requestJson } from "./api";
import type { BackendHealth } from "../types/health";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isBackendHealth(value: unknown): value is BackendHealth {
  return (
    isRecord(value) &&
    value.status === "ok" &&
    typeof value.application === "string" &&
    value.application.trim().length > 0 &&
    typeof value.version === "string" &&
    value.version.trim().length > 0
  );
}

export async function getBackendHealth(
  signal?: AbortSignal,
): Promise<BackendHealth> {
  const payload = await requestJson("/api/health", { signal });
  if (!isBackendHealth(payload)) {
    throw new ApiRequestError(
      "Flask returned an unexpected health response.",
      "invalid-response",
    );
  }
  return payload;
}

