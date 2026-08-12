"use client";

/**
 * Drag-and-drop uploader (P4-T07: drag drop, progress, preview, retry).
 *
 * Accessibility is not an afterthought here: a drop zone that only responds to
 * pointer events excludes keyboard and screen-reader users entirely, so the
 * zone is a real `<button>` that opens the file picker, and the hidden input
 * remains the actual control. The drag handlers are an enhancement layered on
 * top of something that already works without them.
 */

import { useCallback, useRef, useState, type DragEvent } from "react";

import type { UploadItem, UploadStatus } from "@/lib/uploads/upload-queue";
import { useUploader } from "@/lib/uploads/use-uploader";

interface MediaUploaderProps {
  workspaceId: string;
  onUploaded?: (item: UploadItem) => void;
}

const STATUS_LABEL: Record<UploadStatus, string> = {
  queued: "Waiting",
  signing: "Preparing",
  uploading: "Uploading",
  finalizing: "Checking",
  done: "Ready",
  error: "Failed",
  canceled: "Canceled",
};

export function MediaUploader({ workspaceId, onUploaded }: MediaUploaderProps) {
  const {
    items,
    accept,
    config,
    addFiles,
    retry,
    cancel,
    remove,
    clearFinished,
  } = useUploader(workspaceId, onUploaded);

  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  // Drag events fire for every child element entered, so a plain boolean
  // flickers off the moment the pointer crosses an inner node. Counting
  // enter/leave pairs is what keeps the highlight steady.
  const dragDepth = useRef(0);

  const onDragEnter = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  }, []);

  const onDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setDragging(false);
    }
  }, []);

  const onDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      if (event.dataTransfer?.files?.length) {
        addFiles(event.dataTransfer.files);
      }
    },
    [addFiles],
  );

  const finishedCount = items.filter((item) => item.status === "done").length;

  return (
    <section aria-label="Upload media">
      <div
        onDragEnter={onDragEnter}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`border-border rounded-xl border border-dashed p-8 text-center transition-colors ${
          dragging ? "bg-black/5 dark:bg-white/5" : ""
        }`}
      >
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="text-sm font-medium underline underline-offset-4"
        >
          Choose files
        </button>
        <p className="text-muted mt-2 text-sm">or drag them here</p>
        {config && (
          <p className="text-muted mt-3 text-xs">
            Images up to {formatBytes(config.max_image_bytes)}, video up to{" "}
            {formatBytes(config.max_video_bytes)} and{" "}
            {config.max_video_duration_seconds} seconds.
          </p>
        )}

        <input
          ref={inputRef}
          type="file"
          multiple
          // `accept` filters the picker for convenience only; the server's
          // whitelist is the control that matters (§12).
          {...(accept ? { accept } : {})}
          onChange={(event) => {
            if (event.target.files) addFiles(event.target.files);
            // Reset so choosing the same file twice in a row still fires.
            event.target.value = "";
          }}
          className="sr-only"
        />
      </div>

      {items.length > 0 && (
        <>
          <ul className="mt-6 grid gap-3">
            {items.map((item) => (
              <UploadRow
                key={item.id}
                item={item}
                onRetry={() => retry(item.id)}
                onCancel={() => cancel(item.id)}
                onRemove={() => remove(item.id)}
              />
            ))}
          </ul>

          {finishedCount > 0 && (
            <button
              type="button"
              onClick={clearFinished}
              className="text-muted mt-4 text-xs underline underline-offset-4"
            >
              Clear {finishedCount} finished
            </button>
          )}
        </>
      )}
    </section>
  );
}

interface UploadRowProps {
  item: UploadItem;
  onRetry: () => void;
  onCancel: () => void;
  onRemove: () => void;
}

function UploadRow({ item, onRetry, onCancel, onRemove }: UploadRowProps) {
  const inFlight =
    item.status === "signing" ||
    item.status === "uploading" ||
    item.status === "finalizing";
  const percent = Math.round(item.progress * 100);

  return (
    <li className="border-border flex items-center gap-4 rounded-xl border p-3">
      <Thumbnail item={item} />

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="truncate text-sm font-medium">{item.file.name}</p>
          <span className="text-muted shrink-0 text-xs">
            {STATUS_LABEL[item.status]}
          </span>
        </div>

        {item.status === "uploading" && (
          <div
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Uploading ${item.file.name}`}
            className="mt-2 h-1 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/10"
          >
            <div
              className="h-full bg-current transition-[width]"
              style={{ width: `${percent}%` }}
            />
          </div>
        )}

        {item.status === "error" && item.error && (
          <p role="alert" className="mt-1 text-xs">
            {item.error}
          </p>
        )}

        {item.status === "done" && item.asset && (
          <p className="text-muted mt-1 text-xs">{describeAsset(item)}</p>
        )}
      </div>

      <div className="flex shrink-0 gap-3 text-xs">
        {inFlight && (
          <button type="button" onClick={onCancel} className="underline">
            Cancel
          </button>
        )}
        {(item.status === "error" || item.status === "canceled") && (
          <button type="button" onClick={onRetry} className="underline">
            Retry
          </button>
        )}
        {!inFlight && (
          <button
            type="button"
            onClick={onRemove}
            className="text-muted underline"
          >
            Remove
          </button>
        )}
      </div>
    </li>
  );
}

function Thumbnail({ item }: { item: UploadItem }) {
  if (item.previewUrl) {
    return (
      // A local object URL, so `next/image` would add a proxy hop for a blob
      // the browser already holds.
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.previewUrl}
        alt=""
        className="h-12 w-12 shrink-0 rounded-md object-cover"
      />
    );
  }
  return (
    <div
      aria-hidden
      className="text-muted flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-black/5 text-[10px] tracking-wide uppercase dark:bg-white/5"
    >
      {extensionOf(item.file.name)}
    </div>
  );
}

function describeAsset(item: UploadItem): string {
  const asset = item.asset;
  if (!asset) return "";
  const parts: string[] = [];
  if (asset.width && asset.height) parts.push(`${asset.width}×${asset.height}`);
  if (asset.duration_ms)
    parts.push(`${(asset.duration_ms / 1000).toFixed(1)}s`);
  if (asset.size_bytes) parts.push(formatBytes(asset.size_bytes));
  return parts.join(" · ");
}

function extensionOf(filename: string): string {
  const index = filename.lastIndexOf(".");
  return index > 0 ? filename.slice(index + 1, index + 5) : "file";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
