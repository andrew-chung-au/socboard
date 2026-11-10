import { useNavigate } from "@tanstack/react-router";
import type { CaseSummary } from "@/services";
import { age } from "@/lib/time";
import { Criticality, ReasonBadge, SeverityBadge, StatusBadge } from "./badges";

/** Renders cases in exactly the order given. Never sorts. */
export function CaseTable({ cases, now, closed = false }: { cases: CaseSummary[]; now: number; closed?: boolean }) {
  const navigate = useNavigate();
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-b bg-muted/60 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
            <th className="px-3 py-2 font-medium">Severity</th>
            <th className="px-3 py-2 font-medium">Title</th>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Asset</th>
            <th className="px-3 py-2 font-medium">Age</th>
            <th className="px-3 py-2 font-medium">Owner</th>
            <th className="px-3 py-2 font-medium">{closed ? "Reason" : "Status"}</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr
              key={c.id}
              tabIndex={0}
              onClick={() => navigate({ to: "/cases/$id", params: { id: c.id } })}
              onKeyDown={(e) => e.key === "Enter" && navigate({ to: "/cases/$id", params: { id: c.id } })}
              className="cursor-pointer border-b last:border-0 hover:bg-accent/50 focus:bg-accent/60 focus:outline-none"
            >
              <td className="px-3 py-1.5"><SeverityBadge severity={c.severity} /></td>
              <td className="max-w-[360px] px-3 py-1.5">
                <div className="truncate font-medium" title={c.title}>{c.title}</div>
                <div className="font-mono text-[11px] text-muted-foreground">{c.id} · {c.source}</div>
              </td>
              <td className="px-3 py-1.5 font-mono text-xs">{c.alertType}</td>
              <td className="px-3 py-1.5">
                <div className="font-mono text-xs">{c.asset}</div>
                <Criticality value={c.assetCriticality} />
              </td>
              <td className="tabular px-3 py-1.5 font-mono text-xs">{age(c.alertTimestamp, now)}</td>
              <td className="px-3 py-1.5">
                {c.owner ? c.owner.displayName : <span className="italic text-muted-foreground">Unclaimed</span>}
              </td>
              <td className="px-3 py-1.5">{closed ? <ReasonBadge reason={c.closingReason} /> : <StatusBadge status={c.status} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
