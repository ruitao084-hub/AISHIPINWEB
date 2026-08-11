import { cn } from "@aipvs/ui";

/**
 * PHASE 0 placeholder.
 *
 * The real marketing surface is built in PHASE 15 against §102 (professional,
 * modern, low learning curve — explicitly *not* a technical dashboard). What
 * ships here is only enough to prove the toolchain renders and to state the two
 * primary user tasks the eventual landing page must lead with.
 */

const PRIMARY_TASKS = [
  {
    title: "Upload Product",
    body: "Register a real product, attach its photos, and confirm the facts that may be used in advertising.",
  },
  {
    title: "Generate Video",
    body: "Creative plan, script, storyboard, shot generation, voiceover, render — every step asynchronous and traceable.",
  },
] as const;

export default function HomePage() {
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-10 px-6 py-20">
      <header className="flex flex-col gap-3">
        <p className="text-muted text-sm font-medium tracking-widest uppercase">
          Phase 0 · Repository Bootstrap
        </p>
        <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          AI Product Video Studio
        </h1>
        <p className="text-muted max-w-xl text-lg text-pretty">
          Turn real product photos into truthful, ready-to-publish product
          videos.
        </p>
      </header>

      <ul className="grid gap-4 sm:grid-cols-2">
        {PRIMARY_TASKS.map((task) => (
          <li
            key={task.title}
            className={cn(
              "border-border rounded-xl border p-5",
              "transition-colors hover:border-current/30",
            )}
          >
            <h2 className="text-base font-semibold">{task.title}</h2>
            <p className="text-muted mt-2 text-sm leading-relaxed">
              {task.body}
            </p>
          </li>
        ))}
      </ul>

      <footer className="text-muted border-border border-t pt-6 text-sm">
        Application routes land from PHASE 3 onward. See{" "}
        <code className="font-mono">TASK_STATUS.md</code> for current progress.
      </footer>
    </main>
  );
}
