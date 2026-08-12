import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, UploadAbortedError } from "@/lib/api/client";
import {
  describe as describeError,
  performUpload,
  uploadReducer,
  type UploadAction,
  type UploadItem,
} from "./upload-queue";

vi.mock("@/lib/api/client", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/api/client")>(
      "@/lib/api/client",
    );
  return {
    ...actual,
    uploadApi: {
      presign: vi.fn(),
      complete: vi.fn(),
      config: vi.fn(),
      list: vi.fn(),
      get: vi.fn(),
    },
    uploadToStorage: vi.fn(),
  };
});

const { uploadApi, uploadToStorage } = await import("@/lib/api/client");

function item(overrides: Partial<UploadItem> = {}): UploadItem {
  return {
    id: "item-1",
    file: new File(["x"], "photo.png", { type: "image/png" }),
    status: "queued",
    progress: 0,
    assetId: null,
    asset: null,
    error: null,
    previewUrl: null,
    ...overrides,
  };
}

function apiError(code: string, message: string): ApiError {
  return new ApiError(400, {
    error: { code, message, request_id: "req-1" },
  } as never);
}

describe("uploadReducer", () => {
  it("moves an item through the two-phase handshake", () => {
    let state = uploadReducer([], { type: "add", items: [item()] });
    state = uploadReducer(state, {
      type: "status",
      id: "item-1",
      status: "signing",
    });
    expect(state[0]?.status).toBe("signing");

    state = uploadReducer(state, {
      type: "presigned",
      id: "item-1",
      assetId: "asset-9",
    });
    expect(state[0]).toMatchObject({ status: "uploading", assetId: "asset-9" });

    state = uploadReducer(state, {
      type: "progress",
      id: "item-1",
      progress: 0.5,
    });
    expect(state[0]?.progress).toBe(0.5);

    state = uploadReducer(state, {
      type: "done",
      id: "item-1",
      asset: { id: "asset-9" } as never,
    });
    expect(state[0]).toMatchObject({ status: "done", progress: 1 });
  });

  it("clamps progress that arrives out of range", () => {
    const state = uploadReducer([item()], {
      type: "progress",
      id: "item-1",
      progress: 1.4,
    });
    expect(state[0]?.progress).toBe(1);
  });

  it("treats NaN progress as zero rather than rendering a broken bar", () => {
    const state = uploadReducer([item()], {
      type: "progress",
      id: "item-1",
      progress: Number.NaN,
    });
    expect(state[0]?.progress).toBe(0);
  });

  it("drops the asset id on retry, because the server row is FAILED", () => {
    // §12 — a failed asset cannot be completed again; the retry must presign a
    // fresh one or the second attempt is rejected for the wrong reason.
    const failed = item({
      status: "error",
      assetId: "asset-9",
      error: "boom",
      progress: 0.7,
    });
    const state = uploadReducer([failed], { type: "retry", id: "item-1" });

    expect(state[0]).toMatchObject({
      status: "queued",
      assetId: null,
      error: null,
      progress: 0,
    });
  });

  it("retries a canceled upload", () => {
    const state = uploadReducer([item({ status: "canceled" })], {
      type: "retry",
      id: "item-1",
    });
    expect(state[0]?.status).toBe("queued");
  });

  it("ignores a retry of an upload that is still running", () => {
    const running = item({ status: "uploading", progress: 0.3 });
    const state = uploadReducer([running], { type: "retry", id: "item-1" });
    expect(state[0]).toBe(running);
  });

  it("ignores a retry of a finished upload", () => {
    const done = item({ status: "done", progress: 1 });
    expect(uploadReducer([done], { type: "retry", id: "item-1" })[0]).toBe(
      done,
    );
  });

  it("leaves other items untouched", () => {
    const other = item({ id: "item-2" });
    const state = uploadReducer([item(), other], {
      type: "error",
      id: "item-1",
      message: "nope",
    });
    expect(state[1]).toBe(other);
  });

  it("clears only finished items", () => {
    const state = uploadReducer(
      [
        item({ id: "a", status: "done" }),
        item({ id: "b", status: "error" }),
        item({ id: "c", status: "uploading" }),
      ],
      { type: "clearFinished" },
    );
    expect(state.map((entry) => entry.id)).toEqual(["b", "c"]);
  });

  it("removes a single item", () => {
    const state = uploadReducer([item({ id: "a" }), item({ id: "b" })], {
      type: "remove",
      id: "a",
    });
    expect(state.map((entry) => entry.id)).toEqual(["b"]);
  });
});

describe("performUpload", () => {
  const presigned = {
    asset: { id: "asset-9" },
    upload_url: "https://storage.example.com/key?sig=abc",
    method: "PUT",
    headers: { "Content-Type": "image/png" },
    expires_in: 900,
  };

  let actions: UploadAction[];
  const dispatch = (action: UploadAction) => {
    actions.push(action);
  };

  beforeEach(() => {
    actions = [];
    vi.mocked(uploadApi.presign).mockReset();
    vi.mocked(uploadApi.complete).mockReset();
    vi.mocked(uploadToStorage).mockReset();
  });

  it("presigns, transfers, then completes", async () => {
    vi.mocked(uploadApi.presign).mockResolvedValue(presigned as never);
    vi.mocked(uploadToStorage).mockReturnValue({
      done: Promise.resolve(),
      cancel: vi.fn(),
    });
    vi.mocked(uploadApi.complete).mockResolvedValue({
      id: "asset-9",
    } as never);

    await performUpload("ws-1", item(), dispatch);

    expect(actions.map((action) => action.type)).toEqual([
      "status",
      "presigned",
      "status",
      "done",
    ]);
    expect(uploadApi.complete).toHaveBeenCalledWith("ws-1", "asset-9");
  });

  it("sends the bytes to storage, never to the API", async () => {
    vi.mocked(uploadApi.presign).mockResolvedValue(presigned as never);
    vi.mocked(uploadToStorage).mockReturnValue({
      done: Promise.resolve(),
      cancel: vi.fn(),
    });
    vi.mocked(uploadApi.complete).mockResolvedValue({} as never);

    await performUpload("ws-1", item(), dispatch);

    const [url, , headers] = vi.mocked(uploadToStorage).mock.calls[0] ?? [];
    expect(url).toBe(presigned.upload_url);
    expect(headers).toEqual({ "Content-Type": "image/png" });
  });

  it("surfaces the server's reason for refusing a file", async () => {
    vi.mocked(uploadApi.presign).mockRejectedValue(
      apiError("UPLOAD_TOO_LARGE", "That file is larger than the 20 MB limit."),
    );

    await performUpload("ws-1", item(), dispatch);

    expect(actions.at(-1)).toEqual({
      type: "error",
      id: "item-1",
      message: "That file is larger than the 20 MB limit.",
    });
  });

  it("reports a rejection at completion, not a silent success", async () => {
    vi.mocked(uploadApi.presign).mockResolvedValue(presigned as never);
    vi.mocked(uploadToStorage).mockReturnValue({
      done: Promise.resolve(),
      cancel: vi.fn(),
    });
    vi.mocked(uploadApi.complete).mockRejectedValue(
      apiError("ASSET_INVALID", "The file contents do not match."),
    );

    await performUpload("ws-1", item(), dispatch);

    expect(actions.at(-1)).toMatchObject({
      type: "error",
      message: "The file contents do not match.",
    });
  });

  it("distinguishes a cancellation from a failure", async () => {
    vi.mocked(uploadApi.presign).mockResolvedValue(presigned as never);
    vi.mocked(uploadToStorage).mockReturnValue({
      done: Promise.reject(new UploadAbortedError()),
      cancel: vi.fn(),
    });

    await performUpload("ws-1", item(), dispatch);

    expect(actions.at(-1)).toEqual({
      type: "status",
      id: "item-1",
      status: "canceled",
    });
    expect(uploadApi.complete).not.toHaveBeenCalled();
  });

  it("hands the caller a cancel handle before the transfer starts", async () => {
    const cancel = vi.fn();
    vi.mocked(uploadApi.presign).mockResolvedValue(presigned as never);
    vi.mocked(uploadToStorage).mockReturnValue({
      done: Promise.resolve(),
      cancel,
    });
    vi.mocked(uploadApi.complete).mockResolvedValue({} as never);

    const handles: Array<{ cancel: () => void }> = [];
    await performUpload("ws-1", item(), dispatch, (handle) =>
      handles.push(handle),
    );

    expect(handles).toHaveLength(1);
    handles[0]?.cancel();
    expect(cancel).toHaveBeenCalled();
  });
});

describe("describe", () => {
  it("prefers the API's message", () => {
    expect(describeError(apiError("ASSET_INVALID", "Not a real image."))).toBe(
      "Not a real image.",
    );
  });

  it("falls back to a plain Error's message", () => {
    expect(describeError(new Error("network down"))).toBe("network down");
  });

  it("has something to say about a non-Error", () => {
    expect(describeError("nope")).toBe("The upload failed.");
  });
});
