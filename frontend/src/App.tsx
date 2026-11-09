import { useCallback, useEffect, useState } from "react";
import { api, token, type Role } from "./api";
import { PoDetail } from "./components/PoDetail";
import { PoList } from "./components/PoList";
import { Settings } from "./components/Settings";
import { SignIn } from "./components/SignIn";

/** The screen comes from the address bar (#/po/<id>, #/settings), so Back and refresh work. */
function useHashRoute() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = useCallback((to: string) => { window.location.hash = to; }, []);
  return [hash.replace(/^#/, "") || "/", go] as const;
}

export default function App() {
  const [signedIn, setSignedIn] = useState(Boolean(token.get()));
  const [role, setRole] = useState<Role | null>(null);
  const [route, go] = useHashRoute();

  const signOut = useCallback(() => {
    token.clear();
    setRole(null);
    setSignedIn(false);
  }, []);

  useEffect(() => {
    if (signedIn) api.getRole().then(setRole).catch(signOut);
  }, [signedIn, signOut]);

  if (!signedIn) return <SignIn onSignedIn={() => setSignedIn(true)} />;

  const poMatch = route.match(/^\/po\/(.+)$/);
  let screen;
  if (poMatch) {
    screen = <PoDetail poId={decodeURIComponent(poMatch[1])} onBack={() => go("/")} onSignOut={signOut}
                       readOnly={role === "viewer"} />;
  } else if (route === "/settings" && role === "admin") {
    screen = <Settings onSignOut={signOut} />;
  } else {
    screen = <PoList onOpen={(id) => go(`/po/${encodeURIComponent(id)}`)} onSignOut={signOut}
                     canUpload={role === "csr" || role === "admin"} />;
  }

  return (
    <>
      <nav className="appbar">
        <strong>PO Intelligence</strong>
        <a href="#/" className={!route.startsWith("/settings") ? "active" : ""}>Purchase orders</a>
        {role === "admin" && <a href="#/settings" className={route === "/settings" ? "active" : ""}>Settings</a>}
        <span className="spacer" />
        {role && <span className="role">{role === "admin" ? "Admin" : role === "csr" ? "CSR" : "Demo · read-only"}</span>}
        <button className="link" onClick={signOut}>Sign out</button>
      </nav>
      {role === "viewer" && (
        <p className="demo-banner">You are viewing a read-only demo with synthetic purchase orders. Uploading,
          editing and approving are switched off for this account.</p>
      )}
      {screen}
    </>
  );
}
