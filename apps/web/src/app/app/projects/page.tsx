"use client";

/**
 * Projects list and the creation wizard (P7-T03).
 *
 * The wizard is a form, not a multi-step flow with hidden state. Every field
 * §16 feeds to the creative engine is visible at once, because a user who
 * cannot see that they asked for a 15-second Douyin cut will not understand
 * the three plans they get back.
 *
 * Choosing a platform moves the aspect ratio to that platform's native frame,
 * and stops doing so the moment the user picks one themselves — a helpful
 * default that keeps overriding a deliberate choice is worse than no default.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  ApiError,
  productApi,
  projectApi,
  workspaceApi,
  type AspectRatio,
  type ProductResponse,
  type ProjectPurpose,
  type ProjectResponse,
  type QualityMode,
  type TargetPlatform,
  type VideoStyle,
} from "@/lib/api/client";

interface Loaded {
  workspaceId: string;
  projects: ProjectResponse[];
  products: ProductResponse[];
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | ({ kind: "ready" } & Loaded);

const PURPOSES: ProjectPurpose[] = [
  "SOCIAL_AD",
  "ECOMMERCE_LISTING",
  "LAUNCH",
  "BRAND_STORY",
  "FEATURE_HIGHLIGHT",
  "TUTORIAL",
  "OTHER",
];

const PLATFORMS: TargetPlatform[] = [
  "DOUYIN",
  "XIAOHONGSHU",
  "BILIBILI",
  "WECHAT_CHANNELS",
  "TAOBAO",
  "TIKTOK",
  "INSTAGRAM",
  "YOUTUBE",
  "OTHER",
];

const ASPECTS: AspectRatio[] = ["9:16", "16:9", "1:1", "4:5"];

const STYLES: VideoStyle[] = [
  "CLEAN_MINIMAL",
  "WARM_LIFESTYLE",
  "TECH_PREMIUM",
  "BOLD_ENERGETIC",
  "NATURAL_DOCUMENTARY",
  "LUXURY",
];

const QUALITIES: QualityMode[] = ["FAST", "STANDARD", "HIGH", "PREMIUM"];

/**
 * Each platform's native frame. Mirrors `PLATFORM_DEFAULT_ASPECT` on the
 * server; duplicated rather than fetched because it is a hint for a form
 * field, and a round trip to pre-fill a select is not worth the latency.
 */
const PLATFORM_ASPECT: Record<TargetPlatform, AspectRatio> = {
  DOUYIN: "9:16",
  XIAOHONGSHU: "4:5",
  BILIBILI: "16:9",
  WECHAT_CHANNELS: "9:16",
  TAOBAO: "1:1",
  TIKTOK: "9:16",
  INSTAGRAM: "4:5",
  YOUTUBE: "16:9",
  OTHER: "9:16",
};

export default function ProjectsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async (): Promise<Loaded> => {
    const workspaces = await workspaceApi.list();
    const workspace = workspaces[0];
    if (!workspace) throw new Error("You do not belong to a workspace yet.");

    const [projects, products] = await Promise.all([
      projectApi.list(workspace.id),
      productApi.list(workspace.id),
    ]);
    return { workspaceId: workspace.id, projects, products };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await load();
        if (!cancelled) setState({ kind: "ready", ...loaded });
      } catch (error) {
        if (!cancelled) setState({ kind: "error", message: describe(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const refresh = useCallback(async () => {
    try {
      setState({ kind: "ready", ...(await load()) });
    } catch (error) {
      setState({ kind: "error", message: describe(error) });
    }
  }, [load]);

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

  const { workspaceId, projects, products } = state;

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
      <p className="text-muted mt-1 text-sm">
        A project turns one product into one video.
      </p>

      {actionError && (
        <p
          role="alert"
          className="border-border mt-6 rounded-md border px-3 py-2 text-sm"
        >
          {actionError}
        </p>
      )}

      {projects.length > 0 && (
        <ul className="mt-8 grid gap-2">
          {projects.map((project) => (
            <li key={project.id}>
              <Link
                href={`/app/projects/${project.id}`}
                className="border-border flex items-center justify-between gap-4 rounded-lg border px-3 py-2"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm">{project.name}</span>
                  <span className="text-muted mt-0.5 block text-xs">
                    {project.duration_seconds}s · {project.aspect_ratio} ·{" "}
                    {project.target_platform}
                  </span>
                </span>
                <span className="text-muted shrink-0 text-xs tracking-wide uppercase">
                  {project.status}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {products.length === 0 ? (
        <p className="text-muted mt-8 text-sm">
          Add a product before starting a project — a video needs something
          verified to be about.
        </p>
      ) : (
        <NewProjectForm
          products={products}
          busy={creating}
          onSubmit={async (payload) => {
            setActionError(null);
            setCreating(true);
            try {
              await projectApi.create(workspaceId, payload);
              await refresh();
            } catch (error) {
              setActionError(describe(error));
            } finally {
              setCreating(false);
            }
          }}
        />
      )}
    </main>
  );
}

function NewProjectForm({
  products,
  busy,
  onSubmit,
}: {
  products: ProductResponse[];
  busy: boolean;
  onSubmit: (payload: {
    product_id: string;
    name: string;
    language: string;
    purpose: ProjectPurpose;
    target_platform: TargetPlatform;
    target_audience: string | null;
    aspect_ratio: AspectRatio;
    duration_seconds: number;
    style: VideoStyle;
    quality_mode: QualityMode;
  }) => Promise<void>;
}) {
  const [productId, setProductId] = useState(products[0]?.id ?? "");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState<ProjectPurpose>("SOCIAL_AD");
  const [platform, setPlatform] = useState<TargetPlatform>("DOUYIN");
  const [audience, setAudience] = useState("");
  const [aspect, setAspect] = useState<AspectRatio>("9:16");
  const [duration, setDuration] = useState(30);
  const [style, setStyle] = useState<VideoStyle>("CLEAN_MINIMAL");
  const [quality, setQuality] = useState<QualityMode>("STANDARD");
  // Once the user picks a frame themselves, the platform stops overriding it.
  const [aspectTouched, setAspectTouched] = useState(false);

  return (
    <form
      className="border-border mt-8 grid gap-4 rounded-xl border p-4"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSubmit({
          product_id: productId,
          name,
          // The language every generated word is written in (§128). Fixed for
          // now: the whole product is zh-CN, and a selector offering choices
          // the prompts have not been tuned for would promise more than it
          // delivers.
          language: "zh-CN",
          purpose,
          target_platform: platform,
          target_audience: audience.trim() || null,
          aspect_ratio: aspect,
          duration_seconds: duration,
          style,
          quality_mode: quality,
        });
        setName("");
        setAudience("");
      }}
    >
      <h2 className="text-sm font-medium">New project</h2>

      <label className="grid gap-1 text-sm">
        <span className="text-muted text-xs">Product</span>
        <select
          value={productId}
          onChange={(event) => setProductId(event.target.value)}
          className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
        >
          {products.map((product) => (
            <option key={product.id} value={product.id}>
              {product.name}
            </option>
          ))}
        </select>
      </label>

      <label className="grid gap-1 text-sm">
        <span className="text-muted text-xs">Name</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={200}
          className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
        />
      </label>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Purpose</span>
          <select
            value={purpose}
            onChange={(event) =>
              setPurpose(event.target.value as ProjectPurpose)
            }
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {PURPOSES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Platform</span>
          <select
            value={platform}
            onChange={(event) => {
              const next = event.target.value as TargetPlatform;
              setPlatform(next);
              if (!aspectTouched) setAspect(PLATFORM_ASPECT[next]);
            }}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {PLATFORMS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Frame</span>
          <select
            value={aspect}
            onChange={(event) => {
              setAspect(event.target.value as AspectRatio);
              setAspectTouched(true);
            }}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {ASPECTS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Duration (seconds)</span>
          <input
            type="number"
            min={5}
            max={600}
            value={duration}
            onChange={(event) => setDuration(Number(event.target.value))}
            required
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </label>

        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Style</span>
          <select
            value={style}
            onChange={(event) => setStyle(event.target.value as VideoStyle)}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {STYLES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>

        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Quality</span>
          <select
            value={quality}
            onChange={(event) => setQuality(event.target.value as QualityMode)}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {QUALITIES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="grid gap-1 text-sm">
        <span className="text-muted text-xs">Audience (optional)</span>
        <input
          value={audience}
          onChange={(event) => setAudience(event.target.value)}
          placeholder="e.g. 25-35 岁，住在城市公寓"
          className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
        />
      </label>

      <button
        type="submit"
        disabled={busy || !productId}
        className="border-border justify-self-start rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
      >
        {busy ? "Creating…" : "Create project"}
      </button>
    </form>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
