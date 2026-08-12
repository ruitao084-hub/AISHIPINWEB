"use client";

/**
 * Media library (PHASE 4).
 *
 * Uploads land here before there is anything to attach them to — products
 * arrive in PHASE 5, projects in PHASE 7 — which is why the workspace has a
 * standalone library at all rather than an upload button buried in a product
 * form.
 */

import { useCallback, useEffect, useState } from "react";

import { MediaUploader, formatBytes } from "@/components/media-uploader";
import {
  ApiError,
  uploadApi,
  workspaceApi,
  type MediaAssetResponse,
  type WorkspaceResponse,
} from "@/lib/api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      workspace: WorkspaceResponse;
      assets: MediaAssetResponse[];
    };

export default function MediaLibraryPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  const load = useCallback(async () => {
    const workspaces = await workspaceApi.list();
    const workspace = workspaces[0];
    if (!workspace) {
      throw new Error("You do not belong to a workspace yet.");
    }
    const assets = await uploadApi.list(workspace.id);
    return { workspace, assets };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const { workspace, assets } = await load();
        if (!cancelled) setState({ kind: "ready", workspace, assets });
      } catch (error) {
        if (cancelled) return;
        setState({
          kind: "error",
          message:
            error instanceof ApiError || error instanceof Error
              ? error.message
              : "Could not load your media.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  // Refetch rather than splicing the finished item into local state: the
  // server's row carries the probed dimensions, duration and checksum, and
  // reconstructing that on the client would be a second source of truth.
  const refresh = useCallback(async () => {
    setState((current) => {
      if (current.kind !== "ready") return current;
      void uploadApi
        .list(current.workspace.id)
        .then((assets) =>
          setState((latest) =>
            latest.kind === "ready" ? { ...latest, assets } : latest,
          ),
        )
        .catch(() => {
          // The upload itself succeeded; a failed list refresh is not worth
          // replacing the page with an error.
        });
      return current;
    });
  }, []);

  const onUploaded = useCallback(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Media</h1>
      <p className="text-muted mt-1 text-sm">
        Product photos and footage for your videos.
      </p>

      {state.kind === "loading" && (
        <p className="text-muted mt-8 text-sm" role="status">
          Loading…
        </p>
      )}

      {state.kind === "error" && (
        <p
          role="alert"
          className="border-border mt-8 rounded-md border px-3 py-2 text-sm"
        >
          {state.message}
        </p>
      )}

      {state.kind === "ready" && (
        <>
          <div className="mt-8">
            <MediaUploader
              workspaceId={state.workspace.id}
              onUploaded={onUploaded}
            />
          </div>

          <h2 className="mt-12 text-sm font-medium tracking-wide uppercase">
            Library
          </h2>

          {state.assets.length === 0 ? (
            <p className="text-muted mt-3 text-sm">Nothing uploaded yet.</p>
          ) : (
            <ul className="mt-3 grid gap-2">
              {state.assets.map((asset) => (
                <li
                  key={asset.id}
                  className="border-border flex items-baseline justify-between gap-4 rounded-lg border px-3 py-2"
                >
                  <span className="truncate text-sm">
                    {asset.original_filename ?? asset.id}
                  </span>
                  <span className="text-muted shrink-0 text-xs">
                    {asset.asset_type}
                    {asset.width && asset.height
                      ? ` · ${asset.width}×${asset.height}`
                      : ""}
                    {asset.size_bytes
                      ? ` · ${formatBytes(asset.size_bytes)}`
                      : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </main>
  );
}
