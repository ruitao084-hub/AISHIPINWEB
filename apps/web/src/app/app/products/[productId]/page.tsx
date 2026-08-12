"use client";

/**
 * Product detail: imagery, analysis and review (P5-T08, P6-T08).
 *
 * The verification controls are the point of this screen. §13 requires a
 * person to confirm every fact before it can be used, so the review state is
 * shown on every row rather than hidden behind a filter — an unconfirmed fact
 * that *looks* confirmed is the failure this whole layer exists to prevent.
 *
 * P6-T08 adds the third disposition: **Edit + Verify**. A model that got a
 * material almost right is the common case, and without it a reviewer's only
 * options are to accept something wrong or to reject and retype it. The edit
 * goes through the same `verify` flag the API already honours, so a corrected
 * fact is recorded as USER_PROVIDED with the reviewer's name on it — not as
 * something the AI got right.
 */

import { useCallback, useEffect, useState } from "react";

import { MediaUploader } from "@/components/media-uploader";
import {
  ApiError,
  productApi,
  workspaceApi,
  type ClaimType,
  type FactType,
  type ProductAnalysisResponse,
  type ProductAssetResponse,
  type ProductClaimResponse,
  type ProductFactResponse,
  type ProductResponse,
} from "@/lib/api/client";
import type { UploadItem } from "@/lib/uploads/upload-queue";

interface Loaded {
  workspaceId: string;
  product: ProductResponse;
  assets: ProductAssetResponse[];
  facts: ProductFactResponse[];
  claims: ProductClaimResponse[];
  analyses: ProductAnalysisResponse[];
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | ({ kind: "ready" } & Loaded);

const FACT_TYPES: FactType[] = [
  "SPEC",
  "MATERIAL",
  "FEATURE",
  "PERFORMANCE",
  "CERTIFICATION",
  "INGREDIENT",
  "COMPATIBILITY",
  "WARRANTY",
  "PRICING",
  "APPEARANCE",
  "OTHER",
];

const CLAIM_TYPES: ClaimType[] = [
  "FUNCTIONAL",
  "PERFORMANCE",
  "COMPARATIVE",
  "CERTIFICATION",
  "SAFETY",
  "EMOTIONAL",
];

export default function ProductDetailPage({
  params,
}: {
  params: Promise<{ productId: string }>;
}) {
  const [productId, setProductId] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [actionError, setActionError] = useState<string | null>(null);
  // Guards the analyse button. The call costs money and takes seconds, so a
  // double-click without this is two billable requests.
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    void params.then(({ productId: id }) => setProductId(id));
  }, [params]);

  const load = useCallback(async (id: string): Promise<Loaded> => {
    const workspaces = await workspaceApi.list();
    const workspace = workspaces[0];
    if (!workspace) throw new Error("You do not belong to a workspace yet.");

    const [product, assets, facts, claims, analyses] = await Promise.all([
      productApi.get(workspace.id, id),
      productApi.assets(workspace.id, id),
      productApi.facts(workspace.id, id),
      productApi.claims(workspace.id, id),
      productApi.analyses(workspace.id, id),
    ]);
    return {
      workspaceId: workspace.id,
      product,
      assets,
      facts,
      claims,
      analyses,
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!productId) return;
    try {
      setState({ kind: "ready", ...(await load(productId)) });
    } catch (error) {
      setState({ kind: "error", message: describe(error) });
    }
  }, [productId, load]);

  // The initial load runs its own async body rather than calling `refresh`:
  // `refresh` sets state synchronously on the effect's first tick, which
  // cascades renders. Awaiting first is both correct and what the other pages
  // do.
  useEffect(() => {
    if (!productId) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await load(productId);
        if (!cancelled) setState({ kind: "ready", ...loaded });
      } catch (error) {
        if (!cancelled) setState({ kind: "error", message: describe(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productId, load]);

  /** Run a mutation, surface its error, then reload. */
  const act = useCallback(
    async (operation: () => Promise<unknown>) => {
      setActionError(null);
      try {
        await operation();
        await refresh();
      } catch (error) {
        // The server's message says *why* — "none of the facts this claim
        // cites have been verified" is actionable in a way that a generic
        // failure notice is not.
        setActionError(describe(error));
      }
    },
    [refresh],
  );

  const onUploaded = useCallback(
    (item: UploadItem) => {
      if (state.kind !== "ready") return;
      const asset = item.asset;
      if (!asset) return;
      const { workspaceId, product } = state;
      void act(() => productApi.attachAsset(workspaceId, product.id, asset.id));
    },
    [state, act],
  );

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

  const { workspaceId, product, assets, facts, claims, analyses } = state;
  const latestAnalysis = analyses[0] ?? null;
  const imageCount = assets.length;
  const verifiedFacts = facts.filter(
    (fact) => fact.verification_status === "VERIFIED",
  );

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">
          {product.name}
        </h1>
        <span className="text-muted shrink-0 text-xs tracking-wide uppercase">
          {product.status}
        </span>
      </div>
      <p className="text-muted mt-1 text-sm">{product.category}</p>

      {actionError && (
        <p
          role="alert"
          className="border-border mt-6 rounded-md border px-3 py-2 text-sm"
        >
          {actionError}
        </p>
      )}

      {/* --- imagery --- */}
      <section className="mt-10">
        <h2 className="text-sm font-medium tracking-wide uppercase">Images</h2>
        {assets.length > 0 && (
          <ul className="mt-3 grid gap-2">
            {assets.map((asset) => (
              <li
                key={asset.id}
                className="border-border flex items-center justify-between gap-4 rounded-lg border px-3 py-2"
              >
                <span className="truncate text-sm">
                  {asset.original_filename ?? asset.media_asset_id}
                  {asset.is_primary && (
                    <span className="text-muted ml-2 text-xs">primary</span>
                  )}
                </span>
                <span className="flex shrink-0 gap-3 text-xs">
                  {!asset.is_primary && (
                    <button
                      type="button"
                      className="underline"
                      onClick={() =>
                        void act(() =>
                          productApi.setPrimaryAsset(
                            workspaceId,
                            product.id,
                            asset.id,
                          ),
                        )
                      }
                    >
                      Make primary
                    </button>
                  )}
                  <button
                    type="button"
                    className="text-muted underline"
                    onClick={() =>
                      void act(() =>
                        productApi.detachAsset(
                          workspaceId,
                          product.id,
                          asset.id,
                        ),
                      )
                    }
                  >
                    Remove
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4">
          <MediaUploader workspaceId={workspaceId} onUploaded={onUploaded} />
        </div>
      </section>

      {/* --- analysis (P6-T08) --- */}
      <section className="mt-12">
        <div className="flex items-baseline justify-between gap-4">
          <h2 className="text-sm font-medium tracking-wide uppercase">
            AI analysis
          </h2>
          <button
            type="button"
            disabled={analyzing || imageCount === 0}
            className="border-border rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
            onClick={() => {
              setAnalyzing(true);
              void act(() =>
                productApi.analyze(workspaceId, product.id),
              ).finally(() => setAnalyzing(false));
            }}
          >
            {analyzing
              ? "Analysing…"
              : latestAnalysis
                ? "Re-run analysis"
                : "Analyse images"}
          </button>
        </div>
        <p className="text-muted mt-1 text-xs">
          {imageCount === 0
            ? "Attach at least one image first."
            : "Everything the model finds arrives unverified. Nothing below is usable until you confirm it."}
        </p>

        {latestAnalysis && (
          <AnalysisSummary analysis={latestAnalysis} product={product} />
        )}
      </section>

      {/* --- facts --- */}
      <section className="mt-12">
        <h2 className="text-sm font-medium tracking-wide uppercase">Facts</h2>
        <p className="text-muted mt-1 text-xs">
          Only verified facts can back a claim or reach a generated script.
        </p>

        {facts.length > 0 && (
          <ul className="mt-3 grid gap-2">
            {facts.map((fact) => (
              <FactRow
                key={fact.id}
                fact={fact}
                onVerify={() =>
                  void act(() =>
                    productApi.verifyFact(workspaceId, product.id, fact.id),
                  )
                }
                onReject={() =>
                  void act(() =>
                    productApi.rejectFact(workspaceId, product.id, fact.id),
                  )
                }
                onEditAndVerify={(valueText) =>
                  void act(() =>
                    // `verify: true` in the same call: a reviewer who corrected
                    // a value has taken responsibility for it, and a separate
                    // verify step would leave a window where the corrected fact
                    // sits unconfirmed.
                    productApi.updateFact(workspaceId, product.id, fact.id, {
                      value_text: valueText,
                      verify: true,
                    }),
                  )
                }
              />
            ))}
          </ul>
        )}

        <NewFactForm
          onSubmit={(payload) =>
            act(() => productApi.createFact(workspaceId, product.id, payload))
          }
        />
      </section>

      {/* --- claims --- */}
      <section className="mt-12">
        <h2 className="text-sm font-medium tracking-wide uppercase">Claims</h2>
        <p className="text-muted mt-1 text-xs">
          A claim that states something about the product needs a verified fact
          behind it before it can be approved.
        </p>

        {claims.length > 0 && (
          <ul className="mt-3 grid gap-2">
            {claims.map((claim) => (
              <ClaimRow
                key={claim.id}
                claim={claim}
                onVerify={() =>
                  void act(() =>
                    productApi.verifyClaim(workspaceId, product.id, claim.id),
                  )
                }
                onReject={() =>
                  void act(() =>
                    productApi.rejectClaim(workspaceId, product.id, claim.id),
                  )
                }
              />
            ))}
          </ul>
        )}

        <NewClaimForm
          facts={verifiedFacts}
          onSubmit={(payload) =>
            act(() => productApi.createClaim(workspaceId, product.id, payload))
          }
        />
      </section>

      {product.status !== "READY" && product.status !== "ARCHIVED" && (
        <button
          type="button"
          className="border-border mt-12 rounded-md border px-3 py-1.5 text-sm"
          onClick={() =>
            void act(() => productApi.markReady(workspaceId, product.id))
          }
        >
          Mark product ready
        </button>
      )}
    </main>
  );
}

/**
 * A summary of the most recent analysis run (§14, §15).
 *
 * Deliberately shows the provenance — provider, model, prompt version — beside
 * the result. §15's point in recording those is that somebody can later ask
 * "what produced this?", and a record nobody can see does not answer that.
 */
function AnalysisSummary({
  analysis,
  product,
}: {
  analysis: ProductAnalysisResponse;
  product: ProductResponse;
}) {
  const dna = product.visual_dna as Record<string, string[] | undefined>;
  const tone = dna.tone ?? [];
  const palette = dna.palette ?? [];

  return (
    <div className="border-border mt-3 grid gap-3 rounded-xl border p-4">
      <p className="text-sm">
        {analysis.status === "SUCCEEDED"
          ? `${analysis.created_fact_count} observations and ${analysis.created_claim_count} suggestions, all awaiting review.`
          : `Last run ${analysis.status.toLowerCase()}${
              analysis.error_code ? ` (${analysis.error_code})` : ""
            }.`}
      </p>

      {product.ai_summary && (
        <p className="text-muted text-sm">{product.ai_summary}</p>
      )}

      {(tone.length > 0 || palette.length > 0) && (
        <p className="text-muted text-xs">
          Visual direction: {[...tone, ...palette].join(" · ")}
        </p>
      )}

      <p className="text-muted text-xs">
        {analysis.provider}
        {analysis.model ? ` · ${analysis.model}` : ""} ·{" "}
        {analysis.prompt_key} v{analysis.prompt_version} ·{" "}
        {analysis.analyzed_asset_ids.length} image
        {analysis.analyzed_asset_ids.length === 1 ? "" : "s"}
        {analysis.latency_ms === null ? "" : ` · ${analysis.latency_ms}ms`}
      </p>
    </div>
  );
}

function FactRow({
  fact,
  onVerify,
  onReject,
  onEditAndVerify,
}: {
  fact: ProductFactResponse;
  onVerify: () => void;
  onReject: () => void;
  onEditAndVerify: (valueText: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(fact.value_text);

  const settled =
    fact.verification_status === "VERIFIED" ||
    fact.verification_status === "REJECTED";
  const fromAI = fact.source_type.startsWith("AI_");

  if (editing) {
    return (
      <li className="border-border rounded-lg border px-3 py-2">
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setEditing(false);
            onEditAndVerify(draft);
          }}
        >
          <label className="grid gap-1 text-sm">
            <span className="text-muted text-xs">{fact.key}</span>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              required
              // The row was replaced by this form on the reviewer's own click,
              // so focus belongs where they were already looking.
              autoFocus
              className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
            />
          </label>
          <span className="flex gap-3 text-xs">
            <button type="submit" className="underline">
              Save and verify
            </button>
            <button
              type="button"
              className="text-muted underline"
              onClick={() => {
                setDraft(fact.value_text);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </span>
        </form>
      </li>
    );
  }

  return (
    <li className="border-border flex items-center justify-between gap-4 rounded-lg border px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm">
          <span className="text-muted">{fact.key}</span> — {fact.value_text}
        </p>
        <p className="text-muted mt-0.5 text-xs">
          {fact.fact_type} · {fact.verification_status}
          {fromAI && fact.verification_status === "AI_INFERRED"
            ? " · the AI observed this, nobody has confirmed it"
            : ""}
        </p>
      </div>
      <span className="flex shrink-0 gap-3 text-xs">
        {fact.verification_status !== "VERIFIED" && (
          <button type="button" className="underline" onClick={onVerify}>
            Verify
          </button>
        )}
        {!settled && (
          <button
            type="button"
            className="underline"
            onClick={() => setEditing(true)}
          >
            Edit
          </button>
        )}
        {!settled && (
          <button
            type="button"
            className="text-muted underline"
            onClick={onReject}
          >
            Reject
          </button>
        )}
      </span>
    </li>
  );
}

function ClaimRow({
  claim,
  onVerify,
  onReject,
}: {
  claim: ProductClaimResponse;
  onVerify: () => void;
  onReject: () => void;
}) {
  return (
    <li className="border-border flex items-center justify-between gap-4 rounded-lg border px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm">{claim.claim_text}</p>
        <p className="text-muted mt-0.5 text-xs">
          {claim.claim_type} · {claim.status} · risk {claim.risk_level} ·{" "}
          {claim.source_fact_ids.length} cited
        </p>
      </div>
      <span className="flex shrink-0 gap-3 text-xs">
        {claim.status !== "VERIFIED" && (
          <button type="button" className="underline" onClick={onVerify}>
            Approve
          </button>
        )}
        {claim.status !== "REJECTED" && (
          <button
            type="button"
            className="text-muted underline"
            onClick={onReject}
          >
            Reject
          </button>
        )}
      </span>
    </li>
  );
}

function NewFactForm({
  onSubmit,
}: {
  onSubmit: (payload: {
    fact_type: FactType;
    key: string;
    value_text: string;
    verify: boolean;
  }) => Promise<void>;
}) {
  const [factType, setFactType] = useState<FactType>("FEATURE");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");

  return (
    <form
      className="border-border mt-4 grid gap-3 rounded-xl border p-4"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSubmit({
          fact_type: factType,
          key,
          value_text: value,
          verify: false,
        });
        setKey("");
        setValue("");
      }}
    >
      <h3 className="text-sm font-medium">Add a fact</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Type</span>
          <select
            value={factType}
            onChange={(event) => setFactType(event.target.value as FactType)}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          >
            {FACT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Key</span>
          <input
            value={key}
            onChange={(event) => setKey(event.target.value)}
            required
            maxLength={120}
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-muted text-xs">Value</span>
          <input
            value={value}
            onChange={(event) => setValue(event.target.value)}
            required
            className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
          />
        </label>
      </div>
      <button
        type="submit"
        className="border-border justify-self-start rounded-md border px-3 py-1.5 text-sm"
      >
        Add fact
      </button>
    </form>
  );
}

function NewClaimForm({
  facts,
  onSubmit,
}: {
  facts: ProductFactResponse[];
  onSubmit: (payload: {
    claim_text: string;
    claim_type: ClaimType;
    source_fact_ids: string[];
  }) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [claimType, setClaimType] = useState<ClaimType>("FUNCTIONAL");
  const [cited, setCited] = useState<string[]>([]);

  return (
    <form
      className="border-border mt-4 grid gap-3 rounded-xl border p-4"
      onSubmit={async (event) => {
        event.preventDefault();
        await onSubmit({
          claim_text: text,
          claim_type: claimType,
          source_fact_ids: cited,
        });
        setText("");
        setCited([]);
      }}
    >
      <h3 className="text-sm font-medium">Propose a claim</h3>
      <label className="grid gap-1 text-sm">
        <span className="text-muted text-xs">Claim</span>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          required
          className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
        />
      </label>
      <label className="grid gap-1 text-sm">
        <span className="text-muted text-xs">Type</span>
        <select
          value={claimType}
          onChange={(event) => setClaimType(event.target.value as ClaimType)}
          className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
        >
          {CLAIM_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>

      {facts.length > 0 && (
        <fieldset className="grid gap-1">
          <legend className="text-muted text-xs">
            Supporting verified facts
          </legend>
          {facts.map((fact) => (
            <label key={fact.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={cited.includes(fact.id)}
                onChange={(event) =>
                  setCited((current) =>
                    event.target.checked
                      ? [...current, fact.id]
                      : current.filter((id) => id !== fact.id),
                  )
                }
              />
              <span className="truncate">
                {fact.key} — {fact.value_text}
              </span>
            </label>
          ))}
        </fieldset>
      )}

      <button
        type="submit"
        className="border-border justify-self-start rounded-md border px-3 py-1.5 text-sm"
      >
        Propose claim
      </button>
    </form>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
