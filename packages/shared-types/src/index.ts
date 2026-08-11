/**
 * Shared types between the web app and the HTTP API.
 *
 * Taskbook §5.2 makes the FastAPI OpenAPI document the single source of truth
 * for the HTTP contract. Everything below is derived from it — `src/generated`
 * is produced by `pnpm generate` and must never be hand-edited. CI regenerates
 * and fails on any diff, so a route whose shape changed cannot reach the web
 * app as a silent runtime error.
 *
 * Regenerate after any API change:
 *
 * ```bash
 * make contract
 * ```
 */

import type { components, operations, paths } from "./generated/api";

export type { components, operations, paths };

/** Response and request bodies, keyed by their Pydantic model name. */
export type Schemas = components["schemas"];

/** The uniform error envelope every failing endpoint returns (§41). */
export type ErrorResponse = Schemas["ErrorResponse"];

/** The error object inside that envelope. */
export type ErrorDetail = Schemas["ErrorDetail"];

/**
 * The closed set of error codes (§65).
 *
 * Clients branch on this rather than on HTTP status or message text: the
 * status is coarse and the message is for humans.
 */
export type ErrorCode = Schemas["ErrorCode"];

/** Liveness payload (§70). */
export type HealthResponse = Schemas["HealthResponse"];

/** Readiness payload, including per-dependency status (§70). */
export type ReadyResponse = Schemas["ReadyResponse"];

/** API version and enabled feature flags (§122). */
export type ApiInfo = Schemas["ApiInfo"];

/** Every path the API serves. */
export type ApiPath = keyof paths;

/**
 * Narrow an unknown response body to the error envelope.
 *
 * Written as a type guard rather than a cast because an error body can arrive
 * from anywhere in the stack — including a proxy that never saw our handlers.
 */
export function isErrorResponse(value: unknown): value is ErrorResponse {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const { error } = value as { error: unknown };
  return (
    typeof error === "object" &&
    error !== null &&
    "code" in error &&
    "message" in error
  );
}
