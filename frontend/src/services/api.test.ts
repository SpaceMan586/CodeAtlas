import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError, requestJson } from "./api";
import {
  invalidJsonResponse,
  jsonResponse,
  nonJsonResponse,
} from "../test/responses";

describe("requestJson", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("returns valid JSON and sends an Accept header", async () => {
    const payload = { status: "ok" };
    fetchMock.mockResolvedValue(jsonResponse(payload));

    await expect(requestJson("/api/health")).resolves.toEqual(payload);
    const requestHeaders = fetchMock.mock.calls[0]?.[1]?.headers as Headers;
    expect(requestHeaders.get("Accept")).toBe("application/json");
  });

  it.each([400, 404, 500])("classifies HTTP %i as an HTTP error", async (status) => {
    fetchMock.mockResolvedValue(jsonResponse({ error: "unavailable" }, status));

    await expect(requestJson("/api/health")).rejects.toMatchObject({
      name: "ApiRequestError",
      kind: "http",
      status,
    });
  });

  it("keeps a non-JSON HTTP error classified as an HTTP error", async () => {
    fetchMock.mockResolvedValue(nonJsonResponse(500));

    await expect(requestJson("/api/health")).rejects.toMatchObject({
      kind: "http",
      status: 500,
    });
  });

  it("classifies a non-JSON success response", async () => {
    fetchMock.mockResolvedValue(nonJsonResponse());

    await expect(requestJson("/api/health")).rejects.toMatchObject({
      kind: "invalid-content-type",
    });
  });

  it("classifies malformed JSON", async () => {
    fetchMock.mockResolvedValue(invalidJsonResponse());

    await expect(requestJson("/api/health")).rejects.toMatchObject({
      kind: "invalid-json",
    });
  });

  it("classifies fetch rejection as a network error", async () => {
    fetchMock.mockRejectedValue(new TypeError("connection refused"));

    await expect(requestJson("/api/health")).rejects.toMatchObject({
      kind: "network",
    });
  });

  it("classifies a timed out request", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new Error("aborted")),
            { once: true },
          );
        }),
    );

    const request = requestJson("/api/health", { timeoutMs: 25 });
    const rejection = expect(request).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(25);

    await rejection;
  });

  it("distinguishes a caller-aborted request from a timeout", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new Error("aborted")),
            { once: true },
          );
        }),
    );

    const request = requestJson("/api/health", { signal: controller.signal });
    controller.abort();

    const error = await request.catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({ kind: "aborted" });
  });
});
