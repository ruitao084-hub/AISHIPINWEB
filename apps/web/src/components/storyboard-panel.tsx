"use client";

/**
 * Storyboard review and shot editing (P8-T09).
 *
 * Two decisions shape this component.
 *
 * **The compiled prompt is shown, not hidden.** It is what will actually be
 * sent to a video model, and a user who cannot see it cannot tell why a clip
 * came back wrong. It is displayed read-only, because §19 forbids handing a
 * model a sentence a user typed — the fields below it are the editable
 * surface, and changing one rebuilds the prompt.
 *
 * **The identity lock is a visible per-shot control (§29).** Not a global
 * setting: whether the product must match its references exactly depends on
 * whether the shot is a macro or a wide room, and that is a judgement the
 * person looking at the shot should make.
 */

import { useState } from "react";

import { type ShotResponse, type StoryboardResponse } from "@/lib/api/client";

export function StoryboardPanel({
  targetSeconds,
  storyboard,
  shots,
  busy,
  onGenerate,
  onApprove,
  onEditShot,
}: {
  targetSeconds: number;
  storyboard: StoryboardResponse | null;
  shots: ShotResponse[];
  busy: boolean;
  onGenerate: () => void;
  onApprove: () => void;
  onEditShot: (shotId: string, payload: Record<string, unknown>) => void;
}) {
  const total = storyboard?.total_duration_seconds ?? 0;
  // The same 10% the server enforces. Shown rather than only enforced, so a
  // user editing durations can see themselves drifting out of range instead
  // of discovering it when approval is refused.
  const outOfRange =
    targetSeconds > 0 && Math.abs(total - targetSeconds) / targetSeconds > 0.1;

  return (
    <section className="mt-12">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-medium tracking-wide uppercase">
          Storyboard
        </h2>
        <button
          type="button"
          disabled={busy}
          className="border-border rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
          onClick={onGenerate}
        >
          {busy
            ? "Building…"
            : storyboard
              ? "Rebuild storyboard"
              : "Break into shots"}
        </button>
      </div>
      <p className="text-muted mt-1 text-xs">
        Needs an approved script. Each shot&rsquo;s prompt is compiled from its
        fields — edit the fields, not the prompt.
      </p>

      {storyboard && (
        <>
          <div className="border-border mt-4 flex items-baseline justify-between gap-4 rounded-lg border px-3 py-2">
            <span className="text-sm">
              Version {storyboard.version} · {storyboard.status} ·{" "}
              {shots.length} shot{shots.length === 1 ? "" : "s"}
            </span>
            <span
              className={`shrink-0 text-xs ${outOfRange ? "" : "text-muted"}`}
            >
              {total}s of {targetSeconds}s
              {outOfRange ? " — outside tolerance" : ""}
            </span>
          </div>

          <ol className="mt-3 grid gap-3">
            {shots.map((shot) => (
              <ShotCard
                key={shot.id}
                shot={shot}
                onEdit={(payload) => onEditShot(shot.id, payload)}
              />
            ))}
          </ol>

          {storyboard.status !== "APPROVED" && shots.length > 0 && (
            <button
              type="button"
              disabled={busy}
              className="border-border mt-4 rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
              onClick={onApprove}
            >
              Approve storyboard
            </button>
          )}
        </>
      )}
    </section>
  );
}

function ShotCard({
  shot,
  onEdit,
}: {
  shot: ShotResponse;
  onEdit: (payload: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <li className="border-border rounded-xl border p-4">
      <div className="flex items-baseline justify-between gap-4">
        <h3 className="text-sm font-medium">
          {shot.sequence_no}. {shot.title || shot.shot_type}
        </h3>
        <span className="text-muted shrink-0 text-xs">
          {shot.shot_type} · {shot.duration_seconds}s
          {shot.identity_lock ? " · identity locked" : ""}
        </span>
      </div>

      <p className="text-muted mt-2 text-sm">{shot.description}</p>

      {shot.voiceover_text && (
        <p className="mt-2 text-sm">&ldquo;{shot.voiceover_text}&rdquo;</p>
      )}

      <div className="mt-3 flex flex-wrap gap-3 text-xs">
        <button
          type="button"
          className="underline"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Hide details" : "Edit shot"}
        </button>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={shot.identity_lock}
            onChange={(event) =>
              onEdit({ identity_lock: event.target.checked })
            }
          />
          <span>Lock product identity</span>
        </label>
      </div>

      {open && <ShotEditor shot={shot} onEdit={onEdit} />}
    </li>
  );
}

function ShotEditor({
  shot,
  onEdit,
}: {
  shot: ShotResponse;
  onEdit: (payload: Record<string, unknown>) => void;
}) {
  const [duration, setDuration] = useState(shot.duration_seconds);
  const [camera, setCamera] = useState(shot.camera);
  const [motion, setMotion] = useState(shot.motion);
  const [lighting, setLighting] = useState(shot.lighting);
  const [composition, setComposition] = useState(shot.composition);
  const [voiceover, setVoiceover] = useState(shot.voiceover_text);

  return (
    <form
      className="mt-3 grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        onEdit({
          duration_seconds: duration,
          camera,
          motion,
          lighting,
          composition,
          voiceover_text: voiceover,
        });
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Duration (2–10s)">
          <input
            type="number"
            min={2}
            max={10}
            step={0.5}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
            className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="Camera">
          <input
            value={camera}
            onChange={(event) => setCamera(event.target.value)}
            className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="Motion">
          <input
            value={motion}
            onChange={(event) => setMotion(event.target.value)}
            className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="Lighting">
          <input
            value={lighting}
            onChange={(event) => setLighting(event.target.value)}
            className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="Composition">
          <input
            value={composition}
            onChange={(event) => setComposition(event.target.value)}
            className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </Field>
        <Field label="Transitions">
          <span className="text-muted text-sm">
            {shot.transition_in} → {shot.transition_out}
          </span>
        </Field>
      </div>

      <Field label="Voiceover">
        <input
          value={voiceover}
          onChange={(event) => setVoiceover(event.target.value)}
          className="border-border w-full rounded-md border bg-transparent px-2 py-1.5 text-sm"
        />
      </Field>

      <button
        type="submit"
        className="border-border justify-self-start rounded-md border px-3 py-1.5 text-sm"
      >
        Save and recompile prompt
      </button>

      <details className="text-xs">
        <summary className="text-muted cursor-pointer">
          Compiled prompt (read-only)
        </summary>
        <pre className="border-border mt-2 overflow-x-auto rounded-md border px-2 py-1.5 whitespace-pre-wrap">
          {shot.visual_prompt}
        </pre>
        <p className="text-muted mt-2">Avoid: {shot.negative_prompt}</p>
        {shot.references.length > 0 && (
          <p className="text-muted mt-2">
            {shot.references.length} identity reference
            {shot.references.length === 1 ? "" : "s"} attached — the generated
            product must match them.
          </p>
        )}
      </details>
    </form>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="grid gap-1 text-sm">
      <span className="text-muted text-xs">{label}</span>
      {children}
    </label>
  );
}
