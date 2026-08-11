"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthFooterLink, AuthForm, Field } from "@/components/auth-form";
import { useAuth } from "@/lib/auth/auth-context";

export default function LoginPage() {
  const router = useRouter();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <AuthForm
      title="Sign in"
      subtitle="Continue to AI Product Video Studio."
      submitLabel="Sign in"
      pendingLabel="Signing in…"
      onSubmit={async () => {
        await login(email, password);
        router.push("/app");
      }}
      footer={
        <AuthFooterLink
          href="/register"
          prompt="No account yet?"
          action="Create one"
        />
      }
    >
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
        autoComplete="current-password"
      />
    </AuthForm>
  );
}
