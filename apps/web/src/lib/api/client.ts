/**
 * Typed HTTP client for the AIPVS API.
 *
 * Every request and response type comes from `@aipvs/shared-types`, which is
 * generated from the API's OpenAPI document (taskbook §5.2). Hand-written
 * shapes are exactly the drift that pipeline exists to prevent, so nothing
 * here declares its own.
 *
 * Two rules this module enforces on behalf of every caller:
 *
 * 1. **The access token lives in memory, never in `localStorage`.** A token in
 *    web storage is readable by any injected script; keeping it in a module
 *    variable means an XSS bug has to run while the tab is open rather than
 *    harvesting a credential at leisure. The refresh token is an HttpOnly
 *    cookie the client never sees at all (§39).
 * 2. **A 401 triggers exactly one refresh attempt**, and concurrent callers
 *    share it. Without the sharing, five parallel requests hitting an expired
 *    token would fire five refreshes — and because refresh rotates, four of
 *    them would be rejected and log the user out.
 */

import type { ErrorResponse, Schemas } from "@aipvs/shared-types";
import { isErrorResponse } from "@aipvs/shared-types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type TokenResponse = Schemas["TokenResponse"];
export type UserResponse = Schemas["UserResponse"];
export type WorkspaceResponse = Schemas["WorkspaceResponse"];
export type MemberResponse = Schemas["MemberResponse"];
export type RegisterRequest = Schemas["RegisterRequest"];
export type LoginRequest = Schemas["LoginRequest"];

/** An error carrying the API's `code`, so callers branch on it rather than text. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, body: ErrorResponse) {
    super(body.error.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error.code;
    this.requestId = body.error.request_id ?? null;
    this.details = (body.error.details as Record<string, unknown>) ?? null;
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** In-flight refresh, shared so concurrent 401s do not each rotate the token. */
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) return null;
      const body = (await response.json()) as TokenResponse;
      setAccessToken(body.access_token);
      return body.access_token;
    } catch {
      return null;
    } finally {
      // Cleared in a microtask so callers awaiting this promise all observe
      // the same result before the next 401 can start a fresh attempt.
      queueMicrotask(() => {
        refreshInFlight = null;
      });
    }
  })();

  return refreshInFlight;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Internal: prevents a refresh loop when the refresh itself 401s. */
  retryOnUnauthorized?: boolean;
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (isErrorResponse(body)) {
    return new ApiError(response.status, body);
  }
  // A proxy or gateway can fail before ever reaching our handlers, so the
  // envelope is not guaranteed — synthesise one rather than throwing raw text.
  return new ApiError(response.status, {
    error: {
      code: "INTERNAL_ERROR",
      message: `Request failed with status ${response.status}.`,
      request_id: null,
    },
  } as ErrorResponse);
}

export async function apiRequest<T>(
  path: string,
  { method = "GET", body, retryOnUnauthorized = true }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const init: RequestInit = {
    method,
    headers,
    // Sends the HttpOnly refresh cookie; the access token travels in the
    // Authorization header rather than a cookie, keeping it clear of CSRF.
    credentials: "include",
  };
  // Assigned only when present: `exactOptionalPropertyTypes` distinguishes an
  // absent property from one explicitly set to `undefined`, and `RequestInit`
  // does not accept the latter.
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_URL}${path}`, init);

  if (response.status === 401 && retryOnUnauthorized) {
    const renewed = await refreshAccessToken();
    if (renewed) {
      return apiRequest<T>(path, { method, body, retryOnUnauthorized: false });
    }
    setAccessToken(null);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// --- Auth ------------------------------------------------------------------

export const authApi = {
  register: (payload: RegisterRequest) =>
    apiRequest<TokenResponse>("/api/v1/auth/register", {
      method: "POST",
      body: payload,
    }),

  login: (payload: LoginRequest) =>
    apiRequest<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: payload,
    }),

  logout: () =>
    apiRequest<void>("/api/v1/auth/logout", {
      method: "POST",
      retryOnUnauthorized: false,
    }),

  me: () => apiRequest<UserResponse>("/api/v1/auth/me"),

  /** Restore a session from the refresh cookie on a cold page load. */
  refresh: async (): Promise<string | null> => refreshAccessToken(),
};

// --- Workspaces ------------------------------------------------------------

export const workspaceApi = {
  list: () => apiRequest<WorkspaceResponse[]>("/api/v1/workspaces"),

  get: (id: string) =>
    apiRequest<WorkspaceResponse>(`/api/v1/workspaces/${id}`),

  create: (name: string) =>
    apiRequest<WorkspaceResponse>("/api/v1/workspaces", {
      method: "POST",
      body: { name },
    }),

  members: (id: string) =>
    apiRequest<MemberResponse[]>(`/api/v1/workspaces/${id}/members`),
};
