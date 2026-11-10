import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { AppShell } from "@/components/soc/AppShell";
import { CaseTable } from "@/components/soc/CaseTable";
import { useSession } from "@/lib/auth";
import { useNow } from "@/lib/useNow";
import { api, config } from "@/services";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Queue — SOCBoard" },
      { name: "description", content: "Open security alert cases, ordered by severity then age." },
      { property: "og:title", content: "Queue — SOCBoard" },
      { property: "og:description", content: "Open security alert cases, ordered by severity then age." },
    ],
  }),
  component: () => (
    <AppShell>
      <QueuePage />
    </AppShell>
  ),
});

type Filter = "all" | "mine" | "unclaimed";

function QueuePage() {
  const { session } = useSession();
  const now = useNow();
  const [filter, setFilter] = useState<Filter>("all");
  const q = useQuery({
    queryKey: ["queue"],
    queryFn: () => api.getQueue(),
    refetchInterval: config.pollIntervalMs,
    refetchIntervalInBackground: true,
    retry: false,
  });
  const all = q.data?.cases ?? [];
  // Filtering only removes rows; it never reorders.
  const shown = all.filter((c) =>
    filter === "mine" ? c.owner?.id === session?.user.id : filter === "unclaimed" ? !c.owner : true,
  );
  const updatedAgo = q.dataUpdatedAt ? Math.max(0, Math.floor((now - q.dataUpdatedAt) / 1000)) : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">Open queue</h1>
        <span className="font-mono text-xs text-muted-foreground">{all.length} open</span>
        <div className="flex rounded-sm border bg-card p-0.5 text-xs" role="group" aria-label="Filter">
          {(["all", "mine", "unclaimed"] as Filter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={cn("rounded-sm px-2 py-1 capitalize", filter === f ? "bg-secondary font-medium" : "text-muted-foreground")}
            >
              {f}
            </button>
          ))}
        </div>
        <span className="ml-auto font-mono text-xs text-muted-foreground" aria-live="polite">
          {updatedAgo === null ? "loading…" : `updated ${updatedAgo}s ago`}
        </span>
      </div>

      {q.isError && q.data && (
        <div role="alert" className="flex items-center gap-2 rounded-sm border border-warning/50 bg-warning-bg px-3 py-1.5 text-xs text-warning">
          <AlertTriangle className="h-3.5 w-3.5" /> Refresh failed: {(q.error as Error).message} Showing last known data; retrying.
        </div>
      )}

      {q.isPending ? (
        <div className="rounded-md border bg-card p-8 text-center text-muted-foreground">Loading queue…</div>
      ) : q.isError && !q.data ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-6 text-center text-sm text-destructive">
          Could not load the queue: {(q.error as Error).message} Retrying automatically.
        </div>
      ) : shown.length === 0 ? (
        <div className="rounded-md border border-dashed bg-card p-8 text-center text-muted-foreground">
          {filter === "all" ? "No open cases. The queue is clear." : `No ${filter === "mine" ? "cases owned by you" : "unclaimed cases"}.`}
        </div>
      ) : (
        <CaseTable cases={shown} now={now} />
      )}
    </div>
  );
}
