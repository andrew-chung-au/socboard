import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/soc/AppShell";
import { CaseTable } from "@/components/soc/CaseTable";
import { useSession } from "@/lib/auth";
import { useNow } from "@/lib/useNow";
import { api } from "@/services";

export const Route = createFileRoute("/closed")({
  head: () => ({
    meta: [
      { title: "Closed cases — SOCBoard" },
      { name: "description", content: "Closed alert cases with closing reasons and audit trails." },
      { property: "og:title", content: "Closed cases — SOCBoard" },
      { property: "og:description", content: "Closed alert cases with closing reasons and audit trails." },
    ],
  }),
  component: () => (
    <AppShell>
      <ClosedPage />
    </AppShell>
  ),
});

function ClosedPage() {
  const now = useNow(30_000);
  const { session } = useSession();
  const isLead = session?.user.role === "lead";
  // Lead-only: the backend answers an Analyst with 403, so the list is not requested.
  const q = useQuery({ queryKey: ["closed"], queryFn: () => api.getClosedCases(), enabled: isLead, retry: false });
  if (!isLead)
    return <div className="rounded-md border border-dashed bg-card p-8 text-center text-muted-foreground">Only a Lead can see closed cases.</div>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Closed cases</h1>
        {q.data && <span className="font-mono text-xs text-muted-foreground">{q.data.length} closed</span>}
      </div>
      {q.isPending ? (
        <div className="rounded-md border bg-card p-8 text-center text-muted-foreground">Loading…</div>
      ) : q.isError ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-6 text-center text-sm text-destructive">
          Could not load closed cases: {(q.error as Error).message}
        </div>
      ) : q.data.length === 0 ? (
        <div className="rounded-md border border-dashed bg-card p-8 text-center text-muted-foreground">No closed cases yet.</div>
      ) : (
        <CaseTable cases={q.data} now={now} closed />
      )}
    </div>
  );
}
