// Sign-in screen, CSV import panel and the account menu in the top bar.
const { useState: useStateAcc, useEffect: useEffectAcc, useRef: useRefAcc } = React;

const PROVIDER_LABELS = {
  google: 'Continue with Google',
  microsoft: 'Continue with Microsoft',
  apple: 'Continue with Apple',
  facebook: 'Continue with Facebook',
};

function SignIn() {
  const [info, setInfo] = useStateAcc(null);
  const error = new URLSearchParams(location.search).get('signin_error');
  useEffectAcc(() => { window.VaultApi.providers().then(setInfo).catch(() => setInfo({ providers: [] })); }, []);

  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh', padding: 16 }}>
      <div className="panel" style={{ width: 'min(380px, 100%)', textAlign: 'center' }}>
        <div style={{ fontFamily: 'var(--display)', fontSize: 36, color: 'var(--gold)' }}>◇</div>
        <h1 className="h1" style={{ margin: '8px 0 4px' }}>The Vault</h1>
        <p className="label-mono" style={{ marginBottom: 20 }}>Sign in to open your collection</p>
        {error && <p style={{ color: 'var(--danger)', marginBottom: 12 }}>Sign-in failed ({error}). Please try again.</p>}
        {!info && <div className="spinner" style={{ width: 18, height: 18, margin: '0 auto' }}></div>}
        {info && (
          <div style={{ display: 'grid', gap: 10 }}>
            {info.providers.map((p) => (
              <a key={p} className="btn" href={`/api/auth/login/${p}`}>{PROVIDER_LABELS[p] || p}</a>
            ))}
            {info.dev_login && (
              <button className="btn ghost" onClick={() => window.VaultApi.devLogin().then(() => location.reload())}>
                Local dev sign-in
              </button>
            )}
            {!info.providers.length && !info.dev_login && (
              <p className="label-mono">No sign-in providers are configured on this server.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ImportButton({ onImported, className, label = 'Import CSV' }) {
  const input = useRefAcc(null);
  const [busy, setBusy] = useStateAcc(false);
  async function onFile(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setBusy(true);
    try {
      const res = await window.VaultApi.importCsv(file);
      onImported && onImported(res);
    } catch (err) {
      alert('Import failed: ' + err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <input ref={input} type="file" accept=".csv,text/csv" style={{ display: 'none' }} onChange={onFile} />
      <button className={className || 'btn sm'} disabled={busy} onClick={() => input.current.click()}>
        {busy ? 'Importing…' : label}
      </button>
    </>
  );
}

function describeChanges(c) {
  if (!c) return '';
  const parts = [];
  if (c.added) parts.push(`${c.added} new`);
  if (c.increased) parts.push(`${c.increased} more`);
  if (c.decreased) parts.push(`${c.decreased} fewer`);
  if (c.removed) parts.push(`${c.removed} gone`);
  return parts.length ? `${parts.join(', ')} (+${c.copies_in} / −${c.copies_out} cards)` : 'no changes';
}

function EmptyVault({ onImported }) {
  return (
    <div style={{ display: 'grid', placeItems: 'center', minHeight: '70vh' }}>
      <div className="panel" style={{ width: 'min(460px, 100%)', textAlign: 'center' }}>
        <p className="eyebrow">Your vault is empty</p>
        <h1 className="h1" style={{ margin: '8px 0 12px' }}>Import your collection</h1>
        <p className="label-mono" style={{ marginBottom: 20 }}>
          Export a CSV from the Dragon Shield app (Inventory → Export) and upload it here.
          Re-import any time: the Vault records what changed.
        </p>
        <ImportButton className="btn primary" label="Choose CSV file" onImported={onImported} />
      </div>
    </div>
  );
}

function AccountMenu({ me, onImported }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
      <ImportButton onImported={onImported} />
      <span className="label-mono" title={me && me.email ? me.email : ''}>{me ? (me.name || me.email || 'Signed in') : ''}</span>
      <button className="btn sm ghost" onClick={() => window.VaultApi.logout().then(() => location.reload())}>Sign out</button>
    </div>
  );
}

Object.assign(window, { SignIn, ImportButton, EmptyVault, AccountMenu, describeChanges });
