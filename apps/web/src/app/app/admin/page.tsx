"use client";

/**
 * Operator console (§99, PHASE 22).
 *
 * §99's acceptance names four numbers an operator must be able to see:
 * 生成量, 成功率, 成本, 失败率 — volume, success rate, cost, failure rate. They
 * are the first thing on the page, not buried under a navigation tree, because
 * the reason anybody opens this screen is to answer "is it working right now".
 *
 * **This page does not gate on permission.** The server answers 403, which is
 * the actual protection; a client-side check would only hide a link. What it
 * does do is render that 403 as a sentence rather than a stack trace, because
 * a staff member who has lost their flag should be told so.
 */

import { useCallback, useEffect, useState } from "react";

import { FailureState, LoadingState } from "@/components/states";
import {
  adminApi,
  type AdminJobResponse,
  type AnalyticsResponse,
  type ProviderConfigResponse,
  type PromptVersionResponse,
  type WorkspaceSummary,
} from "@/lib/api/client";

interface Console {
  analytics: AnalyticsResponse;
  providers: ProviderConfigResponse[];
  failures: AdminJobResponse[];
  workspaces: WorkspaceSummary[];
  prompts: PromptVersionResponse[];
}

type State =
  | { kind: "loading" }
  | { kind: "error"; error: unknown }
  | ({ kind: "ready" } & Console);

const WINDOWS = [1, 24, 168] as const;

export default function AdminPage() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [hours, setHours] = useState<number>(24);
  const [busy, setBusy] = useState<string | null>(null);

  const fetchAll = useCallback(async (): Promise<Console> => {
    const [analytics, providers, failures, workspaces, prompts] =
      await Promise.all([
        adminApi.analytics(hours),
        adminApi.providers(),
        adminApi.failedJobs(hours),
        adminApi.workspaces(),
        adminApi.prompts(),
      ]);
    return { analytics, providers, failures, workspaces, prompts };
  }, [hours]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchAll();
        if (!cancelled) setState({ kind: "ready", ...next });
      } catch (error) {
        if (!cancelled) setState({ kind: "error", error });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  const reload = useCallback(() => {
    void (async () => {
      try {
        setState({ kind: "ready", ...(await fetchAll()) });
      } catch (error) {
        setState({ kind: "error", error });
      }
    })();
  }, [fetchAll]);

  if (state.kind === "loading") {
    return (
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12">
        <LoadingState label="Loading the console…" />
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12">
        <h1 className="text-2xl font-semibold tracking-tight">Operations</h1>
        <div className="mt-6">
          <FailureState
            error={state.error}
            onRetry={reload}
            retryLabel="Retry"
          />
        </div>
      </main>
    );
  }

  const { analytics, providers, failures, workspaces, prompts } = state;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-12">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Operations</h1>
        <div className="flex shrink-0 gap-1">
          {WINDOWS.map((window) => (
            <button
              key={window}
              type="button"
              className={`border-border rounded-md border px-2.5 py-1 text-xs ${
                hours === window ? "ring-1" : ""
              }`}
              onClick={() => {
                setState({ kind: "loading" });
                setHours(window);
              }}
            >
              {window === 1 ? "1h" : window === 24 ? "24h" : "7d"}
            </button>
          ))}
        </div>
      </div>

      {/* --- §99's four numbers --- */}
      <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Generations" value={String(analytics.total_jobs)} />
        <Stat
          label="Success rate"
          value={
            analytics.total_jobs
              ? `${Math.round(analytics.success_rate * 100)}%`
              : "—"
          }
        />
        <Stat
          label="Failure rate"
          value={
            analytics.total_jobs
              ? `${Math.round(analytics.failure_rate * 100)}%`
              : "—"
          }
        />
        <Stat label="Credits spent" value={String(analytics.total_spend)} />
      </section>

      <p className="text-muted mt-2 text-xs">
        Last {analytics.window_hours}h across {analytics.workspaces_active}{" "}
        active workspace{analytics.workspaces_active === 1 ? "" : "s"}. A rate
        of &ldquo;—&rdquo; means no jobs ran, which is not the same as 0%.
      </p>

      {/* --- providers (P22-T05, P19-T07) --- */}
      <Section title="Providers">
        <ul className="grid gap-2">
          {providers.map((provider) => {
            const open =
              provider.circuit_open_until !== null &&
              new Date(provider.circuit_open_until) > new Date();
            return (
              <li
                key={provider.id}
                className="border-border flex items-baseline justify-between gap-4 rounded-lg border px-3 py-2"
              >
                <span className="text-sm">
                  {provider.provider}
                  <span className="text-muted ml-2 text-xs">
                    {provider.model || "—"} · priority {provider.priority}
                    {provider.attempts > 0 &&
                      ` · ${Math.round(provider.failure_rate * 100)}% failing over ${provider.attempts}`}
                    {open && " · breaker open"}
                  </span>
                </span>
                <button
                  type="button"
                  disabled={busy !== null}
                  className="border-border shrink-0 rounded-md border px-2.5 py-1 text-xs disabled:opacity-50"
                  onClick={() => {
                    setBusy(provider.provider);
                    void adminApi
                      .setProviderEnabled(provider.provider, !provider.enabled)
                      .then(reload)
                      .finally(() => setBusy(null));
                  }}
                >
                  {provider.enabled ? "Disable" : "Enable"}
                </button>
              </li>
            );
          })}
        </ul>
        <p className="text-muted mt-2 text-xs">
          Enabling also clears the circuit breaker — turning a provider back on
          means &ldquo;try this again now&rdquo;.
        </p>
      </Section>

      {/* --- per-provider usage (P22-T07) --- */}
      {analytics.by_provider.length > 0 && (
        <Section title="Spend by provider">
          <table className="w-full text-xs">
            <thead className="text-muted text-left">
              <tr>
                <th className="py-1 font-normal">Provider</th>
                <th className="py-1 text-right font-normal">Jobs</th>
                <th className="py-1 text-right font-normal">Success</th>
                <th className="py-1 text-right font-normal">Credits</th>
              </tr>
            </thead>
            <tbody>
              {analytics.by_provider.map((row) => (
                <tr key={row.provider} className="border-border border-t">
                  <td className="py-1.5">{row.provider}</td>
                  <td className="py-1.5 text-right">{row.total}</td>
                  <td className="py-1.5 text-right">
                    {Math.round(row.success_rate * 100)}%
                  </td>
                  <td className="py-1.5 text-right">{row.spend}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* --- failures (P22-T04) --- */}
      <Section title={`Failures (${failures.length})`}>
        {failures.length === 0 ? (
          <p className="text-muted text-xs">
            Nothing failed in the last {hours}h.
          </p>
        ) : (
          <ul className="grid gap-1 text-xs">
            {failures.slice(0, 20).map((job) => (
              <li
                key={job.id}
                className="flex items-baseline justify-between gap-4"
              >
                <span>
                  {job.job_type} · {job.provider}
                  {job.retry_count > 0 && ` · ${job.retry_count} retries`}
                </span>
                <span className="text-muted shrink-0">
                  {job.error_code ?? job.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* --- workspaces (P22-T02) --- */}
      <Section title="Workspaces">
        <table className="w-full text-xs">
          <thead className="text-muted text-left">
            <tr>
              <th className="py-1 font-normal">Name</th>
              <th className="py-1 font-normal">Plan</th>
              <th className="py-1 text-right font-normal">Jobs</th>
              <th className="py-1 text-right font-normal">Spent</th>
            </tr>
          </thead>
          <tbody>
            {workspaces.slice(0, 25).map((workspace) => (
              <tr key={workspace.id} className="border-border border-t">
                <td className="py-1.5">{workspace.name}</td>
                <td className="py-1.5">{workspace.plan_code}</td>
                <td className="py-1.5 text-right">{workspace.job_count}</td>
                <td className="py-1.5 text-right">{workspace.spend}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {/* --- prompt registry (P22-T06) --- */}
      <Section title="Prompt registry">
        <ul className="grid gap-1 text-xs">
          {prompts.map((prompt) => (
            <li
              key={`${prompt.key}-${prompt.version}`}
              className="flex items-baseline justify-between gap-4"
            >
              <span>
                {prompt.key}{" "}
                <span className="text-muted">v{prompt.version}</span>
              </span>
              <span className="text-muted shrink-0">
                {prompt.active ? "active" : "superseded"} · {prompt.characters}{" "}
                chars
              </span>
            </li>
          ))}
        </ul>
        <p className="text-muted mt-2 text-xs">
          Read-only. A version is immutable once shipped (§15) — a job records
          which one it sent, and editing that text would change what a recorded
          call claims to have done.
        </p>
      </Section>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-border rounded-xl border px-3 py-3">
      <div className="text-muted text-xs">{label}</div>
      <div className="mt-1 text-xl font-medium tabular-nums">{value}</div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-10">
      <h2 className="text-sm font-medium tracking-wide uppercase">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}
