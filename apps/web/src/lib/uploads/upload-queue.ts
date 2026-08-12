/**
 * Upload state machine (taskbook §12, P4-T07).
 *
 * Kept apart from React because it is the part worth testing: a file moves
 * through presign → transfer → complete, and each hop can fail differently.
 * The reducer below is pure, so those transitions are asserted directly rather
 * than through a rendered component.
 *
 * The states mirror the server's two-phase handshake rather than inventing a
 * parallel vocabulary — when a `finalizing` item fails, the asset row on the
 * server really is `FAILED`, and the UI says so honestly.
 */

import {
  ApiError,
  UploadAbortedError,
  uploadApi,
  uploadToStorage,
  type MediaAssetResponse,
} from "@/lib/api/client";

export type UploadStatus =
  | "queued"
  | "signing"
  | "uploading"
  | "finalizing"
  | "done"
  | "error"
  | "canceled";

export interface UploadItem {
  /** Client-side id. The server's asset id only exists after presign. */
  readonly id: string;
  readonly file: File;
  readonly status: UploadStatus;
  /** 0–1, meaningful only while `uploading`. */
  readonly progress: number;
  readonly assetId: string | null;
  readonly asset: MediaAssetResponse | null;
  readonly error: string | null;
  /** Object URL for a local preview, or null for types that have none. */
  readonly previewUrl: string | null;
}

export type UploadAction =
  | { type: "add"; items: UploadItem[] }
  | { type: "status"; id: string; status: UploadStatus }
  | { type: "progress"; id: string; progress: number }
  | { type: "presigned"; id: string; assetId: string }
  | { type: "done"; id: string; asset: MediaAssetResponse }
  | { type: "error"; id: string; message: string }
  | { type: "retry"; id: string }
  | { type: "remove"; id: string }
  | { type: "clearFinished" };

/** Statuses from which a retry is meaningful. */
const RETRYABLE: ReadonlySet<UploadStatus> = new Set<UploadStatus>([
  "error",
  "canceled",
]);

export function uploadReducer(
  state: UploadItem[],
  action: UploadAction,
): UploadItem[] {
  switch (action.type) {
    case "add":
      return [...state, ...action.items];

    case "status":
      return patch(state, action.id, { status: action.status });

    case "progress":
      return patch(state, action.id, {
        progress: clamp(action.progress),
        status: "uploading",
      });

    case "presigned":
      return patch(state, action.id, {
        assetId: action.assetId,
        status: "uploading",
        progress: 0,
      });

    case "done":
      return patch(state, action.id, {
        status: "done",
        progress: 1,
        asset: action.asset,
        error: null,
      });

    case "error":
      return patch(state, action.id, {
        status: "error",
        error: action.message,
      });

    case "retry": {
      const item = state.find((entry) => entry.id === action.id);
      if (!item || !RETRYABLE.has(item.status)) return state;
      // A retry starts a *new* upload: the previous attempt's asset row is
      // FAILED on the server and cannot be reused (§12), so the id is dropped
      // rather than carried over into a request that would be rejected.
      return patch(state, action.id, {
        status: "queued",
        progress: 0,
        error: null,
        assetId: null,
      });
    }

    case "remove":
      return state.filter((entry) => entry.id !== action.id);

    case "clearFinished":
      return state.filter((entry) => entry.status !== "done");

    default:
      return state;
  }
}

function patch(
  state: UploadItem[],
  id: string,
  changes: Partial<UploadItem>,
): UploadItem[] {
  return state.map((entry) =>
    entry.id === id ? { ...entry, ...changes } : entry,
  );
}

function clamp(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export function createUploadItem(file: File): UploadItem {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    file,
    status: "queued",
    progress: 0,
    assetId: null,
    asset: null,
    error: null,
    previewUrl: file.type.startsWith("image/")
      ? URL.createObjectURL(file)
      : null,
  };
}

export interface UploadHandle {
  cancel: () => void;
}

/**
 * Run one file through the full handshake.
 *
 * `dispatch` receives every transition, so the caller's state stays in step
 * without this function knowing anything about React.
 */
export async function performUpload(
  workspaceId: string,
  item: UploadItem,
  dispatch: (action: UploadAction) => void,
  registerHandle?: (handle: UploadHandle) => void,
): Promise<void> {
  dispatch({ type: "status", id: item.id, status: "signing" });

  try {
    const presigned = await uploadApi.presign(workspaceId, {
      filename: item.file.name,
      // Browsers occasionally report an empty type for an unfamiliar
      // extension. Sending it through unchanged lets the server reject it with
      // its own message rather than the client guessing a type it cannot know.
      mime_type: item.file.type,
      size_bytes: item.file.size,
    });

    dispatch({
      type: "presigned",
      id: item.id,
      assetId: presigned.asset.id,
    });

    const transfer = uploadToStorage(
      presigned.upload_url,
      item.file,
      presigned.headers,
      (fraction) =>
        dispatch({ type: "progress", id: item.id, progress: fraction }),
    );
    registerHandle?.({ cancel: transfer.cancel });
    await transfer.done;

    dispatch({ type: "status", id: item.id, status: "finalizing" });
    const asset = await uploadApi.complete(workspaceId, presigned.asset.id);
    dispatch({ type: "done", id: item.id, asset });
  } catch (error) {
    if (error instanceof UploadAbortedError) {
      dispatch({ type: "status", id: item.id, status: "canceled" });
      return;
    }
    dispatch({ type: "error", id: item.id, message: describe(error) });
  }
}

/**
 * Turn a failure into something worth showing a user.
 *
 * The server's message is preferred wherever there is one: it knows *why* a
 * file was refused ("larger than the 20 MB limit"), which is the difference
 * between a user who can fix the problem and one who cannot.
 */
export function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "The upload failed.";
}
