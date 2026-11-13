import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "../../app/App";
import { ApiRequestError } from "../../services/api";
import { getBackendHealth } from "../../services/healthService";
import type { BackendHealth } from "../../types/health";

vi.mock("../../services/healthService", () => ({
  getBackendHealth: vi.fn(),
}));

const health: BackendHealth = {
  status: "ok",
  application: "CodeAtlas",
  version: "0.1.0",
};

const getBackendHealthMock = vi.mocked(getBackendHealth);

describe("Overview application interface", () => {
  beforeEach(() => {
    getBackendHealthMock.mockReset();
    getBackendHealthMock.mockReturnValue(new Promise(() => undefined));
  });

  it("shows CHECKING and preserves the existing application shell", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Checking Flask connection…" })).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "CodeAtlas · v0.1.0" })).toBeNull();
    expect(screen.getByRole("complementary")).not.toBeNull();
    expect(screen.getByRole("banner")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "Understand how your codebase connects." })).not.toBeNull();
    expect(screen.getByText("No project analyzed")).not.toBeNull();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).not.toBeNull();
    for (const item of ["Project Explorer", "Graph Explorer", "Function Search", "Function Trace", "Settings"]) {
      expect(screen.getByText(item)).not.toBeNull();
    }
  });

  it("shows the application name and version when connected", async () => {
    getBackendHealthMock.mockResolvedValue(health);

    render(<App />);

    expect(await screen.findByRole("heading", { name: "CodeAtlas · v0.1.0" })).not.toBeNull();
  });

  it("shows a safe error and retries after the user clicks Retry", async () => {
    getBackendHealthMock
      .mockRejectedValueOnce(new ApiRequestError("Backend unavailable. Try again.", "network"))
      .mockResolvedValueOnce(health);
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Backend unavailable" })).not.toBeNull();
    expect(screen.getByText("Backend unavailable. Try again.")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Retry connection" }));

    await waitFor(() => expect(getBackendHealthMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("heading", { name: "CodeAtlas · v0.1.0" })).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Retry connection" })).toBeNull();
  });
});
