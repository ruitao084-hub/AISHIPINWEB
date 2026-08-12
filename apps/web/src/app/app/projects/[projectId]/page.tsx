"use client";

/**
 * Project detail: choose a creative direction, then read the script (P7-T03).
 *
 * Two things on this screen are load-bearing rather than decorative.
 *
 * **`risk_notes` is shown on every plan, not hidden behind a disclosure.**
 * §16 puts it there for the model to flag a direction that leans on something
 * unverified, and a warning nobody reads before choosing is not a warning.
 *
 * **The script names the claims behind it.** §109 lets only approved claims
 * reach a script, and showing the count is how a reader can tell the
 * difference between "nothing was claimed" and "the filter silently dropped
 * everything" — two very different situations that look identical in the text.
 */

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  projectApi,
  workspaceApi,
  type CreativePlanResponse,
  type ProjectResponse,
  type ScriptResponse,
} from "@/lib/api/client";

interface Loaded {
  workspaceId: string;
  project: ProjectResponse;
  plans: CreativePlanResponse[];
  scripts: ScriptResponse[];
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | ({ kind: "ready" } & Loaded);

interface ScriptSectionView {
  section: string;
  narration: string;
  visual: string;
}

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const [projectId, setProjectId] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);
  // Both generation calls are billed, so their triggers stay disabled while
  // one is in flight.
  const [busy, setBusy] = useState<null | "plans" | "script">(null);

  useEffect(() => {
    void params.then(({ projectId: id }) => setProjectId(id));
  }, [params]);

  const load = useCallback(async (id: string): Promise<Loaded> => {
    const workspaces = await workspaceApi.list();
    const workspace = workspaces[0];
    if (!workspace) throw new Error("You do not belong to a workspace yet.");

    const [project, plans, scripts] = await Promise.all([
      projectApi.get(workspace.id, id),
      projectApi.plans(workspace.id, id),
      projectApi.scripts(workspace.id, id),
    ]);
    return { workspaceId: workspace.id, project, plans, scripts };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await load(projectId);
        if (!cancelled) setState({ kind: "ready", ...loaded });
      } catch (error) {
        if (!cancelled) setState({ kind: "error", message: describe(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, load]);

  const act = useCallback(
    async (operation: () => Promise<unknown>, kind?: "plans" | "script") => {
      setActionError(null);
      if (kind) setBusy(kind);
      try {
        await operation();
        if (projectId) setState({ kind: "ready", ...(await load(projectId)) });
      } catch (error) {
        // The server's message is specific — "this product has no verified
        // facts yet" tells the user what to do, which a generic notice cannot.
        setActionError(describe(error));
      } finally {
        setBusy(null);
      }
    },
    [projectId, load],
  );

  if (state.kind === "loading") {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <p className="text-muted text-sm" role="status">
          Loading…
        </p>
      </main>
    );
  }

  if (state.kind === "error") {
    return (
      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
        <p
          role="alert"
          className="border-border rounded-md border px-3 py-2 text-sm"
        >
          {state.message}
        </p>
      </main>
    );
  }

  const { workspaceId, project, plans, scripts } = state;
  const latestVersion =
    plans.length > 0 ? Math.max(...plans.map((p) => p.version)) : 0;
  const currentPlans = plans.filter((plan) => plan.version === latestVersion);
  const selected = plans.find((plan) => plan.selected);
  const latestScript = scripts[0];

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.name}
        </h1>
        <span className="text-muted shrink-0 text-xs tracking-wide uppercase">
          {project.status}
        </span>
      </div>
      <p className="text-muted mt-1 text-sm">
        {project.duration_seconds}s · {project.aspect_ratio} ·{" "}
        {project.target_platform} · {project.style}
      </p>

      {project.failure_reason && (
        <p
          role="alert"
          className="border-border mt-6 rounded-md border px-3 py-2 text-sm"
        >
          {project.failure_reason}
        </p>
      )}

      {actionError && (
        <p
          role="alert"
          className="border-border mt-6 rounded-md border px-3 py-2 text-sm"
        >
          {actionError}
        </p>
      )}

      {/* --- creative plans (§16) --- */}
      <section className="mt-10">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-sm font-medium tracking-wide uppercase">
            Creative direction
          </h2>
          <button
            type="button"
            disabled={busy !== null}
            className="border-border rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
            onClick={() =>
              void act(
                () => projectApi.generatePlans(workspaceId, project.id),
                "plans",
              )
            }
          >
            {busy === "plans"
              ? "Generating…"
              : plans.length > 0
                ? "Generate new options"
                : "Generate three options"}
          </button>
        </div>
        <p className="text-muted mt-1 text-xs">
          Built only from this product&rsquo;s verified facts and approved
          claims. Choose one before writing the script.
        </p>

        {currentPlans.length > 0 && (
          <ul className="mt-4 grid gap-3">
            {currentPlans.map((plan) => (
              <PlanCard
                key={plan.id}
                plan={plan}
                onSelect={() =>
                  void act(() =>
                    projectApi.selectPlan(workspaceId, project.id, plan.id),
                  )
                }
              />
            ))}
          </ul>
        )}
      </section>

      {/* --- script (§17) --- */}
      {selected && (
        <section className="mt-12">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="text-sm font-medium tracking-wide uppercase">
              Script
            </h2>
            <button
              type="button"
              disabled={busy !== null}
              className="border-border rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
              onClick={() =>
                void act(
                  () => projectApi.generateScript(workspaceId, project.id),
                  "script",
                )
              }
            >
              {busy === "script"
                ? "Writing…"
                : latestScript
                  ? "Write another version"
                  : "Write the script"}
            </button>
          </div>

          {latestScript && (
            <ScriptView
              script={latestScript}
              targetSeconds={project.duration_seconds}
              onApprove={() =>
                void act(() =>
                  projectApi.approveScript(
                    workspaceId,
                    project.id,
                    latestScript.id,
                  ),
                )
              }
            />
          )}

          {scripts.length > 1 && (
            <p className="text-muted mt-3 text-xs">
              {scripts.length} versions kept. Earlier ones are never
              overwritten.
            </p>
          )}
        </section>
      )}
    </main>
  );
}

function PlanCard({
  plan,
  onSelect,
}: {
  plan: CreativePlanResponse;
  onSelect: () => void;
}) {
  return (
    <li
      className={`border-border rounded-xl border p-4 ${
        plan.selected ? "ring-1" : ""
      }`}
    >
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-medium">{plan.title}</h3>
        {plan.selected ? (
          <span className="text-muted shrink-0 text-xs">chosen</span>
        ) : (
          <button
            type="button"
            className="shrink-0 text-xs underline"
            onClick={onSelect}
          >
            Choose this
          </button>
        )}
      </div>

      <p className="text-muted mt-2 text-sm">{plan.concept}</p>

      <dl className="mt-3 grid gap-1.5 text-xs">
        <Row label="Hook" value={plan.hook} />
        <Row label="Message" value={plan.core_message} />
        <Row label="Structure" value={plan.narrative_structure} />
        <Row label="Visuals" value={plan.visual_direction} />
        <Row label="Camera" value={plan.camera_direction} />
        <Row label="Music" value={plan.music_direction} />
        <Row label="Ending" value={plan.ending_cta} />
      </dl>

      {plan.risk_notes && (
        <p className="border-border mt-3 rounded-md border px-2 py-1.5 text-xs">
          <span className="font-medium">Check before filming: </span>
          {plan.risk_notes}
        </p>
      )}
    </li>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[5rem_1fr] gap-2">
      <dt className="text-muted">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function ScriptView({
  script,
  targetSeconds,
  onApprove,
}: {
  script: ScriptResponse;
  targetSeconds: number;
  onApprove: () => void;
}) {
  const sections = (script.content_json.sections ??
    []) as unknown as ScriptSectionView[];
  const estimated = script.estimated_duration_seconds ?? 0;
  // The same 35% tolerance the server applies. Over-length is worth saying out
  // loud: §17 budgets words against duration, and a script that runs long gets
  // cut by somebody — better them than the renderer.
  const overBudget =
    targetSeconds > 0 &&
    Math.abs(estimated - targetSeconds) / targetSeconds > 0.35;

  return (
    <div className="border-border mt-4 grid gap-3 rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm">
          Version {script.version} · {script.status}
        </span>
        {script.status !== "APPROVED" && (
          <button
            type="button"
            className="shrink-0 text-xs underline"
            onClick={onApprove}
          >
            Approve this version
          </button>
        )}
      </div>

      <p className="text-muted text-xs">
        About {estimated}s of narration against a {targetSeconds}s target
        {overBudget ? " — worth trimming before filming" : ""}. Built from{" "}
        {script.sourced_claim_ids.length} approved claim
        {script.sourced_claim_ids.length === 1 ? "" : "s"}.
      </p>

      <ol className="grid gap-2">
        {sections.map((section) => (
          <li key={section.section} className="grid gap-0.5">
            <span className="text-muted text-xs tracking-wide uppercase">
              {section.section.replaceAll("_", " ")}
            </span>
            {section.narration ? (
              <span className="text-sm">{section.narration}</span>
            ) : (
              <span className="text-muted text-sm italic">
                No narration — visual only
              </span>
            )}
            {section.visual && (
              <span className="text-muted text-xs">{section.visual}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
