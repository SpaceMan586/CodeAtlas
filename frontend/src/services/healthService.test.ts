import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "./api";
import { getBackendHealth } from "./healthService";
import { jsonResponse } from "../test/responses";

const health = {
  status: "ok",
  application: "CodeAtlas",
  version: "0.1.0",
} as const;

describe("getBackendHealth", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("returns a valid health response", async () => {
    fetchMock.mockResolvedValue(jsonResponse(health));

    await expect(getBackendHealth()).resolves.toEqual(health);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/health",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it.each([
    ["missing status", { application: "CodeAtlas", version: "0.1.0" }],
    ["missing application", { status: "ok", version: "0.1.0" }],
    ["missing version", { status: "ok", application: "CodeAtlas" }],
    ["incorrect status", { ...health, status: "ready" }],
    ["incorrect application type", { ...health, application: 7 }],
    ["incorrect version type", { ...health, version: null }],
    ["empty application", { ...health, application: "  " }],
    ["empty version", { ...health, version: "" }],
    ["unexpected object", { error: "not a health payload" }],
    ["unexpected array", [health]],
  ])("rejects a response with %s", async (_label, payload) => {
    fetchMock.mockResolvedValue(jsonResponse(payload));

    await expect(getBackendHealth()).rejects.toMatchObject({
      name: "ApiRequestError",
      kind: "invalid-response",
    });
  });

  it("preserves malformed-JSON errors from the API service", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => {
        throw new SyntaxError("bad JSON");
      },
    } as unknown as Response);

    const error = await getBackendHealth().catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({ kind: "invalid-json" });
  });
});
