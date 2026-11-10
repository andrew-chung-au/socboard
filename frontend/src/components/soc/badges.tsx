import type { AssetCriticality, ClosingReason, Severity, Status } from "@/services";
import { CLOSING_REASON_LABELS } from "@/services";
import { cn } from "@/lib/utils";

const SEV: Record<Severity, { cls: string; mark: string }> = {
  Critical: { cls: "bg-sev-critical-bg text-sev-critical border-sev-critical/40", mark: "▲▲" },
  High: { cls: "bg-sev-high-bg text-sev-high border-sev-high/40", mark: "▲" },
  Medium: { cls: "bg-sev-medium-bg text-sev-medium border-sev-medium/40", mark: "■" },
  Low: { cls: "bg-sev-low-bg text-sev-low border-sev-low/40", mark: "▽" },
};

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const s = SEV[severity];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide",
        s.cls,
        className,
      )}
    >
      <span aria-hidden className="text-[9px]">{s.mark}</span>
      {severity}
    </span>
  );
}

export function StatusBadge({ status }: { status: Status }) {
  const cls =
    status === "New"
      ? "border-primary/50 text-primary"
      : status === "Investigating"
        ? "border-warning/50 bg-warning-bg text-warning"
        : "border-border bg-muted text-muted-foreground";
  return (
    <span className={cn("inline-flex rounded-sm border px-1.5 py-0.5 text-[11px] font-medium", cls)}>
      {status}
    </span>
  );
}

export function Criticality({ value }: { value: AssetCriticality }) {
  return <span className="text-[11px] text-muted-foreground">crit: {value.toLowerCase()}</span>;
}

export function ReasonBadge({ reason }: { reason: ClosingReason | null }) {
  if (!reason) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex rounded-sm border bg-secondary px-1.5 py-0.5 text-[11px] font-medium">
      {CLOSING_REASON_LABELS[reason]}
    </span>
  );
}
