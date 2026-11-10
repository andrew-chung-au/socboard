import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/soc/AppShell";
import { useSession } from "@/lib/auth";
import { api, ApiError, session } from "@/services";
import { MOCK_CREDENTIALS } from "@/services/mockService";

export const Route = createFileRoute("/login")({
  head: () => ({
    meta: [
      { title: "Sign in — SOCBoard" },
      { name: "description", content: "Sign in to the SOCBoard alert triage board." },
      { property: "og:title", content: "Sign in — SOCBoard" },
      { property: "og:description", content: "Sign in to the SOCBoard alert triage board." },
    ],
  }),
  component: LoginPage,
});

function LoginPage() {
  const { hydrated, session: s } = useSession();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (hydrated && s) return <Navigate to="/" />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.login(username, password);
      session.set(res);
      navigate({ to: "/" });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="absolute right-3 top-3"><ThemeToggle /></div>
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2 font-mono text-lg font-semibold">
          <ShieldAlert className="h-5 w-5 text-primary" /> SOCBoard
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-md border bg-card p-5">
          <h1 className="text-base font-semibold">Sign in</h1>
          <div className="space-y-1.5">
            <Label htmlFor="u">Username</Label>
            <Input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="p">Password</Label>
            <Input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </div>
          {error && (
            <p role="alert" className="rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        {import.meta.env.VITE_USE_MOCK !== "false" && (
          <div className="mt-4 rounded-md border border-dashed p-3 text-xs">
            <div className="mb-1.5 font-medium text-muted-foreground">Mock mode — test accounts</div>
            <ul className="space-y-1 font-mono">
              {MOCK_CREDENTIALS.map((c) => (
                <li key={c.username}>
                  <button
                    type="button"
                    className="hover:text-primary"
                    onClick={() => {
                      setUsername(c.username);
                      setPassword(c.password);
                    }}
                  >
                    {c.username} / {c.password}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
