import { createFileRoute, Link } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/soc/AppShell";
import { Criticality, ReasonBadge, SeverityBadge, StatusBadge } from "@/components/soc/badges";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useSession } from "@/lib/auth";
import { fmt } from "@/lib/time";
import { api, ApiError, CLOSING_REASON_LABELS, config, type CaseDetail, type ClosingReason } from "@/services";

export const Route = createFileRoute("/cases/$id")({
  head: ({ params }) => ({
    meta: [
      { title: `Case ${params.id} — SOCBoard` },
      { name: "description", content: "Alert case detail, notes and audit trail." },
      { property: "og:title", content: `Case ${params.id} — SOCBoard` },
      { property: "og:description", content: "Alert case detail, notes and audit trail." },
    ],
  }),
  component: () => (
    <AppShell>
      <CasePage />
    </AppShell>
  ),
});

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words">{children}</dd>
    </div>
  );
}

function Panel({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="rounded-md border bg-card">
      <header className="flex items-center justify-between border-b px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
        {right}
      </header>
      <div className="p-3">{children}</div>
    </section>
  );
}

function CasePage() {
  const { id } = Route.useParams();
  const { hydrated, session } = useSession();
  const me = session?.user;
  const qc = useQueryClient();
  const caseQ = useQuery({ queryKey: ["case", id], queryFn: () => api.getCase(id), retry: false });
  const isLead = me?.role === "lead";
  // The audit trail is Lead-only (the backend answers an Analyst with 403), so it is not requested.
  const auditQ = useQuery({ queryKey: ["audit", id], queryFn: () => api.getAudit(id), enabled: isLead, retry: false });
  const usersQ = useQuery({ queryKey: ["users"], queryFn: () => api.listUsers(), enabled: isLead, retry: false });

  const [actionError, setActionError] = useState<string | null>(null);
  const [closeOpen, setCloseOpen] = useState(false);
  const [reason, setReason] = useState<ClosingReason | "">("");
  const [noteText, setNoteText] = useState("");
  const [reassignTo, setReassignTo] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["case", id] });
    qc.invalidateQueries({ queryKey: ["audit", id] });
    qc.invalidateQueries({ queryKey: ["queue"] });
    qc.invalidateQueries({ queryKey: ["closed"] });
  };

  const run = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onMutate: () => setActionError(null),
    onSuccess: () => refresh(),
    onError: (err) => {
      const msg = err instanceof ApiError ? err.message : "The action failed.";
      setActionError(msg);
      toast.error(msg);
      refresh();
    },
  });

  if (!hydrated || caseQ.isPending) return <div className="rounded-md border bg-card p-8 text-center text-muted-foreground">Loading case…</div>;
  if (caseQ.isError)
    return (
      <div className="space-y-3">
        <BackLink />
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-6 text-sm text-destructive">
          {(caseQ.error as Error).message}
        </div>
      </div>
    );

  const c: CaseDetail = caseQ.data;
  const isOwner = !!c.owner && c.owner.id === me?.id;
  const closed = c.status === "Closed";
  const canClaim = !c.owner && !closed;
  const canChangeStatus = (isOwner || isLead) && !closed;
  const canReassign = config.featureReassign && isLead && !!c.owner && !closed;
  const busy = run.isPending;

  return (
    <div className="space-y-3">
      <BackLink closed={closed && isLead} />

      <div className="flex flex-wrap items-start gap-3">
        <SeverityBadge severity={c.severity} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <h1 className="whitespace-pre-wrap break-words text-lg font-semibold">{c.title}</h1>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-xs text-muted-foreground">
            <span>{c.id}</span>·<StatusBadge status={c.status} />·
            <span>owner: {c.owner ? c.owner.displayName : "Unclaimed"}</span>
            {closed && <>· <ReasonBadge reason={c.closingReason} /></>}
          </div>
        </div>
      </div>

      {actionError && (
        <div role="alert" className="flex items-start justify-between gap-3 rounded-sm border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <span><strong>Server refused the action:</strong> {actionError}</span>
          <button className="text-xs underline" onClick={() => setActionError(null)}>Dismiss</button>
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
        <div className="space-y-3">
          <Panel title="Alert">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3">
              <Field label="Source">{c.source}</Field>
              <Field label="Type"><span className="font-mono text-xs">{c.alertType}</span></Field>
              <Field label="Asset">
                <span className="font-mono text-xs">{c.asset}</span> <Criticality value={c.assetCriticality} />
              </Field>
              <Field label="Alert time"><span className="font-mono text-xs">{fmt(c.alertTimestamp)}</span></Field>
              <Field label="Ingested"><span className="font-mono text-xs">{fmt(c.ingestedAt)}</span></Field>
              <Field label="Severity rule"><span className="font-mono text-xs">{c.severityRule}</span></Field>
            </dl>
            <div className="mt-3">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Description</div>
              <p className="mt-1 whitespace-pre-wrap break-words rounded-sm border bg-muted/50 p-2 font-mono text-xs">{c.description}</p>
            </div>
          </Panel>

          <Panel title={`Notes (${c.notes.length})`}>
            {c.notes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No notes yet.</p>
            ) : (
              <ol className="space-y-2">
                {c.notes.map((n) => (
                  <li key={n.id} className="rounded-sm border p-2">
                    <div className="mb-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <span className="font-medium text-foreground">{n.author.displayName}</span>
                      <span className="font-mono">{fmt(n.createdAt)}</span>
                    </div>
                    <p className="whitespace-pre-wrap break-words text-sm">{n.text}</p>
                  </li>
                ))}
              </ol>
            )}
            {!closed && (
              <form
                className="mt-3 space-y-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  const text = noteText;
                  if (!text.trim()) return;
                  run.mutate(() => api.addNote(c.id, text).then(() => setNoteText("")));
                }}
              >
                <Label htmlFor="note" className="sr-only">Add note</Label>
                <Textarea id="note" rows={3} placeholder="Add a note…" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
                <Button type="submit" size="sm" disabled={busy || !noteText.trim()}>Add note</Button>
              </form>
            )}
          </Panel>

          {isLead && (
          <Panel title="Audit trail">
            {auditQ.isPending ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : auditQ.isError ? (
              <p role="alert" className="text-sm text-destructive">{(auditQ.error as Error).message}</p>
            ) : auditQ.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No entries.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-xs">
                  <thead>
                    <tr className="border-b text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                      <th className="py-1.5 pr-3 font-medium">When</th>
                      <th className="py-1.5 pr-3 font-medium">Who</th>
                      <th className="py-1.5 pr-3 font-medium">Action</th>
                      <th className="py-1.5 font-medium">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditQ.data.map((a) => (
                      <tr key={a.id} className="border-b last:border-0 align-top">
                        <td className="whitespace-nowrap py-1.5 pr-3 font-mono">{fmt(a.at)}</td>
                        <td className="py-1.5 pr-3">{a.actor === "system" ? <span className="italic text-muted-foreground">system</span> : a.actor.displayName}</td>
                        <td className="py-1.5 pr-3 font-mono">{a.action}</td>
                        <td className="whitespace-pre-wrap break-words py-1.5">{a.detail}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>
          )}
        </div>

        <aside>
          <Panel title="Actions">
            {closed ? (
              <p className="text-sm text-muted-foreground">This case is closed and read-only.</p>
            ) : (
              <div className="space-y-2">
                {canClaim && (
                  <Button className="w-full" size="sm" disabled={busy} onClick={() => run.mutate(() => api.claimCase(c.id))}>
                    Claim case
                  </Button>
                )}
                {canChangeStatus && c.status === "New" && (
                  <Button className="w-full" size="sm" variant="secondary" disabled={busy}
                    onClick={() => run.mutate(() => api.transitionCase(c.id, "Investigating"))}>
                    Start investigating
                  </Button>
                )}
                {canChangeStatus && c.status === "Investigating" && (
                  <Button className="w-full" size="sm" variant="secondary" disabled={busy}
                    onClick={() => { setReason(""); setCloseOpen(true); }}>
                    Close case…
                  </Button>
                )}
                {!canChangeStatus && (
                  <p className="text-xs text-muted-foreground">Only the owner or a Lead can change status.</p>
                )}
                {canReassign && (
                  <div className="space-y-1.5 border-t pt-2">
                    <Label className="text-xs">Reassign to</Label>
                    <div className="flex gap-2">
                      <Select value={reassignTo} onValueChange={setReassignTo}>
                        <SelectTrigger className="h-8"><SelectValue placeholder="Choose user" /></SelectTrigger>
                        <SelectContent>
                          {(usersQ.data ?? []).filter((u) => u.id !== c.owner?.id).map((u) => (
                            <SelectItem key={u.id} value={u.id}>{u.displayName} ({u.role})</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button size="sm" variant="outline" disabled={busy || !reassignTo}
                        onClick={() => run.mutate(() => api.reassignCase(c.id, reassignTo).then(() => setReassignTo("")))}>
                        Reassign
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </Panel>
        </aside>
      </div>

      <Dialog open={closeOpen} onOpenChange={setCloseOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Close case {c.id}</DialogTitle>
            <DialogDescription>A closing reason is required.</DialogDescription>
          </DialogHeader>
          <RadioGroup value={reason} onValueChange={(v) => setReason(v as ClosingReason)} className="gap-2">
            {(Object.keys(CLOSING_REASON_LABELS) as ClosingReason[]).map((r) => (
              <div key={r} className="flex items-center gap-2">
                <RadioGroupItem id={`r-${r}`} value={r} />
                <Label htmlFor={`r-${r}`}>{CLOSING_REASON_LABELS[r]}</Label>
              </div>
            ))}
          </RadioGroup>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCloseOpen(false)}>Cancel</Button>
            <Button disabled={!reason || busy}
              onClick={() => {
                if (!reason) return;
                setCloseOpen(false);
                run.mutate(() => api.transitionCase(c.id, "Closed", reason));
              }}>
              Close case
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function BackLink({ closed }: { closed?: boolean }) {
  return (
    <Link to={closed ? "/closed" : "/"} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
      <ArrowLeft className="h-3.5 w-3.5" /> {closed ? "Closed cases" : "Queue"}
    </Link>
  );
}
