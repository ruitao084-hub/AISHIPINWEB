"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthFooterLink, AuthForm, Field } from "@/components/auth-form";
import { useAuth } from "@/lib/auth/auth-context";

/** Mirrors the server's policy so the rule is stated before submission, not after. */
const MIN_PASSWORD_LENGTH = 12;

export default function RegisterPage() {
  const router = useRouter();
  const { register } = useAuth();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <AuthForm
      title="Create an account"
      subtitle="Your workspace is set up automatically."
      submitLabel="Create account"
      pendingLabel="Creating account…"
      onSubmit={async () => {
        await register(email, password, displayName);
        router.push("/app");
      }}
      footer={
        <AuthFooterLink
          href="/login"
          prompt="Already have an account?"
          action="Sign in"
        />
      }
    >
      <Field
        id="display_name"
        label="Name"
        value={displayName}
        onChange={setDisplayName}
        autoComplete="name"
      />
      <Field
        id="email"
        label="Email"
        type="email"
        value={email}
        onChange={setEmail}
        autoComplete="email"
      />
      <Field
        id="password"
        label="Password"
        type="password"
        value={password}
        onChange={setPassword}
        autoComplete="new-password"
        minLength={MIN_PASSWORD_LENGTH}
        hint={`At least ${MIN_PASSWORD_LENGTH} characters. A memorable phrase beats a short complex string.`}
      />
    </AuthForm>
  );
}
