import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type PoStatus, type PoSummary } from "../api";
import { StatusBadge } from "./StatusBadge";

const REFRESH_MS = 10_000;               // re-check every 10 s so new uploads appear by themselves
const FILTERS: (PoStatus | "ALL")[] = ["ALL", "NEEDS_REVIEW", "READY", "POSTED", "DUPLICATE", "REJECTED", "FAILED"];

function formatTime(seconds: number) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function fileName(key: string) {
  return key.split("/").pop() ?? key;
}

export function PoList({ onOpen, onSignOut, canUpload }:
  { onOpen: (poId: string) => void; onSignOut: () => void; canUpload: boolean }) {
  const [pos, setPos] = useState<PoSummary[]>([]);
  const [filter, setFilter] = useState<PoStatus | "ALL">("ALL");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setPos(await api.listPos());
      setError("");
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) onSignOut();
      else setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [onSignOut]);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);    // stop polling when the page closes
  }, [load]);

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";             // lets the same file be picked again
    if (!file) return;
    setMessage(`Uploading ${file.name}…`);
    try {
      await api.uploadPo(file);
      setMessage(`${file.name} uploaded. It will appear here in about 20 seconds.`);
      setTimeout(load, 20_000);
    } catch (err) {
      setMessage("");
      setError(`Upload failed: ${(err as Error).message}`);
    }
  }

  const counts = pos.reduce<Record<string, number>>((acc, p) => ({ ...acc, [p.status]: (acc[p.status] ?? 0) + 1 }), {});
  const shown = filter === "ALL" ? pos : pos.filter((p) => p.status === filter);

  return (
    <main className="page">
      <header className="topbar">
        <h1>Purchase orders</h1>
        <div className="actions">
          {canUpload && (<>
            <input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg" hidden onChange={upload} />
            <button onClick={() => fileInput.current?.click()}>Upload PO</button>
          </>)}
          <button className="secondary" onClick={load}>Refresh</button>
        </div>
      </header>

      {message && <p className="notice">{message}</p>}
      {error && <p className="error">{error}</p>}

      <nav className="filters">
        {FILTERS.map((f) => (
          <button key={f} className={f === filter ? "chip active" : "chip"} onClick={() => setFilter(f)}>
            {f === "ALL" ? "All" : f.replace("_", " ").toLowerCase()} ({f === "ALL" ? pos.length : counts[f] ?? 0})
          </button>
        ))}
      </nav>

      {loading ? <p className="muted">Loading…</p> : shown.length === 0 ? (
        <p className="muted">No purchase orders here yet. Upload one to get started.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Received</th><th>Customer</th><th>PO number</th><th>Status</th>
                  <th className="num">Score</th><th className="num">Issues</th><th>File</th></tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <tr key={p.po_id} onClick={() => onOpen(p.po_id)} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && onOpen(p.po_id)}>
                  <td>{formatTime(p.created_at)}</td>
                  <td>{p.customer_name ?? <span className="muted">unknown</span>}</td>
                  <td>{p.po_number ?? <span className="missing">missing</span>}</td>
                  <td><StatusBadge status={p.status} /></td>
                  <td className="num">{p.score == null ? "–" : `${Math.round(p.score * 100)}%`}</td>
                  <td className="num">{p.issue_count || ""}</td>
                  <td className="muted file">{fileName(p.s3_key)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
