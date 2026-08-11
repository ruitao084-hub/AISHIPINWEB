import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge conditional class names, letting later Tailwind utilities win over
 * earlier conflicting ones.
 *
 * This lives in `@aipvs/ui` rather than inside the web app so every frontend
 * surface (and the shadcn/ui primitives generated into them) resolves the same
 * implementation. `components.json` points its `utils` alias here.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
