import { useState } from "react";
import { api, ApiError, DEMO_TOKEN, token } from "../api";

/** Asks for the token once, checks it against the API, then keeps it for this tab only. */
export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function signIn(tokenValue: string) {
    setBusy(true);
    setError("");
    token.set(tokenValue);
    try {
      await api.listPos();               // a real call proves the token works
      onSignedIn();
    } catch (err) {
      token.clear();
      setError(err instanceof ApiError && (err.status === 401 || err.status === 403)
        ? "That token was not accepted." : `Could not reach the API: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="signin">
      <form onSubmit={(e) => { e.preventDefault(); signIn(value); }} className="card">
        <h1>PO Intelligence</h1>
        <p className="muted">Paste your access token to continue. It is kept only in this browser tab.</p>
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)}
               placeholder="Access token" autoFocus required />
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={busy || !value.trim()}>{busy ? "Checking…" : "Sign in"}</button>
        {DEMO_TOKEN && (<>
          <div className="or"><span>or</span></div>
          <button type="button" className="secondary" disabled={busy} onClick={() => signIn(DEMO_TOKEN)}>
            Explore the read-only demo
          </button>
          <p className="muted small">See real AI-processed sample POs. Nothing can be uploaded or changed.</p>
        </>)}
        <p className="muted small disclaimer">Personal learning project for educational purposes only.
          Not a product, not for sale and not offered as a service. All data is synthetic.</p>
      </form>
    </main>
  );
}
