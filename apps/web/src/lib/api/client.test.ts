import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, getAccessToken, setAccessToken } from "./client";

const API_URL = "http://localhost:8000";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function errorBody(code: string, message = "nope") {
  return { error: { code, message, request_id: "req-1" } };
}

describe("apiRequest", () => {
  beforeEach(() => {
    setAccessToken(null);
    vi.restoreAllMocks();
  });

  afterEach(() => {
    setAccessToken(null);
  });

  it("attaches the bearer token when one is set", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, { ok: true }));
    setAccessToken("token-abc");

    await apiRequest("/api/v1/auth/me");

    const init = fetchMock.mock.calls[0]?.[1];
    const headers = init?.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer token-abc");
  });

  it("omits the Authorization header when signed out", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, {}));

    await apiRequest("/api/v1/workspaces");

    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<
      string,
      string
    >;
    expect(headers["Authorization"]).toBeUndefined();
  });

  it("sends credentials so the HttpOnly refresh cookie travels", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(200, {}));

    await apiRequest("/api/v1/workspaces");

    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe("include");
  });

  it("throws an ApiError carrying the API's error code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(404, errorBody("PRODUCT_NOT_FOUND", "Workspace not found.")),
    );

    await expect(apiRequest("/api/v1/workspaces/x")).rejects.toMatchObject({
      code: "PRODUCT_NOT_FOUND",
      status: 404,
      message: "Workspace not found.",
      requestId: "req-1",
    });
  });

  it("synthesises an envelope when a gateway responds without one", async () => {
    // A proxy can fail before reaching our handlers, so the shape is not
    // guaranteed — callers must still get an ApiError, not raw text.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>502</html>", { status: 502 }),
    );

    const error = await apiRequest("/api/v1/workspaces").catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("INTERNAL_ERROR");
  });

  it("returns undefined for 204 rather than parsing an empty body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await expect(
      apiRequest("/api/v1/auth/logout", { method: "POST" }),
    ).resolves.toBeUndefined();
  });

  describe("401 handling", () => {
    it("refreshes once and replays the original request", async () => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(
          jsonResponse(401, errorBody("AUTH_UNAUTHORIZED")),
        )
        .mockResolvedValueOnce(jsonResponse(200, { access_token: "fresh" }))
        .mockResolvedValueOnce(jsonResponse(200, { id: "u1" }));

      const result = await apiRequest<{ id: string }>("/api/v1/auth/me");

      expect(result).toEqual({ id: "u1" });
      expect(fetchMock.mock.calls[1]?.[0]).toBe(
        `${API_URL}/api/v1/auth/refresh`,
      );
      expect(getAccessToken()).toBe("fresh");
    });

    it("does not loop when the refresh itself fails", async () => {
      const fetchMock = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValueOnce(
          jsonResponse(401, errorBody("AUTH_UNAUTHORIZED")),
        )
        .mockResolvedValueOnce(
          jsonResponse(401, errorBody("AUTH_UNAUTHORIZED")),
        );

      await expect(apiRequest("/api/v1/auth/me")).rejects.toBeInstanceOf(
        ApiError,
      );
      // Original + refresh only. A third call would mean an infinite loop.
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(getAccessToken()).toBeNull();
    });

    it("shares one refresh across concurrent 401s", async () => {
      // Refresh rotates the token server-side, so parallel refreshes would
      // invalidate each other and sign the user out.
      let refreshCalls = 0;
      vi.spyOn(globalThis, "fetch").mockImplementation(
        async (input: RequestInfo | URL) => {
          const url = String(input);
          if (url.endsWith("/auth/refresh")) {
            refreshCalls += 1;
            return jsonResponse(200, { access_token: "fresh" });
          }
          return getAccessToken() === "fresh"
            ? jsonResponse(200, { ok: true })
            : jsonResponse(401, errorBody("AUTH_UNAUTHORIZED"));
        },
      );

      await Promise.all([
        apiRequest("/api/v1/workspaces"),
        apiRequest("/api/v1/auth/me"),
        apiRequest("/api/v1/workspaces"),
      ]);

      expect(refreshCalls).toBe(1);
    });
  });
});
