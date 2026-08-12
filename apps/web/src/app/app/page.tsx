"use client";

/**
 * Workspace overview.
 *
 * A placeholder dashboard for PHASE 3 — enough to prove the authenticated
 * round trip works end to end. The real dashboard (§44: recent projects, jobs
 * in flight, failures, credits) arrives in PHASE 15 once there is something to
 * put on it.
 */

import { useEffect, useState } from "react";

import {
  ApiError,
  workspaceApi,
  type WorkspaceResponse,
} from "@/lib/api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; workspaces: WorkspaceResponse[] };

export default function AppDashboardPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      try {
        const workspaces = await workspaceApi.list();
        if (!cancelled) setState({ kind: "ready", workspaces });
      } catch (error) {
        if (cancelled) return;
        // §51 — an error state the user can actually read, not a console log.
        setState({
          kind: "error",
          message:
            error instanceof ApiError
              ? error.message
              : "Could not load your workspaces.",
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Workspaces</h1>

      {state.kind === "loading" && (
        <p className="text-muted mt-6 text-sm" role="status">
          Loading…
        </p>
      )}

      {state.kind === "error" && (
        <p
          role="alert"
          className="border-border mt-6 rounded-md border px-3 py-2 text-sm"
        >
          {state.message}
        </p>
      )}

      {state.kind === "ready" && state.workspaces.length === 0 && (
        <p className="text-muted mt-6 text-sm">
          You do not belong to a workspace yet.
        </p>
      )}

      {state.kind === "ready" && state.workspaces.length > 0 && (
        <ul className="mt-6 grid gap-3">
          {state.workspaces.map((workspace) => (
            <li
              key={workspace.id}
              className="border-border rounded-xl border p-4"
            >
              <div className="flex items-baseline justify-between gap-4">
                <h2 className="font-medium">{workspace.name}</h2>
                <span className="text-muted text-xs tracking-wide uppercase">
                  {workspace.role}
                </span>
              </div>
              <p className="text-muted mt-1 font-mono text-xs">
                {workspace.slug}
              </p>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
