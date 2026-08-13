"use client";

/**
 * Generate → Voice → Render → QC → Download (PHASE 15).
 *
 * §92 requires this chain to have no broken link, and the shape of this
 * component is the chain: five steps in order, each showing what it is waiting
 * for and what it produced.
 *
 * The steps are shown even before they are reachable, greyed rather than
 * hidden. A user who cannot find "Render" does not know whether it is missing
 * or merely not yet available — and §103 asks for a pipeline whose current
 * position is always legible.
 *
 * Nothing here waits on a request. Every action returns a job id and the panel
 * polls it (§26), so a six-minute render is a progress line rather than a
 * hung tab.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  creditsApi,
  generationApi,
  isJobFinished,
  type JobResponse,
  type QualityCheckResponse,
  type RenderResponse,
  type CostEstimateResponse,
  type ShotResponse,
  type StoryboardResponse,
  type VoiceoverResponse,
} from "@/lib/api/client";
import {
  batchProgress,
  failedJobs,
  summarize,
  useJobPollHandle,
} from "@/lib/jobs/use-job-poll";

type StepKey = "shots" | "voice" | "render" | "qc" | "download";

/** Everything this panel reads about a project's post-production. */
interface Snapshot {
  renders: RenderResponse[];
  checks: QualityCheckResponse[];
  voiceover: VoiceoverResponse | null;
  /** §95's quote. Null when credits are off — then there is nothing to show. */
  estimate: CostEstimateResponse | null;
}

const EMPTY: Snapshot = {
  renders: [],
  checks: [],
  voiceover: null,
  estimate: null,
};

interface Props {
  workspaceId: string;
  projectId: string;
  storyboard: StoryboardResponse | null;
  shots: ShotResponse[];
  /** Reload the parent's project data — statuses change as jobs finish. */
  onRefresh: () => void;
}

export function ProductionPanel({
  workspaceId,
  projectId,
  storyboard,
  shots,
  onRefresh,
}: Props) {
  const [data, setData] = useState<Snapshot>(EMPTY);
  const [busy, setBusy] = useState<StepKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const poll = useJobPollHandle(workspaceId);

  // Returns the data rather than storing it, so every caller decides whether
  // its result is still wanted — the mounting effect drops a response that
  // arrives after unmount, the refresh path does not need to.
  const fetchAll = useCallback(async (): Promise<Snapshot> => {
    const [renders, checks] = await Promise.all([
      generationApi.renders(workspaceId, projectId),
      generationApi.qualityChecks(workspaceId, projectId),
    ]);

    let voiceover: VoiceoverResponse | null = null;
    try {
      voiceover = await generationApi.voiceover(workspaceId, projectId);
    } catch (cause) {
      // A 404 here is the normal state before narration exists, not a failure
      // worth showing anybody.
      if (!(cause instanceof ApiError && cause.status === 404)) throw cause;
    }

    let estimate: CostEstimateResponse | null = null;
    try {
      estimate = await creditsApi.estimate(workspaceId, projectId);
    } catch {
      // The quote is advisory. A failure to price the work should not stop
      // someone reading the state of a generation already under way.
      estimate = null;
    }

    return { renders, checks, voiceover, estimate };
  }, [workspaceId, projectId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const next = await fetchAll();
        if (!cancelled) setData(next);
      } catch (cause) {
        if (!cancelled) setError(describe(cause));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchAll]);

  const refreshAll = useCallback(() => {
    void (async () => {
      try {
        setData(await fetchAll());
      } catch (cause) {
        setError(describe(cause));
      }
    })();
    onRefresh();
  }, [fetchAll, onRefresh]);

  const run = useCallback(
    async (step: StepKey, operation: () => Promise<string[]>) => {
      setError(null);
      setBusy(step);
      try {
        const ids = await operation();
        if (ids.length > 0) poll.watch(ids, refreshAll);
        else refreshAll();
      } catch (cause) {
        setError(describe(cause));
      } finally {
        setBusy(null);
      }
    },
    [poll, refreshAll],
  );

  const { renders, checks, voiceover, estimate } = data;
  const approved = storyboard?.status === "APPROVED";
  const readyShots = shots.filter((shot) => shot.status === "READY").length;
  const shotsDone = shots.length > 0 && readyShots === shots.length;
  const latestRender = renders[0] ?? null;
  const finishedRender =
    renders.find(
      (render) => render.status === "COMPLETED" && render.output_asset_id,
    ) ?? null;
  const renderChecks = useMemo(
    () =>
      finishedRender
        ? checks.filter((check) => check.render_id === finishedRender.id)
        : [],
    [checks, finishedRender],
  );

  const watching = poll.state.jobs;
  const stuck = failedJobs(watching);

  return (
    <section className="mt-12">
      <h2 className="text-sm font-medium tracking-wide uppercase">
        Production
      </h2>
      <p className="text-muted mt-1 text-xs">
        Each step queues background work and reports back. You can leave this
        page — progress is kept on the server.
      </p>

      {estimate && <CostSummary estimate={estimate} />}

      {error && (
        <p
          role="alert"
          className="border-border mt-4 rounded-md border px-3 py-2 text-sm"
        >
          {error}
        </p>
      )}

      {watching.length > 0 && (
        <ActiveJobs
          jobs={watching}
          pollError={poll.state.error}
          onCancel={async (jobId) => {
            await generationApi.cancelJob(workspaceId, jobId);
            refreshAll();
          }}
        />
      )}

      <ol className="mt-5 grid gap-3">
        <Step
          index={1}
          title="Generate the shots"
          available={approved}
          unavailableHint="Approve the storyboard first."
          done={shotsDone}
          detail={
            shots.length === 0
              ? "No shots yet."
              : `${readyShots} of ${shots.length} shots have a chosen take.`
          }
          actionLabel={shotsDone ? "Generate again" : "Generate"}
          busy={busy === "shots"}
          disabled={busy !== null || !storyboard}
          onAction={() =>
            void run("shots", async () => {
              if (!storyboard) return [];
              const jobs = await generationApi.generateShots(
                workspaceId,
                projectId,
                storyboard.id,
              );
              return jobs.map((job) => job.id);
            })
          }
        />

        <Step
          index={2}
          title="Record the narration"
          available={approved}
          unavailableHint="Approve the storyboard first."
          done={voiceover !== null}
          detail={
            voiceover
              ? `${voiceover.segments.length} lines · ${formatMs(voiceover.total_duration_ms)} · ${voiceover.provider}`
              : "Speech is synthesised per shot so subtitles land on the right line."
          }
          actionLabel={voiceover ? "Record again" : "Record"}
          busy={busy === "voice"}
          disabled={busy !== null}
          onAction={() =>
            void run("voice", async () => {
              const job = await generationApi.generateVoiceover(
                workspaceId,
                projectId,
              );
              return [job.id];
            })
          }
        />

        <Step
          index={3}
          title="Compose the video"
          available={shotsDone}
          unavailableHint="Every shot needs a chosen take before composing."
          done={finishedRender !== null}
          detail={
            latestRender
              ? `Version ${latestRender.version} · ${latestRender.status}` +
                (latestRender.duration_ms
                  ? ` · ${formatMs(latestRender.duration_ms)}`
                  : "") +
                (latestRender.error_message
                  ? ` · ${latestRender.error_message}`
                  : "")
              : "Clips, narration and subtitles are laid onto one timeline, then encoded once."
          }
          actionLabel={renders.length > 0 ? "Compose again" : "Compose"}
          busy={busy === "render"}
          disabled={busy !== null}
          onAction={() =>
            void run("render", async () => {
              const started = await generationApi.createRender(
                workspaceId,
                projectId,
              );
              return [started.job.id];
            })
          }
        />

        <Step
          index={4}
          title="Check the result"
          available={finishedRender !== null}
          unavailableHint="Compose the video first."
          done={renderChecks.length > 0}
          detail={
            renderChecks.length > 0
              ? renderChecks
                  .map(
                    (check) =>
                      `${check.check_type}: ${check.status} (${check.findings.length} finding${check.findings.length === 1 ? "" : "s"})`,
                  )
                  .join(" · ")
              : "Resolution, duration, audio and black frames are verified against the plan."
          }
          actionLabel={renderChecks.length > 0 ? "Check again" : "Run checks"}
          busy={busy === "qc"}
          disabled={busy !== null || !finishedRender}
          onAction={() =>
            void run("qc", async () => {
              if (!finishedRender) return [];
              const job = await generationApi.runQualityCheck(
                workspaceId,
                projectId,
                finishedRender.id,
              );
              return [job.id];
            })
          }
        />

        <Step
          index={5}
          title="Download"
          available={finishedRender !== null}
          unavailableHint="Nothing to download until a composition finishes."
          done={false}
          detail={
            finishedRender
              ? `Version ${finishedRender.version} · ${finishedRender.width}×${finishedRender.height}`
              : ""
          }
          actionLabel={downloading ? "Preparing…" : "Get the link"}
          busy={downloading}
          disabled={downloading || !finishedRender}
          onAction={() => {
            setError(null);
            setDownloading(true);
            void (async () => {
              try {
                const link = await generationApi.download(
                  workspaceId,
                  projectId,
                  finishedRender?.id,
                );
                // Fetched on click, never held in state: the URL expires, and
                // one rendered ten minutes ago would 403 on press.
                window.open(link.url, "_blank", "noopener,noreferrer");
              } catch (cause) {
                setError(describe(cause));
              } finally {
                setDownloading(false);
              }
            })();
          }}
        />
      </ol>

      {renderChecks.length > 0 && <Findings checks={renderChecks} />}

      {stuck.length > 0 && (
        <p className="border-border mt-4 rounded-md border px-3 py-2 text-xs">
          {stuck.length} job{stuck.length === 1 ? "" : "s"} failed
          {stuck[0]?.error_code ? ` (${stuck[0].error_code})` : ""}. Retries
          have already been attempted where the failure looked temporary —
          running the step again starts a fresh attempt.
        </p>
      )}
    </section>
  );
}

function CostSummary({ estimate }: { estimate: CostEstimateResponse }) {
  return (
    <div className="border-border mt-4 rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm">
          About {estimate.expected} credits to generate this
        </span>
        <span className="text-muted shrink-0 text-xs">
          {estimate.available} available
        </span>
      </div>

      <p className="text-muted mt-1 text-xs">
        Up to {estimate.maximum} is held while the work runs — providers round
        up and a retry costs again. Anything unused is returned.
      </p>

      {!estimate.affordable && (
        <p role="alert" className="mt-2 text-xs">
          That is more than this workspace has available. Add credits before
          starting, or the first shot will be refused.
        </p>
      )}

      {estimate.lines.length > 0 && (
        <dl className="mt-3 grid gap-1 text-xs">
          {estimate.lines.map((line) => (
            <div
              key={line.label}
              className="flex items-baseline justify-between gap-4"
            >
              <dt className="text-muted">{line.label}</dt>
              <dd className="shrink-0">{line.credits}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function ActiveJobs({
  jobs,
  pollError,
  onCancel,
}: {
  jobs: JobResponse[];
  pollError: string | null;
  onCancel: (jobId: string) => Promise<void>;
}) {
  const running = jobs.filter((job) => !isJobFinished(job));
  const percent = batchProgress(jobs);

  return (
    <div className="border-border mt-4 rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm">
          {running.length > 0
            ? `${running.length} job${running.length === 1 ? "" : "s"} running`
            : "All jobs finished"}
        </span>
        <span className="text-muted shrink-0 text-xs">{summarize(jobs)}</span>
      </div>

      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Generation progress"
        className="border-border mt-3 h-1.5 w-full overflow-hidden rounded-full border"
      >
        <div
          className="bg-foreground h-full"
          style={{ width: `${percent}%` }}
        />
      </div>

      {pollError && (
        <p className="text-muted mt-2 text-xs">{pollError} — still trying.</p>
      )}

      {running.length > 0 && (
        <ul className="mt-3 grid gap-1 text-xs">
          {running.map((job) => (
            <li key={job.id} className="flex items-baseline justify-between">
              <span className="text-muted">
                {job.job_type} · {job.status}
                {job.retry_count > 0 ? ` · attempt ${job.retry_count + 1}` : ""}
              </span>
              <button
                type="button"
                className="shrink-0 underline"
                onClick={() => void onCancel(job.id)}
              >
                Cancel
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Findings({ checks }: { checks: QualityCheckResponse[] }) {
  const findings = checks.flatMap((check) =>
    check.findings.map((finding) => ({
      ...(finding as { check?: string; status?: string; detail?: string }),
      id: `${check.id}-${String((finding as { check?: string }).check)}`,
    })),
  );
  const notable = findings.filter((finding) => finding.status !== "PASSED");
  if (notable.length === 0) {
    return (
      <p className="text-muted mt-4 text-xs">Every technical check passed.</p>
    );
  }

  return (
    <ul className="border-border mt-4 grid gap-1.5 rounded-xl border p-4 text-xs">
      {notable.map((finding) => (
        <li key={finding.id} className="grid grid-cols-[6rem_1fr] gap-2">
          <span className="text-muted">{finding.status}</span>
          <span>
            <span className="font-medium">{finding.check}: </span>
            {finding.detail}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Step({
  index,
  title,
  available,
  unavailableHint,
  done,
  detail,
  actionLabel,
  busy,
  disabled,
  onAction,
}: {
  index: number;
  title: string;
  available: boolean;
  unavailableHint: string;
  done: boolean;
  detail: string;
  actionLabel: string;
  busy: boolean;
  disabled: boolean;
  onAction: () => void;
}) {
  return (
    <li
      className={`border-border rounded-xl border p-4 ${available ? "" : "opacity-50"}`}
    >
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-medium">
          <span className="text-muted mr-2">{index}</span>
          {title}
          {done && <span className="text-muted ml-2 text-xs">done</span>}
        </h3>
        <button
          type="button"
          disabled={!available || disabled}
          className="border-border shrink-0 rounded-md border px-3 py-1.5 text-xs disabled:opacity-50"
          onClick={onAction}
        >
          {busy ? "Working…" : actionLabel}
        </button>
      </div>
      <p className="text-muted mt-1.5 text-xs">
        {available ? detail : unavailableHint}
      </p>
    </li>
  );
}

function formatMs(ms: number): string {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
