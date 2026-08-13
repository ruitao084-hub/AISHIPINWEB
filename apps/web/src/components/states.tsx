"use client";

/**
 * Empty, failure and retry states (P16-T05, P16-T06, P16-T07).
 *
 * Three components because these are three different situations that a single
 * "nothing here" box conflates, and conflating them is the actual bug:
 *
 * - **Empty** — nothing exists yet, and there is a next step. The next step is
 *   the whole point; a screen that says "No products" and stops has told the
 *   user what they could already see.
 * - **Failure** — something went wrong. §41's envelope carries an error code
 *   and a request id, and showing both is what turns "it broke" into a support
 *   conversation someone can actually resolve.
 * - **Retry** — a failure the user can act on themselves. Separate from
 *   Failure because a retry button on an unretryable error (a rejected prompt,
 *   an expired plan) invites someone to press it repeatedly and get the same
 *   answer.
 *
 * §41 also decides what these render: the message comes from the server, not
 * from a lookup table here. The server's "this product has no verified facts
 * yet" tells a user what to do; a client-side "Something went wrong" does not.
 */

import type { ReactNode } from "react";

import { ApiError } from "@/lib/api/client";

/** Error codes a user can resolve by pressing the same button again (§24). */
const RETRYABLE_CODES = new Set([
  "PROVIDER_UNAVAILABLE",
  "PROVIDER_RATE_LIMITED",
  "JOB_TIMEOUT",
  "INTERNAL_ERROR",
  "STORAGE_ERROR",
]);

export function isRetryable(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    // A network failure with no envelope: the request never reached us, which
    // is the most retryable kind of failure there is.
    return true;
  }
  return RETRYABLE_CODES.has(error.code) || error.status >= 500;
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}

export function requestIdOf(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null;
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  /** The next step. Omitted only when there genuinely is not one. */
  action?: ReactNode;
}) {
  return (
    <div className="border-border grid gap-2 rounded-xl border border-dashed px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="text-muted mx-auto max-w-prose text-xs">{description}</p>
      {action && <div className="mt-2 flex justify-center">{action}</div>}
    </div>
  );
}

export function FailureState({
  error,
  onRetry,
  retryLabel = "Try again",
}: {
  error: unknown;
  /** Offered only when the failure is one a retry could resolve. */
  onRetry?: (() => void) | undefined;
  retryLabel?: string;
}) {
  const requestId = requestIdOf(error);
  const canRetry = onRetry !== undefined && isRetryable(error);

  return (
    <div
      role="alert"
      className="border-border grid gap-2 rounded-xl border px-4 py-3"
    >
      <p className="text-sm">{describeError(error)}</p>

      {!canRetry && onRetry !== undefined && (
        <p className="text-muted text-xs">
          Running this again would produce the same result — change something
          first.
        </p>
      )}

      <div className="flex items-center justify-between gap-4">
        {requestId ? (
          <span className="text-muted font-mono text-[0.65rem]">
            {requestId}
          </span>
        ) : (
          <span />
        )}
        {canRetry && (
          <button
            type="button"
            className="border-border shrink-0 rounded-md border px-3 py-1 text-xs"
            onClick={onRetry}
          >
            {retryLabel}
          </button>
        )}
      </div>
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="text-muted text-sm" role="status" aria-live="polite">
      {label}
    </p>
  );
}
