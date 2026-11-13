export type ApiRequestErrorKind =
  | "network"
  | "timeout"
  | "http"
  | "invalid-json"
  | "invalid-content-type"
  | "invalid-response"
  | "aborted";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly kind: ApiRequestErrorKind,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

interface JsonRequestOptions extends Omit<RequestInit, "signal"> {
  signal?: AbortSignal;
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 5_000;
const configuredBaseUrl = import.meta.env.DEV
  ? ""
  : (import.meta.env.VITE_API_BASE_URL?.trim() ?? "");

export const API_BASE_URL = configuredBaseUrl.replace(/\/+$/, "");

export async function requestJson(
  path: string,
  options: JsonRequestOptions = {},
): Promise<unknown> {
  const { signal: callerSignal, timeoutMs = DEFAULT_TIMEOUT_MS, ...requestInit } = options;
  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();

  if (callerSignal?.aborted) {
    controller.abort();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  try {
    const headers = new Headers(requestInit.headers);
    headers.set("Accept", "application/json");

    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...requestInit,
      headers,
      signal: controller.signal,
    });

    if (!response.ok) {
      let backendMessage: string | undefined;
      try {
        const payload: unknown = await response.json();
        if (typeof payload === "object" && payload !== null && !Array.isArray(payload)) {
          const apiError = (payload as Record<string, unknown>).error;
          if (typeof apiError === "object" && apiError !== null && !Array.isArray(apiError)) {
            const message = (apiError as Record<string, unknown>).message;
            if (typeof message === "string" && message.trim()) backendMessage = message;
          }
        }
      } catch {
        // Some proxies and server failures do not return JSON.
      }
      const message = backendMessage ?? (response.status >= 500
        ? `Flask could not be reached or returned a server error (HTTP ${response.status}). Check that the backend is running, then retry.`
        : `Flask returned an unexpected response (HTTP ${response.status}).`);
      throw new ApiRequestError(message, "http", response.status);
    }

    const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.includes("json")) {
      throw new ApiRequestError(
        "Flask returned a response that was not JSON.",
        "invalid-content-type",
        response.status,
      );
    }

    try {
      const payload: unknown = await response.json();
      return payload;
    } catch {
      throw new ApiRequestError(
        "Flask returned invalid JSON.",
        "invalid-json",
        response.status,
      );
    }
  } catch (error) {
    if (error instanceof ApiRequestError) {
      throw error;
    }
    if (callerSignal?.aborted) {
      throw new ApiRequestError("The backend request was cancelled.", "aborted");
    }
    if (timedOut) {
      throw new ApiRequestError(
        "The request to Flask timed out. Try again.",
        "timeout",
      );
    }
    throw new ApiRequestError(
      "Flask could not be reached. Check that the backend is running, then retry.",
      "network",
    );
  } finally {
    globalThis.clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}

