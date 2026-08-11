"use client";

/**
 * Shared form shell for sign-in and sign-up.
 *
 * §51 requires every async surface to handle loading, error and disabled
 * states rather than logging to the console — this is where that lives for
 * both auth screens, so neither can quietly omit one.
 */

import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";

import { cn } from "@aipvs/ui";

import { ApiError } from "@/lib/api/client";

interface AuthFormProps {
  title: string;
  subtitle: string;
  submitLabel: string;
  pendingLabel: string;
  onSubmit: () => Promise<void>;
  footer: ReactNode;
  children: ReactNode;
}

export function AuthForm({
  title,
  subtitle,
  submitLabel,
  pendingLabel,
  onSubmit,
  footer,
  children,
}: AuthFormProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await onSubmit();
    } catch (caught) {
      // The API's message is written to be shown to a user (§65); anything
      // else gets a generic line rather than leaking an internal string.
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-6 py-16">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-muted mt-2 text-sm">{subtitle}</p>
      </header>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {children}

        {error !== null && (
          // role="alert" so screen readers announce it without focus moving.
          <p
            role="alert"
            className="border-border rounded-md border px-3 py-2 text-sm"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={pending}
          className={cn(
            "mt-2 rounded-md px-4 py-2.5 text-sm font-medium",
            "bg-foreground text-background transition-opacity",
            "disabled:cursor-not-allowed disabled:opacity-60",
          )}
        >
          {pending ? pendingLabel : submitLabel}
        </button>
      </form>

      <p className="text-muted mt-6 text-sm">{footer}</p>
    </main>
  );
}

interface FieldProps {
  id: string;
  label: string;
  type?: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  required?: boolean;
  minLength?: number;
  hint?: string;
}

export function Field({
  id,
  label,
  type = "text",
  value,
  onChange,
  autoComplete,
  required = true,
  minLength,
  hint,
}: FieldProps) {
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className="flex flex-col gap-1.5">
      {/* An explicit label, not a placeholder: placeholders vanish on focus
          and are not reliably announced (§130). */}
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        required={required}
        minLength={minLength}
        aria-describedby={hintId}
        className={cn(
          "border-border rounded-md border bg-transparent px-3 py-2 text-sm",
          "outline-none focus-visible:ring-2 focus-visible:ring-current/30",
        )}
      />
      {hint && (
        <p id={hintId} className="text-muted text-xs">
          {hint}
        </p>
      )}
    </div>
  );
}

export function AuthFooterLink({
  href,
  prompt,
  action,
}: {
  href: string;
  prompt: string;
  action: string;
}) {
  return (
    <>
      {prompt}{" "}
      <Link href={href} className="underline underline-offset-4">
        {action}
      </Link>
    </>
  );
}
