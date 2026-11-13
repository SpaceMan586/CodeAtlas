import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "../services/api";
import { getBackendHealth } from "../services/healthService";
import type { BackendHealth } from "../types/health";
import { useBackendHealth } from "./useBackendHealth";

vi.mock("../services/healthService", () => ({
  getBackendHealth: vi.fn(),
}));

const health: BackendHealth = {
  status: "ok",
  application: "CodeAtlas",
  version: "0.1.0",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const getBackendHealthMock = vi.mocked(getBackendHealth);

describe("useBackendHealth", () => {
  beforeEach(() => {
    getBackendHealthMock.mockReset();
  });

  it("starts in CHECKING", () => {
    getBackendHealthMock.mockReturnValue(new Promise(() => undefined));

    const { result } = renderHook(() => useBackendHealth());

    expect(result.current.state).toEqual({ status: "CHECKING" });
  });

  it("changes to CONNECTED after a successful request", async () => {
    getBackendHealthMock.mockResolvedValue(health);

    const { result } = renderHook(() => useBackendHealth());

    await waitFor(() => expect(result.current.state).toEqual({ status: "CONNECTED", health }));
  });

  it("changes to DISCONNECTED after a failed request", async () => {
    getBackendHealthMock.mockRejectedValue(new ApiRequestError("offline", "network"));

    const { result } = renderHook(() => useBackendHealth());

    await waitFor(() =>
      expect(result.current.state).toEqual({ status: "DISCONNECTED", error: "offline" }),
    );
  });

  it("does not connect when the health service rejects an invalid response", async () => {
    getBackendHealthMock.mockRejectedValue(
      new ApiRequestError("invalid health payload", "invalid-response"),
    );

    const { result } = renderHook(() => useBackendHealth());

    await waitFor(() => expect(result.current.state.status).toBe("DISCONNECTED"));
    expect(result.current.state).not.toMatchObject({ status: "CONNECTED" });
  });

  it("retries and recovers to CONNECTED", async () => {
    getBackendHealthMock
      .mockRejectedValueOnce(new ApiRequestError("offline", "network"))
      .mockResolvedValueOnce(health);

    const { result } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(result.current.state.status).toBe("DISCONNECTED"));

    await act(async () => result.current.retry());

    await waitFor(() => expect(result.current.state).toEqual({ status: "CONNECTED", health }));
    expect(getBackendHealthMock).toHaveBeenCalledTimes(2);
  });

  it("stays DISCONNECTED when a retry fails", async () => {
    getBackendHealthMock
      .mockRejectedValueOnce(new ApiRequestError("offline", "network"))
      .mockRejectedValueOnce(new ApiRequestError("still offline", "network"));

    const { result } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(result.current.state.status).toBe("DISCONNECTED"));
    await act(async () => result.current.retry());

    await waitFor(() =>
      expect(result.current.state).toEqual({ status: "DISCONNECTED", error: "still offline" }),
    );
  });

  it("aborts the previous request when retry starts a new one", async () => {
    const firstRequest = deferred<BackendHealth>();
    getBackendHealthMock
      .mockImplementationOnce((_signal) => {
        return firstRequest.promise;
      })
      .mockResolvedValueOnce(health);

    const { result } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(getBackendHealthMock).toHaveBeenCalledTimes(1));
    const signal = getBackendHealthMock.mock.calls[0]?.[0];
    expect(signal).toBeInstanceOf(AbortSignal);

    await act(async () => result.current.retry());

    expect(signal?.aborted).toBe(true);
    await waitFor(() => expect(result.current.state.status).toBe("CONNECTED"));
    await act(async () => firstRequest.resolve({ ...health, version: "stale" }));
    expect(result.current.state).toEqual({ status: "CONNECTED", health });
  });

  it("aborts its request when the hook unmounts", async () => {
    const lateRequest = deferred<BackendHealth>();
    getBackendHealthMock.mockReturnValue(lateRequest.promise);

    const { result, unmount } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(getBackendHealthMock).toHaveBeenCalledTimes(1));
    const signal = getBackendHealthMock.mock.calls[0]?.[0];

    unmount();

    expect(signal?.aborted).toBe(true);
    await act(async () => lateRequest.resolve(health));
    expect(result.current.state).toEqual({ status: "CHECKING" });
  });

  it("ignores earlier results after repeated retries", async () => {
    const firstRequest = deferred<BackendHealth>();
    const secondRequest = deferred<BackendHealth>();
    getBackendHealthMock
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise)
      .mockResolvedValueOnce(health);

    const { result } = renderHook(() => useBackendHealth());
    await waitFor(() => expect(getBackendHealthMock).toHaveBeenCalledTimes(1));
    const firstSignal = getBackendHealthMock.mock.calls[0]?.[0];

    await act(async () => result.current.retry());
    await waitFor(() => expect(getBackendHealthMock).toHaveBeenCalledTimes(2));
    const secondSignal = getBackendHealthMock.mock.calls[1]?.[0];
    await act(async () => result.current.retry());

    expect(firstSignal?.aborted).toBe(true);
    expect(secondSignal?.aborted).toBe(true);
    await waitFor(() => expect(result.current.state).toEqual({ status: "CONNECTED", health }));
    expect(getBackendHealthMock).toHaveBeenCalledTimes(3);
  });
});
