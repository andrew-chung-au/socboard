import { Link, Navigate, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type ReactNode } from "react";
import { LogOut, Moon, ShieldAlert, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSession } from "@/lib/auth";
import { config, session } from "@/services";

function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const stored = localStorage.getItem("socboard.theme");
    const d = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
  }, []);
  const toggle = () => {
    const d = !dark;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
    localStorage.setItem("socboard.theme", d ? "dark" : "light");
  };
  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle dark mode" className="h-8 w-8">
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

export { ThemeToggle };

export function AppShell({ children }: { children: ReactNode }) {
  const { hydrated, session: s } = useSession();
  const navigate = useNavigate();
  if (!hydrated) return <div className="p-6 text-muted-foreground">Loading…</div>;
  if (!s) return <Navigate to="/login" />;
  const navCls = "rounded-sm px-2.5 py-1 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground";
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-card/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
          <Link to="/" className="flex items-center gap-2 font-mono text-sm font-semibold tracking-tight">
            <ShieldAlert className="h-4 w-4 text-primary" /> SOCBoard
          </Link>
          {config.useMock && (
            <span className="rounded-sm border border-warning/50 bg-warning-bg px-1.5 text-[10px] font-medium uppercase text-warning">
              mock
            </span>
          )}
          <nav className="flex gap-1">
            <Link to="/" className={navCls} activeOptions={{ exact: true }} activeProps={{ className: "bg-secondary !text-foreground" }}>
              Queue
            </Link>
            <Link to="/closed" className={navCls} activeProps={{ className: "bg-secondary !text-foreground" }}>
              Closed
            </Link>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <div className="text-right leading-tight">
              <div className="text-sm font-medium">{s.user.displayName}</div>
              <div className="font-mono text-[11px] text-muted-foreground">
                {s.user.username} · {s.user.role === "lead" ? "Lead" : "Analyst"}
              </div>
            </div>
            <ThemeToggle />
            <Button
              variant="ghost"
              size="sm"
              className="h-8"
              onClick={() => {
                session.set(null);
                navigate({ to: "/login" });
              }}
            >
              <LogOut className="h-4 w-4" /> <span className="hidden sm:inline">Log out</span>
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">{children}</main>
    </div>
  );
}
