"use client";

/**
 * React binding for the upload queue (P4-T07).
 *
 * Two things this hook owns that the pure state machine deliberately does not:
 *
 * * **Concurrency.** Uploads run a few at a time. Firing twenty parallel PUTs
 *   does not make a connection faster — it divides the same bandwidth into
 *   twenty slow, simultaneously-stalling transfers and makes every progress
 *   bar useless at once.
 * * **Object URL lifetime.** A preview URL pins its blob in memory until it is
 *   revoked, so dropping fifty photos and navigating away would leak all of
 *   them. Every URL created here is revoked on unmount or removal.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { uploadApi, type UploadConfigResponse } from "@/lib/api/client";
import {
  createUploadItem,
  performUpload,
  uploadReducer,
  type UploadHandle,
  type UploadItem,
} from "@/lib/uploads/upload-queue";

const MAX_CONCURRENT = 3;

export interface UseUploaderResult {
  items: UploadItem[];
  config: UploadConfigResponse | null;
  /** MIME list for the file input's `accept`, or undefined before config loads. */
  accept: string | undefined;
  addFiles: (files: Iterable<File>) => void;
  retry: (id: string) => void;
  cancel: (id: string) => void;
  remove: (id: string) => void;
  clearFinished: () => void;
  /** Fires once per asset that reaches `READY`, for the caller to refresh a list. */
  onCompleted?: (asset: UploadItem) => void;
}

export function useUploader(
  workspaceId: string,
  onCompleted?: (item: UploadItem) => void,
): UseUploaderResult {
  const [items, dispatch] = useReducer(uploadReducer, [] as UploadItem[]);
  const [config, setConfig] = useState<UploadConfigResponse | null>(null);

  const running = useRef(new Set<string>());
  const handles = useRef(new Map<string, UploadHandle>());
  const previews = useRef(new Map<string, string>());
  const completed = useRef(new Set<string>());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await uploadApi.config(workspaceId);
        if (!cancelled) setConfig(loaded);
      } catch {
        // Losing the config is not fatal: the picker simply stops filtering,
        // and the server still rejects anything it does not accept. Failing
        // the whole uploader over a hint would be worse.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId]);

  // Revoke every object URL still outstanding when the component goes away.
  useEffect(
    () => () => {
      for (const url of previews.current.values()) URL.revokeObjectURL(url);
      previews.current.clear();
    },
    [],
  );

  const addFiles = useCallback((files: Iterable<File>) => {
    const created = Array.from(files, createUploadItem);
    for (const item of created) {
      if (item.previewUrl) previews.current.set(item.id, item.previewUrl);
    }
    if (created.length > 0) dispatch({ type: "add", items: created });
  }, []);

  const retry = useCallback((id: string) => {
    dispatch({ type: "retry", id });
  }, []);

  const cancel = useCallback((id: string) => {
    handles.current.get(id)?.cancel();
  }, []);

  const remove = useCallback((id: string) => {
    handles.current.get(id)?.cancel();
    const url = previews.current.get(id);
    if (url) {
      URL.revokeObjectURL(url);
      previews.current.delete(id);
    }
    dispatch({ type: "remove", id });
  }, []);

  const clearFinished = useCallback(() => {
    dispatch({ type: "clearFinished" });
  }, []);

  // The pump: whenever the queue changes, start as many uploads as the
  // concurrency budget allows. Driven by an effect rather than by `addFiles`
  // so that a retry re-enters the queue through exactly the same path.
  useEffect(() => {
    const queued = items.filter(
      (item) => item.status === "queued" && !running.current.has(item.id),
    );

    for (const item of queued) {
      if (running.current.size >= MAX_CONCURRENT) break;
      running.current.add(item.id);

      void performUpload(workspaceId, item, dispatch, (handle) => {
        handles.current.set(item.id, handle);
      }).finally(() => {
        running.current.delete(item.id);
        handles.current.delete(item.id);
      });
    }
  }, [items, workspaceId]);

  // Notify once per finished upload, never twice for the same one — the effect
  // re-runs on every progress tick.
  useEffect(() => {
    if (!onCompleted) return;
    for (const item of items) {
      if (item.status === "done" && !completed.current.has(item.id)) {
        completed.current.add(item.id);
        onCompleted(item);
      }
    }
  }, [items, onCompleted]);

  return {
    items,
    config,
    accept: config?.mime_types.join(","),
    addFiles,
    retry,
    cancel,
    remove,
    clearFinished,
  };
}
