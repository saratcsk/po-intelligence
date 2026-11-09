// Step F3: the admin console. Every save is validated by the API, stored as a new version
// with a reason, and can be rolled back. The API refuses all of this to a CSR token.
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Config, type ConfigVersion, type Importance, type Severity } from "../api";

const PERCENT_SETTINGS: { key: string; label: string; help: string; max?: number }[] = [
  { key: "auto_approve_threshold", label: "Auto-approve threshold", help: "A PO with no errors posts automatically only at this score or above." },
  { key: "error_penalty", label: "Penalty per error", help: "How much each error lowers the score.", max: 0.5 },
  { key: "warning_penalty", label: "Penalty per warning", help: "How much each warning lowers the score.", max: 0.5 },
  { key: "low_model_confidence", label: "Low AI confidence", help: "Below this, the AI's own confidence counts as unsure." },
  { key: "price_warn_pct", label: "Price difference: warning", help: "Further than this from list price is a warning." },
  { key: "price_block_pct", label: "Price difference: error", help: "Further than this from list price is always an error." },
];
const FIELD_LABELS: Record<string, string> = {
  po_number: "PO number", customer_name: "Customer", po_date: "PO date", requested_ship_date: "Requested ship date",
  ship_to: "Ship-to address", bill_to: "Bill-to address", buyer_contact: "Buyer contact",
};
const RULE_LABELS: Record<string, string> = {
  ship_date_before_po_date: "Ship date is before the PO date",
  unknown_part: "Part is not in the catalog",
  invalid_quantity: "Quantity is zero or missing",
  uom_mismatch: "Unit of measure differs from the catalog",
  price_check: "Price is 20–50% from list (above the error limit it is always an error)",
  low_ai_confidence: "The AI was unsure about a value",
};
const SWITCHES: { key: "ocr_enabled" | "catalog_lookup_enabled" | "auto_post_enabled"; label: string; help: string }[] = [
  { key: "ocr_enabled", label: "Read scanned POs with OCR", help: "Off: scans go straight to review, with no OCR or AI cost." },
  { key: "catalog_lookup_enabled", label: "Agent checks parts in the catalog", help: "Off: one AI call, no tool use. Cheaper, but parts are not checked by the agent." },
  { key: "auto_post_enabled", label: "Post READY POs to ERP automatically", help: "Off: every PO waits for a person to approve it." },
];

function when(seconds: number) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function Choice<T extends string>({ value, options, onChange, disabled }:
  { value: T; options: readonly T[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button key={o} type="button" role="radio" aria-checked={o === value} disabled={disabled}
                className={o === value ? `on ${o}` : ""} onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  );
}

export function Settings({ onSignOut }: { onSignOut: () => void }) {
  const [saved, setSaved] = useState<Config | null>(null);   // what is live now
  const [draft, setDraft] = useState<Config | null>(null);   // what the admin is editing
  const [history, setHistory] = useState<ConfigVersion[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [confirmVersion, setConfirmVersion] = useState<number | null>(null);

  const handle = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) return onSignOut();
    setProblems(err instanceof ApiError && err.problems ? err.problems : [(err as Error).message]);
  }, [onSignOut]);

  const load = useCallback(async () => {
    try {
      const [cfg, hist] = await Promise.all([api.getConfig(), api.configHistory()]);
      setSaved(cfg);
      setDraft(structuredClone(cfg));
      setHistory(hist.items);
    } catch (err) {
      handle(err);
    }
  }, [handle]);

  useEffect(() => { load(); }, [load]);

  if (!saved || !draft) {
    return <main className="page">{problems.length ? <p className="error">{problems[0]}</p> : <p className="muted">Loading…</p>}</main>;
  }

  const changed = JSON.stringify(saved) !== JSON.stringify(draft);
  const edit = (fn: (c: Config) => void) => setDraft((d) => {
    if (!d) return d;
    const next = structuredClone(d);
    fn(next);
    return next;
  });

  async function save() {
    setBusy(true); setNotice(""); setProblems([]);
    try {
      const { version: _version, ...config } = draft!;       // the server decides the version number
      const result = await api.saveConfig(config, note.trim());
      setNotice(`Saved as version ${result.version}. New POs, edits and approvals use it from now on.`);
      setNote("");
      await load();
    } catch (err) {
      handle(err);
    } finally {
      setBusy(false);
    }
  }

  async function rollback(version: number) {
    setBusy(true); setNotice(""); setProblems([]);
    try {
      const result = await api.rollbackConfig(version);
      setNotice(`Version ${version}'s settings restored as new version ${result.version}.`);
      setConfirmVersion(null);
      await load();
    } catch (err) {
      handle(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page settings">
      <header className="topbar">
        <div>
          <h1>Settings</h1>
          <p className="muted small">Live version: <strong>{saved.version === 0 ? "defaults (never changed)" : `v${saved.version}`}</strong></p>
        </div>
      </header>

      {notice && <p className="notice">{notice}</p>}
      {problems.length > 0 && <div className="error-box">{problems.map((p) => <p key={p}>{p}</p>)}</div>}

      <section className="panel">
        <h2>Accuracy</h2>
        <div className="sliders">
          {PERCENT_SETTINGS.map((s) => {
            const value = draft.accuracy[s.key];
            const max = s.max ?? 1;
            return (
              <label key={s.key} className="slider">
                <span className="slider-head"><span>{s.label}</span><strong>{Math.round(value * 100)}%</strong></span>
                <input type="range" min={0} max={max} step={0.01} value={value} disabled={busy}
                       onChange={(e) => edit((c) => { c.accuracy[s.key] = Number(e.target.value); })} />
                <span className="muted small">{s.help}</span>
              </label>
            );
          })}
        </div>
      </section>

      <section className="panel">
        <h2>Field importance</h2>
        <p className="muted small">Required: missing is an error. Important: missing is a warning. Optional: not checked.</p>
        <div className="rows">
          {Object.keys(draft.fields).map((f) => (
            <div key={f} className="row">
              <span>{FIELD_LABELS[f] ?? f}</span>
              <Choice<Importance> value={draft.fields[f]} options={["required", "important", "optional"]} disabled={busy}
                                  onChange={(v) => edit((c) => { c.fields[f] = v; })} />
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Rules</h2>
        <p className="muted small">"No line items" and "missing part number" are always errors and cannot be switched off.</p>
        <div className="rows">
          {Object.keys(draft.rules).map((r) => (
            <div key={r} className="row">
              <span>{RULE_LABELS[r] ?? r}</span>
              <Choice<Severity> value={draft.rules[r]} options={["off", "warning", "error"]} disabled={busy}
                                onChange={(v) => edit((c) => { c.rules[r] = v; })} />
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Orchestration</h2>
        <div className="rows">
          {SWITCHES.map((s) => (
            <label key={s.key} className="row switch-row">
              <span><span>{s.label}</span><br /><span className="muted small">{s.help}</span></span>
              <input type="checkbox" className="switch" checked={draft.orchestration[s.key]} disabled={busy}
                     onChange={(e) => edit((c) => { c.orchestration[s.key] = e.target.checked; })} />
            </label>
          ))}
          <label className="row">
            <span><span>Agent turn limit</span><br /><span className="muted small">Safety stop for the AI agent's loop (1–10).</span></span>
            <input type="number" min={1} max={10} className="turns" value={draft.orchestration.max_agent_turns} disabled={busy}
                   onChange={(e) => edit((c) => { c.orchestration.max_agent_turns = Number(e.target.value); })} />
          </label>
          {([["daily_upload_limit", "Daily upload limit", "Upload links handed out per day (UTC). Protects the AWS bill."],
             ["daily_processing_limit", "Daily AI processing limit", "POs sent to OCR/AI per day, however they arrived (1–200)."]] as const)
            .map(([key, label, help]) => (
              <label key={key} className="row">
                <span><span>{label}</span><br /><span className="muted small">{help}</span></span>
                <input type="number" min={1} max={200} className="turns" value={draft.orchestration[key]} disabled={busy}
                       onChange={(e) => edit((c) => { c.orchestration[key] = Number(e.target.value); })} />
              </label>
            ))}
          <div className="row">
            <span><span>AI model</span><br /><span className="muted small">Locked to the models the cloud permissions allow.</span></span>
            <code className="small">{draft.orchestration.model_id}</code>
          </div>
        </div>
      </section>

      <section className={changed ? "panel save-bar sticky" : "panel save-bar"}>
        <input placeholder="Why are you making this change? (required, kept in the history)" value={note}
               maxLength={200} disabled={busy || !changed} onChange={(e) => setNote(e.target.value)} />
        <button onClick={save} disabled={busy || !changed || !note.trim()}>{busy ? "Saving…" : "Save as new version"}</button>
        <button className="secondary" onClick={() => { setDraft(structuredClone(saved)); setProblems([]); }}
                disabled={busy || !changed}>Discard changes</button>
        {!changed && <p className="muted small">Change a setting above to enable saving.</p>}
      </section>

      <section className="panel">
        <h2>Change history</h2>
        {history.length === 0 ? <p className="muted">No changes yet. The defaults are in use.</p> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Version</th><th>When</th><th>By</th><th>Why</th><th></th></tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.version} className="static">
                    <td>v{h.version}{h.version === saved.version && <span className="badge badge-posted live">live</span>}</td>
                    <td>{when(h.changed_at)}</td>
                    <td>{h.changed_by}</td>
                    <td className="why">{h.note || <span className="muted">–</span>}</td>
                    <td>
                      {h.version !== saved.version && (confirmVersion === h.version ? (
                        <span className="confirm">
                          <button onClick={() => rollback(h.version)} disabled={busy}>Restore v{h.version}</button>
                          <button className="link" onClick={() => setConfirmVersion(null)}>Cancel</button>
                        </span>
                      ) : <button className="secondary" onClick={() => setConfirmVersion(h.version)} disabled={busy}>Roll back</button>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">Rolling back never deletes history: the old settings are saved again as a new version.</p>
      </section>
    </main>
  );
}
