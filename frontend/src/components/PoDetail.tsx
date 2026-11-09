// Step F2: the CSR's workspace for one PO: the original document beside the extracted data,
// editable fields, the rule issues, and Save & re-check / Approve / Reject.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Issue, type LineItem, type PoData, type PoDetail as Detail } from "../api";
import { StatusBadge } from "./StatusBadge";

const FINAL = new Set(["POSTED", "DUPLICATE", "REJECTED"]);
const HEADER_FIELDS: { key: keyof PoData; label: string; type?: string; wide?: boolean }[] = [
  { key: "customer_name", label: "Customer" },
  { key: "po_number", label: "PO number" },
  { key: "po_date", label: "PO date", type: "date" },
  { key: "requested_ship_date", label: "Requested ship date", type: "date" },
  { key: "buyer_contact", label: "Buyer" },
  { key: "ship_to", label: "Ship to", wide: true },
  { key: "bill_to", label: "Bill to", wide: true },
];

function when(seconds: number) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** Worst issue for a field ("error" beats "warning"), used to colour the input. */
function severityFor(issues: Issue[], field: string) {
  const found = issues.filter((i) => i.field === field);
  return found.some((i) => i.severity === "error") ? "error" : found.length ? "warning" : "";
}

export function PoDetail({ poId, onBack, onSignOut, readOnly }:
  { poId: string; onBack: () => void; onSignOut: () => void; readOnly: boolean }) {
  const [item, setItem] = useState<Detail | null>(null);
  const [draft, setDraft] = useState<PoData | null>(null);   // the CSR's edits, not saved yet
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const show = useCallback((detail: Detail) => {
    setItem(detail);
    setDraft(structuredClone(detail.po ?? { line_items: [] }));
    setDirty(false);
  }, []);

  const handle = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) return onSignOut();
    setError((err as Error).message);
  }, [onSignOut]);

  useEffect(() => {
    api.getPo(poId).then(show).catch(handle);
  }, [poId, show, handle]);

  if (error && !item) return <main className="page"><BackLink onBack={onBack} /><p className="error">{error}</p></main>;
  if (!item || !draft) return <main className="page"><BackLink onBack={onBack} /><p className="muted">Loading…</p></main>;

  const locked = readOnly || FINAL.has(item.status) || busy !== "";
  const issues = item.issues ?? [];
  const errors = issues.filter((i) => i.severity === "error").length;

  // ---- editing: a person's correction counts as fully confident ----
  function setField(key: keyof PoData, value: string) {
    setDraft((d) => d && { ...d, [key]: value || null, field_confidence: { ...d.field_confidence, [key]: 1 } });
    setDirty(true);
  }
  function setLine(index: number, key: keyof LineItem, raw: string) {
    const numeric = key === "quantity" || key === "unit_price";
    const value = numeric ? (raw === "" ? null : Number(raw)) : raw || null;
    setDraft((d) => {
      if (!d) return d;
      const lines = d.line_items.map((line, i) => {
        if (i !== index) return line;
        const next: LineItem = { ...line, [key]: value, confidence: 1 };
        if (key === "part_number") delete next.catalog_status;   // let the rules re-check the new part
        return next;
      });
      return { ...d, line_items: lines };
    });
    setDirty(true);
  }
  function removeLine(index: number) {
    setDraft((d) => d && { ...d, line_items: d.line_items.filter((_, i) => i !== index) });
    setDirty(true);
  }
  function addLine() {
    setDraft((d) => {
      if (!d) return d;
      const next = Math.max(0, ...d.line_items.map((l) => l.line_number)) + 1;
      return { ...d, line_items: [...d.line_items, { line_number: next, part_number: "", quantity: null,
                                                      uom: "EA", unit_price: null, confidence: 1 }] };
    });
    setDirty(true);
  }

  // ---- actions ----
  async function run(label: string, action: () => Promise<Detail>, done: (d: Detail) => string) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const result = await action();
      show(result);
      setNotice(done(result));
    } catch (err) {
      if (err instanceof ApiError && err.issues) setItem({ ...item!, issues: err.issues });
      handle(err);
    } finally {
      setBusy("");
    }
  }
  const save = () => run("save", () => api.updatePo(poId, draft),
    (d) => d.status === "READY" ? "Saved. All checks pass: this PO can be approved." :
      `Saved. ${(d.issues ?? []).length} issue(s) remain.`);
  const approve = () => run("approve", () => api.approvePo(poId),
    (d) => d.status === "POSTED" ? `Posted to ERP as sales order ${d.erp_response?.order_number}.`
      : "This PO was already sent to ERP, so it was not posted again.");
  const reject = () => run("reject", () => api.rejectPo(poId, reason), () => "PO rejected.").then(() => {
    setRejecting(false);
    setReason("");
  });

  const isImage = /\.(png|jpe?g)$/i.test(item.s3_key);

  return (
    <main className="page wide">
      <BackLink onBack={onBack} />
      <header className="detail-head">
        <div>
          <h1>{draft.customer_name || "Unknown customer"} <span className="muted">· {draft.po_number || "no PO number"}</span></h1>
          <p className="muted small">Received {when(item.created_at)} · {item.s3_key.split("/").pop()}</p>
        </div>
        <div className="head-status">
          <StatusBadge status={item.status} />
          <span className="score">{item.score == null ? "–" : `${Math.round(item.score * 100)}%`}</span>
        </div>
      </header>

      {notice && <p className="notice">{notice}</p>}
      {error && <p className="error">{error}</p>}
      {item.status === "POSTED" && item.erp_response && (
        <p className="success">ERP sales order <strong>{item.erp_response.order_number}</strong> created
          with {item.erp_response.lines_created} line(s).</p>
      )}
      {item.status === "REJECTED" && <p className="error">Rejected: {item.reject_reason}</p>}
      {item.status === "FAILED" && <p className="error">Processing failed: {item.error}</p>}

      <div className="split">
        <section className="doc">
          <div className="doc-bar">
            <span className="muted small">Original document</span>
            <a href={item.document_url} target="_blank" rel="noreferrer">Open in new tab</a>
          </div>
          {isImage ? <img src={item.document_url} alt="Original purchase order" />
                   : <iframe src={item.document_url} title="Original purchase order" />}
        </section>

        <section className="data">
          <div className="panel">
            <h2>Issues {issues.length > 0 && <span className="muted">({errors} {errors === 1 ? "error" : "errors"}, {issues.length - errors} {issues.length - errors === 1 ? "warning" : "warnings"})</span>}</h2>
            {issues.length === 0 ? <p className="success-text">No issues. All checks pass.</p> : (
              <ul className="issues">
                {issues.map((i, n) => (
                  <li key={n} className={i.severity}><span className="tag">{i.field}</span>{i.message}</li>
                ))}
              </ul>
            )}
            {dirty && <p className="muted small">You have unsaved changes. Save & re-check to update the issues.</p>}
          </div>

          <div className="panel">
            <h2>Order details</h2>
            <div className="fields">
              {HEADER_FIELDS.map((f) => (
                <label key={f.key} className={f.wide ? "wide" : ""}>
                  <span>{f.label}</span>
                  <input type={f.type ?? "text"} disabled={locked} value={(draft[f.key] as string) ?? ""}
                         className={severityFor(issues, f.key)} onChange={(e) => setField(f.key, e.target.value)} />
                </label>
              ))}
            </div>
            {draft.notes && <p className="muted small">Notes on the PO: {draft.notes}</p>}
          </div>

          <div className="panel">
            <h2>Line items ({draft.line_items.length})</h2>
            <div className="table-wrap">
              <table className="lines">
                <thead><tr><th>#</th><th>Part</th><th>Description</th><th className="num">Qty</th>
                  <th>UOM</th><th className="num">Unit price</th><th></th></tr></thead>
                <tbody>
                  {draft.line_items.map((line, i) => {
                    const sev = severityFor(issues, `line ${line.line_number}`);
                    return (
                      <tr key={line.line_number} className={sev ? `row-${sev}` : ""}>
                        <td>{line.line_number}</td>
                        <td><input disabled={locked} value={line.part_number ?? ""} size={9}
                                   onChange={(e) => setLine(i, "part_number", e.target.value)} />
                          {line.catalog_status === "not_found" && <div className="missing small">not in catalog</div>}</td>
                        <td className="desc">{line.description}</td>
                        <td><input disabled={locked} type="number" min="0" className="num" value={line.quantity ?? ""}
                                   onChange={(e) => setLine(i, "quantity", e.target.value)} /></td>
                        <td><input disabled={locked} value={line.uom ?? ""} size={4}
                                   onChange={(e) => setLine(i, "uom", e.target.value)} /></td>
                        <td><input disabled={locked} type="number" min="0" step="0.01" className="num"
                                   value={line.unit_price ?? ""} onChange={(e) => setLine(i, "unit_price", e.target.value)} /></td>
                        <td>{!locked && <button className="link danger" title="Remove line" onClick={() => removeLine(i)}>✕</button>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!locked && <button className="link" onClick={addLine}>+ Add line</button>}
          </div>

          {!FINAL.has(item.status) && !readOnly && (
            <div className="panel actions-panel">
              <button onClick={save} disabled={locked || !dirty}>{busy === "save" ? "Checking…" : "Save & re-check"}</button>
              <button onClick={approve} disabled={locked || dirty || errors > 0}
                      title={dirty ? "Save your changes first" : errors > 0 ? "Fix the errors first" : ""}>
                {busy === "approve" ? "Posting…" : "Approve & send to ERP"}
              </button>
              <button className="secondary danger" onClick={() => setRejecting(true)} disabled={locked}>Reject</button>
              {(errors > 0 || dirty) && <p className="muted small">
                {dirty ? "Save your changes before approving." : "Approve unlocks when no errors remain. Warnings are allowed."}</p>}
              {rejecting && (
                <div className="reject">
                  <input placeholder="Reason, e.g. customer cancelled" value={reason} autoFocus
                         onChange={(e) => setReason(e.target.value)} />
                  <button className="danger-fill" disabled={!reason.trim() || locked} onClick={reject}>Confirm reject</button>
                  <button className="link" onClick={() => setRejecting(false)}>Cancel</button>
                </div>
              )}
            </div>
          )}

          <details className="panel">
            <summary>AI processing and history</summary>
            <dl className="facts">
              <dt>Text read by</dt><dd>{item.text_method === "ocr" ? "OCR (Textract)" : "PDF text layer"}</dd>
              <dt>AI model</dt><dd>{item.model_id ?? "–"}</dd>
              <dt>AI cost</dt><dd>{item.ai_cost_usd != null ? `$${item.ai_cost_usd.toFixed(4)}` : "–"}</dd>
              <dt>Settings version</dt><dd>{item.config_version ?? "default"}</dd>
            </dl>
            {item.agent_trace && item.agent_trace.length > 0 && (<>
              <h3>Agent steps</h3>
              <ol className="trace">
                {item.agent_trace.map((s, n) => (
                  <li key={n}>Turn {s.turn}: {s.tool === "lookup_part"
                    ? `checked ${s.input?.part_number} in catalog → ${s.output?.found ? "found" : "not found"}`
                    : "recorded the final PO"}</li>
                ))}
              </ol>
            </>)}
            <h3>History</h3>
            <ul className="trace">
              <li>{when(item.created_at)}: received and processed automatically</li>
              {(item.history ?? []).map((h, n) => <li key={n}>{when(h.at)}: {h.action} ({h.detail})</li>)}
            </ul>
          </details>
        </section>
      </div>
    </main>
  );
}

function BackLink({ onBack }: { onBack: () => void }) {
  return <button className="link back" onClick={onBack}>← All purchase orders</button>;
}
