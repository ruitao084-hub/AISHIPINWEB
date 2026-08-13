"use client";

/**
 * Poll a set of jobs until they finish (§26, P15).
 *
 * §26 is explicit that the MVP polls and that WebSockets are a PHASE 24
 * concern. This hook is that decision in one place, so no screen re-implements
 * the interval, the cleanup, or the "stop when everything is terminal" rule.
 *
 * Three details are deliberate:
 *
 * **The interval backs off.** A render takes minutes; asking every two seconds
 * for six minutes is 180 requests to learn one fact. The delay grows from two
 * seconds to fifteen, which keeps the first few checks responsive — most mock
 * jobs finish inside them — without a long job costing hundreds of round
 * trips.
 *
 * **Polling stops at terminal states.** A finished job never changes again
 * (§106), so continuing to ask is pure load.
 *
 * **A failed poll does not stop the loop.** A dropped request during a deploy
 * would otherwise strand the UI on a spinner forever; the next tick recovers.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  generationApi,
  isJobFinished,
  type JobResponse,
} from "@/lib/api/client";

const FIRST_DELAY_MS = 2_000;
const MAX_DELAY_MS = 15_000;

export interface JobPollState {
  /** Latest known state of every watched job, in the order given. */
  jobs: JobResponse[];
  /** True while at least one job is still running. */
  active: boolean;
  /** Set when the last poll failed; cleared by the next success. */
  error: string | null;
}

/**
 * A poll result tagged with the job set it belongs to.
 *
 * Tagged rather than cleared on change, so switching to a new set of jobs
 * shows nothing instead of briefly showing the previous set's progress — and
 * so the hook never has to write state from an effect body to reset itself.
 */
interface Snapshot {
  key: string;
  jobs: JobResponse[];
  error: string | null;
}

const EMPTY: Snapshot = { key: "", jobs: [], error: null };

/**
 * Watch `jobIds` until each reaches a terminal state.
 *
 * `onSettled` fires once, after the last job finishes — that is where a screen
 * reloads whatever the jobs produced.
 */
export function useJobPoll(
  workspaceId: string | null,
  jobIds: readonly string[],
  onSettled?: () => void,
): JobPollState {
  const [snapshot, setSnapshot] = useState<Snapshot>(EMPTY);

  // Held in a ref, and written from an effect rather than during render, so a
  // caller passing an inline arrow does not restart the interval every render.
  const settled = useRef<(() => void) | undefined>(undefined);
  useEffect(() => {
    settled.current = onSettled;
  }, [onSettled]);

  const key = jobIds.join(",");

  useEffect(() => {
    if (!workspaceId || key === "") return;

    const ids = key.split(",");
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = FIRST_DELAY_MS;

    const tick = async () => {
      try {
        const next = await Promise.all(
          ids.map((id) => generationApi.job(workspaceId, id)),
        );
        if (cancelled) return;
        setSnapshot({ key, jobs: next, error: null });

        if (next.every(isJobFinished)) {
          settled.current?.();
          return;
        }
      } catch (cause) {
        if (cancelled) return;
        // Reported but not fatal: the next tick usually succeeds, and giving
        // up here would strand the screen on a spinner.
        setSnapshot((current) => ({
          key,
          jobs: current.key === key ? current.jobs : [],
          error: cause instanceof Error ? cause.message : "Lost contact.",
        }));
      }

      delay = Math.min(delay * 1.5, MAX_DELAY_MS);
      timer = setTimeout(() => void tick(), delay);
    };

    timer = setTimeout(() => void tick(), FIRST_DELAY_MS);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [workspaceId, key]);

  // Derived rather than stored: a stale snapshot from a previous job set is
  // simply not this set's, so it is never shown and never needs clearing.
  const current = snapshot.key === key ? snapshot : EMPTY;
  const settledAlready =
    current.jobs.length > 0 && current.jobs.every(isJobFinished);

  return {
    jobs: current.jobs,
    active: key !== "" && !settledAlready,
    error: current.error,
  };
}

/** How far along a batch of jobs is, as a percentage for a progress bar. */
export function batchProgress(jobs: readonly JobResponse[]): number {
  if (jobs.length === 0) return 0;
  const total = jobs.reduce(
    (sum, job) => sum + (isJobFinished(job) ? 100 : job.progress),
    0,
  );
  return Math.round(total / jobs.length);
}

/** A short human summary of a batch, for a status line. */
export function summarize(jobs: readonly JobResponse[]): string {
  if (jobs.length === 0) return "";
  const done = jobs.filter((job) => job.status === "COMPLETED").length;
  const failed = jobs.filter(
    (job) => job.status === "FAILED" || job.status === "TIMEOUT",
  ).length;
  const parts = [`${done}/${jobs.length} done`];
  if (failed > 0) parts.push(`${failed} failed`);
  return parts.join(" · ");
}

/** Job ids that ended badly, so a screen can offer a retry. */
export function failedJobs(jobs: readonly JobResponse[]): JobResponse[] {
  return jobs.filter(
    (job) => job.status === "FAILED" || job.status === "TIMEOUT",
  );
}

export interface JobPollHandle {
  /** Start watching a set of jobs, replacing whatever was being watched. */
  watch: (ids: readonly string[], onSettled?: () => void) => void;
  clear: () => void;
  state: JobPollState;
}

/**
 * `useJobPoll` for a screen that decides what to watch as the user acts,
 * rather than knowing its job ids up front.
 */
export function useJobPollHandle(workspaceId: string | null): JobPollHandle {
  const [ids, setIds] = useState<readonly string[]>([]);
  const onSettledRef = useRef<(() => void) | undefined>(undefined);

  const handleSettled = useCallback(() => {
    onSettledRef.current?.();
  }, []);

  const state = useJobPoll(workspaceId, ids, handleSettled);

  const watch = useCallback((next: readonly string[], settled?: () => void) => {
    onSettledRef.current = settled;
    setIds(next);
  }, []);

  const clear = useCallback(() => {
    onSettledRef.current = undefined;
    setIds([]);
  }, []);

  return { watch, clear, state };
}
