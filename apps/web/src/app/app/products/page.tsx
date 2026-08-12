"use client";

/**
 * Product list and creation (P5-T08).
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  productApi,
  workspaceApi,
  type ProductResponse,
  type WorkspaceResponse,
} from "@/lib/api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ready";
      workspace: WorkspaceResponse;
      products: ProductResponse[];
    };

export default function ProductsPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const workspaces = await workspaceApi.list();
        const workspace = workspaces[0];
        if (!workspace)
          throw new Error("You do not belong to a workspace yet.");
        const products = await productApi.list(workspace.id);
        if (!cancelled) setState({ kind: "ready", workspace, products });
      } catch (error) {
        if (cancelled) return;
        setState({ kind: "error", message: describe(error) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const createProduct = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (state.kind !== "ready") return;

      setSubmitting(true);
      setFormError(null);
      try {
        await productApi.create(state.workspace.id, { name, category });
        const products = await productApi.list(state.workspace.id);
        setState({ ...state, products });
        setName("");
        setCategory("");
      } catch (error) {
        setFormError(describe(error));
      } finally {
        setSubmitting(false);
      }
    },
    [state, name, category],
  );

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">Products</h1>
      <p className="text-muted mt-1 text-sm">
        Each product carries its own verified facts and approved claims.
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
          <form
            onSubmit={createProduct}
            className="border-border mt-8 grid gap-3 rounded-xl border p-4"
          >
            <h2 className="text-sm font-medium">New product</h2>
            <div className="grid gap-3 sm:grid-cols-2">
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
              <label className="grid gap-1 text-sm">
                <span className="text-muted text-xs">Category</span>
                <input
                  value={category}
                  onChange={(event) => setCategory(event.target.value)}
                  required
                  maxLength={120}
                  className="border-border rounded-md border bg-transparent px-2 py-1.5 text-sm"
                />
              </label>
            </div>
            {formError && (
              <p role="alert" className="text-xs">
                {formError}
              </p>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="border-border justify-self-start rounded-md border px-3 py-1.5 text-sm disabled:opacity-50"
            >
              {submitting ? "Creating…" : "Create product"}
            </button>
          </form>

          {state.products.length === 0 ? (
            <p className="text-muted mt-8 text-sm">No products yet.</p>
          ) : (
            <ul className="mt-8 grid gap-2">
              {state.products.map((product) => (
                <li key={product.id}>
                  <Link
                    href={`/app/products/${product.id}`}
                    className="border-border flex items-baseline justify-between gap-4 rounded-lg border px-3 py-2 hover:bg-black/5 dark:hover:bg-white/5"
                  >
                    <span className="truncate text-sm font-medium">
                      {product.name}
                    </span>
                    <span className="text-muted shrink-0 text-xs tracking-wide uppercase">
                      {product.status}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </main>
  );
}

function describe(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
