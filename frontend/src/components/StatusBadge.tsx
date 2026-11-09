import type { PoStatus } from "../api";

const LABELS: Record<PoStatus, string> = {
  POSTED: "Posted to ERP",
  READY: "Ready",
  NEEDS_REVIEW: "Needs review",
  DUPLICATE: "Duplicate",
  REJECTED: "Rejected",
  FAILED: "Failed",
};

export function StatusBadge({ status }: { status: PoStatus }) {
  return <span className={`badge badge-${status.toLowerCase()}`}>{LABELS[status] ?? status}</span>;
}
