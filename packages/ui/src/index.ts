/**
 * `@aipvs/ui` — cross-app UI layer.
 *
 * Scope split (taskbook §5, §102):
 *  - shadcn/ui *primitives* are generated into the consuming app so the shadcn
 *    CLI keeps working normally (`apps/web/src/components/ui`).
 *  - This package holds what must not fork between apps: the class-merge
 *    helper today, and shared composites (job status chips, shot cards, asset
 *    tiles) as they arrive from PHASE 5 onward.
 */

export { cn } from "./lib/cn";
