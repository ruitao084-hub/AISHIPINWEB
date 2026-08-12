"use client";

/**
 * Protected layout (P3-T10).
 *
 * This gate is a **redirect, not a security boundary**. §40 is explicit that
 * hiding UI is not access control — every request the pages below make is
 * authorised independently by the server, and this layout would be pointless
 * as protection since anyone can run the bundle without it. What it does is
 * spare a signed-out visitor a screen full of failed requests.
 *
 * Redirecting only happens once `status` leaves `loading`: the access token
 * lives in memory, so a fresh page load legitimately has no credential until
 * the refresh cookie has been exchanged.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuth } from "@/lib/auth/auth-context";

export default function AppLayout({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { status, user, logout } = useAuth();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/login");
    }
  }, [status, router]);

  if (status === "loading") {
    return (
      <main className="flex flex-1 items-center justify-center px-6 py-16">
        <p className="text-muted text-sm" role="status">
          Loading your workspace…
        </p>
      </main>
    );
  }

  if (status === "anonymous" || user === null) {
    // The redirect is already scheduled; render nothing rather than flashing
    // application chrome to someone who is about to be sent to /login.
    return null;
  }

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="border-border flex items-center justify-between border-b px-6 py-3">
        <div className="flex items-center gap-6">
          <span className="text-sm font-semibold tracking-tight">
            AI Product Video Studio
          </span>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/app" className="underline-offset-4 hover:underline">
              Workspaces
            </Link>
            <Link
              href="/app/products"
              className="underline-offset-4 hover:underline"
            >
              Products
            </Link>
            <Link
              href="/app/projects"
              className="underline-offset-4 hover:underline"
            >
              Projects
            </Link>
            <Link
              href="/app/media"
              className="underline-offset-4 hover:underline"
            >
              Media
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-4">
          <span className="text-muted text-sm">{user.display_name}</span>
          <button
            type="button"
            onClick={() => void logout()}
            className="text-sm underline underline-offset-4"
          >
            Sign out
          </button>
        </div>
      </header>
      <div className="flex flex-1 flex-col">{children}</div>
    </div>
  );
}
