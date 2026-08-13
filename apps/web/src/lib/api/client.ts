/**
 * Typed HTTP client for the AIPVS API.
 *
 * Every request and response type comes from `@aipvs/shared-types`, which is
 * generated from the API's OpenAPI document (taskbook §5.2). Hand-written
 * shapes are exactly the drift that pipeline exists to prevent, so nothing
 * here declares its own.
 *
 * Two rules this module enforces on behalf of every caller:
 *
 * 1. **The access token lives in memory, never in `localStorage`.** A token in
 *    web storage is readable by any injected script; keeping it in a module
 *    variable means an XSS bug has to run while the tab is open rather than
 *    harvesting a credential at leisure. The refresh token is an HttpOnly
 *    cookie the client never sees at all (§39).
 * 2. **A 401 triggers exactly one refresh attempt**, and concurrent callers
 *    share it. Without the sharing, five parallel requests hitting an expired
 *    token would fire five refreshes — and because refresh rotates, four of
 *    them would be rejected and log the user out.
 */

import type { ErrorResponse, Schemas } from "@aipvs/shared-types";
import { isErrorResponse } from "@aipvs/shared-types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type TokenResponse = Schemas["TokenResponse"];
export type UserResponse = Schemas["UserResponse"];
export type WorkspaceResponse = Schemas["WorkspaceResponse"];
export type MemberResponse = Schemas["MemberResponse"];
export type RegisterRequest = Schemas["RegisterRequest"];
export type LoginRequest = Schemas["LoginRequest"];
export type MediaAssetResponse = Schemas["MediaAssetResponse"];
export type MediaAssetDetailResponse = Schemas["MediaAssetDetailResponse"];
export type PresignRequest = Schemas["PresignRequest"];
export type PresignResponse = Schemas["PresignResponse"];
export type UploadConfigResponse = Schemas["UploadConfigResponse"];
export type AssetType = Schemas["AssetType"];
export type ProductResponse = Schemas["ProductResponse"];
export type ProductAssetResponse = Schemas["ProductAssetResponse"];
export type ProductFactResponse = Schemas["ProductFactResponse"];
export type ProductClaimResponse = Schemas["ProductClaimResponse"];
export type CreateProductRequest = Schemas["CreateProductRequest"];
export type CreateFactRequest = Schemas["CreateFactRequest"];
export type CreateClaimRequest = Schemas["CreateClaimRequest"];
export type UpdateFactRequest = Schemas["UpdateFactRequest"];
export type FactType = Schemas["FactType"];
export type ClaimType = Schemas["ClaimType"];
export type VerificationStatus = Schemas["VerificationStatus"];
export type ClaimStatus = Schemas["ClaimStatus"];
export type ProductAssetRole = Schemas["ProductAssetRole"];
export type ProductAnalysisResponse = Schemas["ProductAnalysisResponse"];
export type AnalysisStatus = Schemas["AnalysisStatus"];
export type ProjectResponse = Schemas["ProjectResponse"];
export type CreativePlanResponse = Schemas["CreativePlanResponse"];
export type ScriptResponse = Schemas["ScriptResponse"];
export type CreateProjectRequest = Schemas["CreateProjectRequest"];
export type ProjectStatus = Schemas["ProjectStatus"];
export type ProjectPurpose = Schemas["ProjectPurpose"];
export type TargetPlatform = Schemas["TargetPlatform"];
export type AspectRatio = Schemas["AspectRatio"];
export type VideoStyle = Schemas["VideoStyle"];
export type QualityMode = Schemas["QualityMode"];
export type StoryboardResponse = Schemas["StoryboardResponse"];
export type ShotResponse = Schemas["ShotResponse"];
export type ShotType = Schemas["ShotType"];
export type TransitionType = Schemas["TransitionType"];
export type UpdateShotRequest = Schemas["UpdateShotRequest"];
export type JobResponse = Schemas["JobResponse"];
export type JobStatus = Schemas["JobStatus"];
export type JobType = Schemas["JobType"];
export type RenderResponse = Schemas["RenderResponse"];
export type RenderStartedResponse = Schemas["RenderStartedResponse"];
export type RenderRequest = Schemas["RenderRequest"];
export type VoiceoverResponse = Schemas["VoiceoverResponse"];
export type QualityCheckResponse = Schemas["QualityCheckResponse"];
export type DownloadResponse = Schemas["DownloadResponse"];
export type CreditAccountResponse = Schemas["CreditAccountResponse"];
export type CreditTransactionResponse = Schemas["CreditTransactionResponse"];
export type CostEstimateResponse = Schemas["CostEstimateResponse"];

/** Job states that will never change again (§106). Polling stops at these. */
export const TERMINAL_JOB_STATUSES: readonly JobStatus[] = [
  "COMPLETED",
  "FAILED",
  "CANCELED",
  "TIMEOUT",
];

export function isJobFinished(job: JobResponse): boolean {
  return TERMINAL_JOB_STATUSES.includes(job.status);
}

/** An error carrying the API's `code`, so callers branch on it rather than text. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, body: ErrorResponse) {
    super(body.error.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.error.code;
    this.requestId = body.error.request_id ?? null;
    this.details = (body.error.details as Record<string, unknown>) ?? null;
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** In-flight refresh, shared so concurrent 401s do not each rotate the token. */
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  refreshInFlight ??= (async () => {
    try {
      const response = await fetch(`${API_URL}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!response.ok) return null;
      const body = (await response.json()) as TokenResponse;
      setAccessToken(body.access_token);
      return body.access_token;
    } catch {
      return null;
    } finally {
      // Cleared in a microtask so callers awaiting this promise all observe
      // the same result before the next 401 can start a fresh attempt.
      queueMicrotask(() => {
        refreshInFlight = null;
      });
    }
  })();

  return refreshInFlight;
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Internal: prevents a refresh loop when the refresh itself 401s. */
  retryOnUnauthorized?: boolean;
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (isErrorResponse(body)) {
    return new ApiError(response.status, body);
  }
  // A proxy or gateway can fail before ever reaching our handlers, so the
  // envelope is not guaranteed — synthesise one rather than throwing raw text.
  return new ApiError(response.status, {
    error: {
      code: "INTERNAL_ERROR",
      message: `Request failed with status ${response.status}.`,
      request_id: null,
    },
  } as ErrorResponse);
}

export async function apiRequest<T>(
  path: string,
  { method = "GET", body, retryOnUnauthorized = true }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const init: RequestInit = {
    method,
    headers,
    // Sends the HttpOnly refresh cookie; the access token travels in the
    // Authorization header rather than a cookie, keeping it clear of CSRF.
    credentials: "include",
  };
  // Assigned only when present: `exactOptionalPropertyTypes` distinguishes an
  // absent property from one explicitly set to `undefined`, and `RequestInit`
  // does not accept the latter.
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_URL}${path}`, init);

  if (response.status === 401 && retryOnUnauthorized) {
    const renewed = await refreshAccessToken();
    if (renewed) {
      return apiRequest<T>(path, { method, body, retryOnUnauthorized: false });
    }
    setAccessToken(null);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// --- Auth ------------------------------------------------------------------

export const authApi = {
  register: (payload: RegisterRequest) =>
    apiRequest<TokenResponse>("/api/v1/auth/register", {
      method: "POST",
      body: payload,
    }),

  login: (payload: LoginRequest) =>
    apiRequest<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: payload,
    }),

  logout: () =>
    apiRequest<void>("/api/v1/auth/logout", {
      method: "POST",
      retryOnUnauthorized: false,
    }),

  me: () => apiRequest<UserResponse>("/api/v1/auth/me"),

  /** Restore a session from the refresh cookie on a cold page load. */
  refresh: async (): Promise<string | null> => refreshAccessToken(),
};

// --- Workspaces ------------------------------------------------------------

export const workspaceApi = {
  list: () => apiRequest<WorkspaceResponse[]>("/api/v1/workspaces"),

  get: (id: string) =>
    apiRequest<WorkspaceResponse>(`/api/v1/workspaces/${id}`),

  create: (name: string) =>
    apiRequest<WorkspaceResponse>("/api/v1/workspaces", {
      method: "POST",
      body: { name },
    }),

  members: (id: string) =>
    apiRequest<MemberResponse[]>(`/api/v1/workspaces/${id}/members`),
};

// --- Uploads and media (§12) -----------------------------------------------

export const uploadApi = {
  /** What this deployment accepts. Fetched rather than hardcoded so the
   * picker's filter and the server's whitelist cannot drift apart. */
  config: (workspaceId: string) =>
    apiRequest<UploadConfigResponse>(
      `/api/v1/workspaces/${workspaceId}/uploads/config`,
    ),

  presign: (workspaceId: string, payload: PresignRequest) =>
    apiRequest<PresignResponse>(
      `/api/v1/workspaces/${workspaceId}/uploads/presign`,
      { method: "POST", body: payload },
    ),

  complete: (workspaceId: string, assetId: string) =>
    apiRequest<MediaAssetResponse>(
      `/api/v1/workspaces/${workspaceId}/uploads/${assetId}/complete`,
      { method: "POST" },
    ),

  list: (workspaceId: string, assetType?: AssetType) => {
    const query = assetType ? `?asset_type=${assetType}` : "";
    return apiRequest<MediaAssetResponse[]>(
      `/api/v1/workspaces/${workspaceId}/assets${query}`,
    );
  },

  get: (workspaceId: string, assetId: string) =>
    apiRequest<MediaAssetDetailResponse>(
      `/api/v1/workspaces/${workspaceId}/assets/${assetId}`,
    ),
};

// --- Products and the Truth Layer (§13, §109) ------------------------------

export const productApi = {
  list: (workspaceId: string) =>
    apiRequest<ProductResponse[]>(`/api/v1/workspaces/${workspaceId}/products`),

  get: (workspaceId: string, productId: string) =>
    apiRequest<ProductResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}`,
    ),

  create: (workspaceId: string, payload: CreateProductRequest) =>
    apiRequest<ProductResponse>(`/api/v1/workspaces/${workspaceId}/products`, {
      method: "POST",
      body: payload,
    }),

  markReady: (workspaceId: string, productId: string) =>
    apiRequest<ProductResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/ready`,
      { method: "POST" },
    ),

  // -- imagery --
  assets: (workspaceId: string, productId: string) =>
    apiRequest<ProductAssetResponse[]>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/assets`,
    ),

  attachAsset: (
    workspaceId: string,
    productId: string,
    mediaAssetId: string,
    assetRole: ProductAssetRole = "OTHER",
  ) =>
    apiRequest<ProductAssetResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/assets`,
      {
        method: "POST",
        body: { media_asset_id: mediaAssetId, asset_role: assetRole },
      },
    ),

  setPrimaryAsset: (workspaceId: string, productId: string, linkId: string) =>
    apiRequest<ProductAssetResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/assets/${linkId}/primary`,
      { method: "POST" },
    ),

  detachAsset: (workspaceId: string, productId: string, linkId: string) =>
    apiRequest<void>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/assets/${linkId}`,
      { method: "DELETE" },
    ),

  // -- facts --
  facts: (workspaceId: string, productId: string) =>
    apiRequest<ProductFactResponse[]>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/facts`,
    ),

  createFact: (
    workspaceId: string,
    productId: string,
    payload: CreateFactRequest,
  ) =>
    apiRequest<ProductFactResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/facts`,
      { method: "POST", body: payload },
    ),

  updateFact: (
    workspaceId: string,
    productId: string,
    factId: string,
    payload: UpdateFactRequest,
  ) =>
    apiRequest<ProductFactResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/facts/${factId}`,
      { method: "PATCH", body: payload },
    ),

  verifyFact: (workspaceId: string, productId: string, factId: string) =>
    apiRequest<ProductFactResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/facts/${factId}/verify`,
      { method: "POST" },
    ),

  rejectFact: (workspaceId: string, productId: string, factId: string) =>
    apiRequest<ProductFactResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/facts/${factId}/reject`,
      { method: "POST" },
    ),

  // -- claims --
  claims: (workspaceId: string, productId: string) =>
    apiRequest<ProductClaimResponse[]>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/claims`,
    ),

  /**
   * §109's `get_verified_claims`: the only claims a script may use.
   *
   * A distinct call rather than a filter over `claims`, so "safe to broadcast"
   * is something a caller asks for rather than something it has to remember
   * to apply.
   */
  verifiedClaims: (workspaceId: string, productId: string) =>
    apiRequest<ProductClaimResponse[]>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/claims/verified`,
    ),

  createClaim: (
    workspaceId: string,
    productId: string,
    payload: CreateClaimRequest,
  ) =>
    apiRequest<ProductClaimResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/claims`,
      { method: "POST", body: payload },
    ),

  verifyClaim: (workspaceId: string, productId: string, claimId: string) =>
    apiRequest<ProductClaimResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/claims/${claimId}/verify`,
      { method: "POST" },
    ),

  rejectClaim: (workspaceId: string, productId: string, claimId: string) =>
    apiRequest<ProductClaimResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/claims/${claimId}/reject`,
      { method: "POST" },
    ),

  // -- analysis (§14) --

  /**
   * Run vision analysis over the product's images.
   *
   * Costs money and takes seconds, so callers must disable their trigger while
   * it is in flight — a double-click is two billable calls. PHASE 9 moves this
   * behind the job queue, at which point the response arrives PENDING and the
   * client polls instead of waiting.
   */
  analyze: (workspaceId: string, productId: string) =>
    apiRequest<ProductAnalysisResponse>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/analyze`,
      { method: "POST" },
    ),

  analyses: (workspaceId: string, productId: string) =>
    apiRequest<ProductAnalysisResponse[]>(
      `/api/v1/workspaces/${workspaceId}/products/${productId}/analyses`,
    ),
};

/**
 * Projects, creative plans and scripts (§16, §17).
 *
 * The two generation calls cost money and take seconds. Callers must disable
 * their trigger while one is in flight — a double-click is two billable
 * requests, and the API cannot tell them apart.
 */
export const projectApi = {
  list: (workspaceId: string, productId?: string) =>
    apiRequest<ProjectResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects` +
        (productId ? `?product_id=${productId}` : ""),
    ),

  get: (workspaceId: string, projectId: string) =>
    apiRequest<ProjectResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}`,
    ),

  create: (workspaceId: string, payload: CreateProjectRequest) =>
    apiRequest<ProjectResponse>(`/api/v1/workspaces/${workspaceId}/projects`, {
      method: "POST",
      body: payload,
    }),

  // -- creative plans (§16) --
  plans: (workspaceId: string, projectId: string) =>
    apiRequest<CreativePlanResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/creative-plans`,
    ),

  generatePlans: (workspaceId: string, projectId: string) =>
    apiRequest<CreativePlanResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/creative-plans`,
      { method: "POST" },
    ),

  selectPlan: (workspaceId: string, projectId: string, planId: string) =>
    apiRequest<CreativePlanResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/creative-plans/${planId}/select`,
      { method: "POST" },
    ),

  // -- scripts (§17) --
  scripts: (workspaceId: string, projectId: string) =>
    apiRequest<ScriptResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/scripts`,
    ),

  generateScript: (workspaceId: string, projectId: string) =>
    apiRequest<ScriptResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/scripts`,
      { method: "POST" },
    ),

  approveScript: (workspaceId: string, projectId: string, scriptId: string) =>
    apiRequest<ScriptResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/scripts/${scriptId}/approve`,
      { method: "POST" },
    ),

  // -- storyboards (§18, §19, §29) --

  storyboards: (workspaceId: string, projectId: string) =>
    apiRequest<StoryboardResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards`,
    ),

  generateStoryboard: (workspaceId: string, projectId: string) =>
    apiRequest<StoryboardResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards`,
      { method: "POST" },
    ),

  shots: (workspaceId: string, projectId: string, storyboardId: string) =>
    apiRequest<ShotResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards/${storyboardId}/shots`,
    ),

  /**
   * Edit a shot. Note what this cannot send: `visual_prompt`.
   *
   * §19 forbids handing a video model a sentence a user typed, so the prompt
   * is compiled from these fields and recompiled after every edit. The API
   * rejects a request carrying one, so a client that tried would find out
   * rather than believe it had worked.
   */
  updateShot: (
    workspaceId: string,
    projectId: string,
    storyboardId: string,
    shotId: string,
    payload: UpdateShotRequest,
  ) =>
    apiRequest<ShotResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards/${storyboardId}/shots/${shotId}`,
      { method: "PATCH", body: payload },
    ),

  approveStoryboard: (
    workspaceId: string,
    projectId: string,
    storyboardId: string,
  ) =>
    apiRequest<StoryboardResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards/${storyboardId}/approve`,
      { method: "POST" },
    ),
};

/**
 * Generation, composition and delivery (§22, §26, §34, §37).
 *
 * Every write here returns a job rather than a result. §26 asks for polling
 * and warns against reaching for WebSockets first, so `job()` is what the UI
 * calls on an interval until `isJobFinished` is true.
 */
export const generationApi = {
  // -- jobs (§22, §26) --

  job: (workspaceId: string, jobId: string) =>
    apiRequest<JobResponse>(`/api/v1/workspaces/${workspaceId}/jobs/${jobId}`),

  projectJobs: (workspaceId: string, projectId: string, jobType?: JobType) =>
    apiRequest<JobResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/jobs` +
        (jobType ? `?job_type=${jobType}` : ""),
    ),

  cancelJob: (workspaceId: string, jobId: string) =>
    apiRequest<JobResponse>(
      `/api/v1/workspaces/${workspaceId}/jobs/${jobId}/cancel`,
      { method: "POST" },
    ),

  // -- shots (§22) --

  generateShots: (
    workspaceId: string,
    projectId: string,
    storyboardId: string,
  ) =>
    apiRequest<JobResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards/${storyboardId}/generate`,
      { method: "POST" },
    ),

  generateShot: (
    workspaceId: string,
    projectId: string,
    storyboardId: string,
    shotId: string,
  ) =>
    apiRequest<JobResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/storyboards/${storyboardId}/shots/${shotId}/generate`,
      { method: "POST" },
    ),

  // -- voice (§30) --

  voiceover: (workspaceId: string, projectId: string) =>
    apiRequest<VoiceoverResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/voiceover`,
    ),

  generateVoiceover: (workspaceId: string, projectId: string) =>
    apiRequest<JobResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/voiceover`,
      { method: "POST" },
    ),

  // -- render (§34) --

  renders: (workspaceId: string, projectId: string) =>
    apiRequest<RenderResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/renders`,
    ),

  render: (workspaceId: string, projectId: string, renderId: string) =>
    apiRequest<RenderResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/renders/${renderId}`,
    ),

  createRender: (
    workspaceId: string,
    projectId: string,
    payload: RenderRequest = { burn_subtitles: true },
  ) =>
    apiRequest<RenderStartedResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/renders`,
      { method: "POST", body: payload },
    ),

  // -- quality checks (§37) --

  qualityChecks: (workspaceId: string, projectId: string, renderId?: string) =>
    apiRequest<QualityCheckResponse[]>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/quality-checks` +
        (renderId ? `?render_id=${renderId}` : ""),
    ),

  runQualityCheck: (workspaceId: string, projectId: string, renderId: string) =>
    apiRequest<JobResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/renders/${renderId}/quality-checks`,
      { method: "POST" },
    ),

  // -- delivery (§60) --

  /**
   * A short-lived link to the finished video.
   *
   * Fetched on click rather than held in state: the URL expires, and a link
   * rendered ten minutes ago would 403 by the time anyone pressed it.
   */
  download: (workspaceId: string, projectId: string, renderId?: string) =>
    apiRequest<DownloadResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/download` +
        (renderId ? `?render_id=${renderId}` : ""),
    ),
};

/** Balance, ledger and pre-generation quotes (§95). */
export const creditsApi = {
  balance: (workspaceId: string) =>
    apiRequest<CreditAccountResponse>(
      `/api/v1/workspaces/${workspaceId}/credits`,
    ),

  transactions: (workspaceId: string, limit = 100) =>
    apiRequest<CreditTransactionResponse[]>(
      `/api/v1/workspaces/${workspaceId}/credits/transactions?limit=${limit}`,
    ),

  /**
   * What generating this project will cost, before anything is spent.
   *
   * Reserves nothing — it is a quote. Fetched fresh each time the panel opens
   * because the storyboard\u2019s shots, and therefore the price, change as it is
   * edited.
   */
  estimate: (workspaceId: string, projectId: string) =>
    apiRequest<CostEstimateResponse>(
      `/api/v1/workspaces/${workspaceId}/projects/${projectId}/cost-estimate`,
    ),
};

export interface UploadTransfer {
  /** Resolves when storage has accepted the whole body. */
  done: Promise<void>;
  /** Aborts the transfer; `done` rejects with an `UploadAbortedError`. */
  cancel: () => void;
}

export class StorageUploadError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "StorageUploadError";
    this.status = status;
  }
}

export class UploadAbortedError extends Error {
  constructor() {
    super("Upload canceled.");
    this.name = "UploadAbortedError";
  }
}

/**
 * PUT a file straight to object storage.
 *
 * Uses `XMLHttpRequest` rather than `fetch` for one reason: `fetch` still has
 * no upload-progress event in any shipping browser, and a progress bar that
 * jumps from 0% to 100% on a 200 MB video is not a progress bar. The rest of
 * the client uses `fetch`; this is the one place the older API earns its keep.
 *
 * No credentials are attached. The URL carries its own signature (§12), and
 * sending cookies to the storage origin would be both useless and a leak.
 */
export function uploadToStorage(
  url: string,
  file: File,
  headers: Record<string, string>,
  onProgress?: (fraction: number) => void,
): UploadTransfer {
  const request = new XMLHttpRequest();

  const done = new Promise<void>((resolve, reject) => {
    request.open("PUT", url, true);
    for (const [name, value] of Object.entries(headers)) {
      request.setRequestHeader(name, value);
    }

    request.upload.addEventListener("progress", (event) => {
      // `lengthComputable` is false for a chunked body; reporting 0 then is
      // honest, where a fabricated estimate would not be.
      if (event.lengthComputable && event.total > 0) {
        onProgress?.(event.loaded / event.total);
      }
    });

    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress?.(1);
        resolve();
        return;
      }
      reject(
        new StorageUploadError(
          request.status,
          `Storage rejected the upload (HTTP ${request.status}).`,
        ),
      );
    });

    request.addEventListener("error", () => {
      // The browser withholds the cause of a cross-origin network failure, so
      // there is nothing more specific to say here than that it failed.
      reject(new StorageUploadError(0, "The connection to storage failed."));
    });

    request.addEventListener("abort", () => {
      reject(new UploadAbortedError());
    });

    request.send(file);
  });

  return {
    done,
    cancel: () => {
      request.abort();
    },
  };
}
