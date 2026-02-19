"use client";

import { useState, useEffect } from "react";
import { Amplify } from "aws-amplify";
import {
  fetchAuthSession,
  signIn as amplifySignIn,
  signUp as amplifySignUp,
  confirmSignUp as amplifyConfirmSignUp,
  resendSignUpCode as amplifyResendCode,
  signOut as amplifySignOut,
} from "aws-amplify/auth";
import { BotIcon, EyeIcon, EyeOffIcon } from "./icons";
import { ChatInterface } from "./ChatInterface";

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: process.env.NEXT_PUBLIC_USER_POOL_ID || "",
      userPoolClientId: process.env.NEXT_PUBLIC_USER_POOL_CLIENT_ID || "",
    },
  },
});

type AuthView = "signIn" | "signUp" | "confirm";

const inputCls =
  "w-full bg-gray-50 border border-gray-200 rounded-xl px-4 py-2.5 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all";
const btnCls =
  "w-full bg-blue-600 hover:bg-blue-700 active:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-2.5 rounded-xl text-sm transition-all shadow-sm hover:shadow-md mt-1";

export function AuthGate() {
  const [view,            setView]            = useState<AuthView>("signIn");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [email,           setEmail]           = useState("");
  const [password,        setPassword]        = useState("");
  const [confirmPwd,      setConfirmPwd]      = useState("");
  const [code,            setCode]            = useState("");
  const [error,           setError]           = useState("");
  const [loading,         setLoading]         = useState(false);
  const [showPwd,         setShowPwd]         = useState(false);

  // Restore session on mount
  useEffect(() => {
    fetchAuthSession()
      .then((s) => { if (s.tokens?.idToken) setIsAuthenticated(true); })
      .catch(() => {});
  }, []);

  const handleSignOut = async () => {
    await amplifySignOut();
    setIsAuthenticated(false);
    setEmail(""); setPassword(""); setCode(""); setError("");
    setView("signIn");
  };

  if (isAuthenticated) return <ChatInterface onSignOut={handleSignOut} />;

  const switchView = (v: AuthView) => { setView(v); setError(""); };

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setLoading(true);
    try {
      const result = await amplifySignIn({ username: email, password });
      if (result.isSignedIn) setIsAuthenticated(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally { setLoading(false); }
  };

  const handleSignUp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirmPwd) { setError("Passwords do not match."); return; }
    setError(""); setLoading(true);
    try {
      await amplifySignUp({ username: email, password, options: { userAttributes: { email } } });
      setView("confirm");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-up failed.");
    } finally { setLoading(false); }
  };

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(""); setLoading(true);
    try {
      await amplifyConfirmSignUp({ username: email, confirmationCode: code });
      const result = await amplifySignIn({ username: email, password });
      if (result.isSignedIn) setIsAuthenticated(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Verification failed.");
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen bg-gray-100 flex items-start justify-center pt-12 px-4 pb-4">
      <div className="w-full max-w-sm">

        {/* Branding */}
        <div className="flex flex-col items-center mb-8 gap-3">
          <div className="bg-blue-600 p-3 rounded-2xl shadow-lg">
            <BotIcon />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold text-gray-900">Knowledge Vault</h1>
            <p className="text-sm text-gray-500 mt-0.5">AI-powered document assistant</p>
          </div>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden">

          {/* Tabs */}
          {view !== "confirm" && (
            <div className="flex border-b border-gray-100">
              {(["signIn", "signUp"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => switchView(v)}
                  className={`flex-1 py-3.5 text-sm font-semibold transition-colors ${
                    view === v
                      ? "text-blue-600 border-b-2 border-blue-600 bg-white"
                      : "text-gray-400 hover:text-gray-600 bg-gray-50/60"
                  }`}
                >
                  {v === "signIn" ? "Sign in" : "Create account"}
                </button>
              ))}
            </div>
          )}

          <div className="p-7">
            {/* Error banner */}
            {error && (
              <div className="mb-4 px-4 py-2.5 bg-red-50 border border-red-200 rounded-xl text-sm text-red-600">
                {error}
              </div>
            )}

            {/* ── Sign in ── */}
            {view === "signIn" && (
              <form onSubmit={handleSignIn} className="space-y-4" noValidate>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                    Email address
                  </label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      type={showPwd ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                      className={`${inputCls} pr-10`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPwd((v) => !v)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      {showPwd ? <EyeOffIcon /> : <EyeIcon />}
                    </button>
                  </div>
                </div>
                <button type="submit" disabled={loading} className={btnCls}>
                  {loading ? "Signing in…" : "Sign in"}
                </button>
              </form>
            )}

            {/* ── Sign up ── */}
            {view === "signUp" && (
              <form onSubmit={handleSignUp} className="space-y-4" noValidate>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                    Email address
                  </label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    required
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      type={showPwd ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                      className={`${inputCls} pr-10`}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPwd((v) => !v)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                    >
                      {showPwd ? <EyeOffIcon /> : <EyeIcon />}
                    </button>
                  </div>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1.5">
                    Confirm password
                  </label>
                  <input
                    type={showPwd ? "text" : "password"}
                    value={confirmPwd}
                    onChange={(e) => setConfirmPwd(e.target.value)}
                    placeholder="••••••••"
                    required
                    className={inputCls}
                  />
                </div>
                <button type="submit" disabled={loading} className={btnCls}>
                  {loading ? "Creating account…" : "Create account"}
                </button>
              </form>
            )}

            {/* ── Email confirmation ── */}
            {view === "confirm" && (
              <div>
                <div className="text-center mb-6">
                  <div className="w-12 h-12 bg-blue-50 rounded-full flex items-center justify-center mx-auto mb-3">
                    <svg className="w-5 h-5 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                      />
                    </svg>
                  </div>
                  <h2 className="text-base font-bold text-gray-900">Check your email</h2>
                  <p className="text-sm text-gray-500 mt-1">
                    We sent a code to{" "}
                    <span className="font-medium text-gray-700">{email}</span>
                  </p>
                </div>
                <form onSubmit={handleConfirm} className="space-y-4" noValidate>
                  <input
                    type="text"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="000000"
                    maxLength={6}
                    inputMode="numeric"
                    required
                    className={`${inputCls} text-center tracking-[0.4em] font-mono text-lg`}
                  />
                  <button type="submit" disabled={loading || code.length < 6} className={btnCls}>
                    {loading ? "Verifying…" : "Verify & sign in"}
                  </button>
                  <button
                    type="button"
                    onClick={() => amplifyResendCode({ username: email })}
                    className="w-full text-sm text-gray-400 hover:text-blue-600 transition-colors py-1.5"
                  >
                    Resend code
                  </button>
                </form>
              </div>
            )}
          </div>
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">Secured by Amazon Cognito</p>
      </div>
    </div>
  );
}
