"use client";

/**
 * Session state for the browser.
 *
 * The access token deliberately lives in memory (see `lib/api/client`), which
 * means a full page load starts with no credential. `bootstrap` recovers the
 * session from the HttpOnly refresh cookie — that round trip is why `status`
 * has a distinct `loading` state, and why the protected layout must wait for
 * it rather than redirecting the moment it sees no user.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  authApi,
  setAccessToken,
  type UserResponse,
} from "@/lib/api/client";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  user: UserResponse | null;
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    password: string,
    displayName: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<UserResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    // Recover a session from the refresh cookie. A failure here is the normal
    // case for a signed-out visitor, not an error worth surfacing.
    void (async () => {
      const token = await authApi.refresh();
      if (cancelled) return;

      if (!token) {
        setStatus("anonymous");
        return;
      }
      try {
        const me = await authApi.me();
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
      } catch {
        if (!cancelled) setStatus("anonymous");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await authApi.login({ email, password });
    setAccessToken(result.access_token);
    setUser(result.user);
    setStatus("authenticated");
  }, []);

  const register = useCallback(
    async (email: string, password: string, displayName: string) => {
      const result = await authApi.register({
        email,
        password,
        display_name: displayName,
      });
      setAccessToken(result.access_token);
      setUser(result.user);
      setStatus("authenticated");
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch (error) {
      // A failed revoke must not trap the user in a signed-in UI; the local
      // session is cleared either way and the token expires on its own.
      if (!(error instanceof ApiError)) throw error;
    } finally {
      setAccessToken(null);
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  const value = useMemo(
    () => ({ status, user, login, register, logout }),
    [status, user, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
