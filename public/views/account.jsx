// Sign-in screen, CSV import panel and the account menu in the top bar.
const { useState: useStateAcc, useEffect: useEffectAcc, useRef: useRefAcc } = React;

const PROVIDERS = {
  google: { name: 'Google', logo: (
    <svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
  ) },
  microsoft: { name: 'Microsoft', logo: (
    <svg viewBox="0 0 21 21" aria-hidden="true"><path fill="#F25022" d="M1 1h9v9H1z"/><path fill="#7FBA00" d="M11 1h9v9h-9z"/><path fill="#00A4EF" d="M1 11h9v9H1z"/><path fill="#FFB900" d="M11 11h9v9h-9z"/></svg>
  ) },
  apple: { name: 'Apple', logo: (
    <svg viewBox="0 0 814 1000" aria-hidden="true"><path fill="currentColor" d="M788 341c-6 4-108 62-108 190 0 148 130 200 134 202-1 3-21 72-69 142-43 62-88 124-156 124s-86-40-164-40c-77 0-104 41-167 41s-106-58-156-128C44 790 0 671 0 557 0 375 118 279 235 279c62 0 114 41 153 41 37 0 95-43 166-43 27 0 124 2 188 64zM554 171c29-35 50-83 50-131 0-7-1-13-2-19-47 2-104 32-138 72-27 30-52 79-52 128 0 7 1 15 2 17 3 1 8 1 13 1 43 0 97-29 127-68z"/></svg>
  ) },
  facebook: { name: 'Facebook', logo: (
    <svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="24" fill="#1877F2"/><path fill="#fff" d="M33.3 30.9 34.4 24h-6.6v-4.5c0-1.9.9-3.7 3.9-3.7h3v-5.9s-2.7-.5-5.4-.5c-5.4 0-8.9 3.3-8.9 9.2V24h-6v6.9h6V48c1.2.2 2.4.3 3.7.3s2.5-.1 3.7-.3V30.9h5.5z"/></svg>
  ) },
};
const PROVIDER_LABELS = Object.fromEntries(Object.entries(PROVIDERS).map(([k, p]) => [k, `Continue with ${p.name}`]));
const LAST_SIGNIN_KEY = 'vault_last_signin';
const SESSION_ENDED_KEY = 'vault_session_ended';
const SIGNED_OUT_KEY = 'vault_signed_out';

// What went wrong, in words: the server sends /?signin_error=CODE&provider=NAME.
function signInProblem(code, provider) {
  const who = (PROVIDERS[provider] && PROVIDERS[provider].name) || 'the provider';
  switch (code) {
    case 'access_denied': return `You cancelled signing in with ${who}. Nothing was shared with the Vault.`;
    case 'temporarily_unavailable': return `${who} isn't answering right now. Try again in a minute, or use another way to sign in.`;
    case 'session_ended':
      return 'Your session was ended while you were signing in, so nothing was linked. Sign in again.';
    case 'mismatching_state':
      return 'That sign-in expired or was started in another tab. Please start again from here.';
    case 'consent_required': case 'interaction_required': return `${who} needs you to confirm access. Please try again.`;
    default: return `Signing in with ${who} didn't work (${code}). Please try again.`;
  }
}

// Read the result of a sign-in attempt once, then clean the URL so a reload doesn't repeat it.
function takeSignInResult() {
  const params = new URLSearchParams(location.search);
  const code = params.get('signin_error');
  const provider = params.get('provider');
  let ended = false, signedOut = false;
  try {
    ended = sessionStorage.getItem(SESSION_ENDED_KEY) === '1'; sessionStorage.removeItem(SESSION_ENDED_KEY);
    signedOut = sessionStorage.getItem(SIGNED_OUT_KEY) === '1'; sessionStorage.removeItem(SIGNED_OUT_KEY);
  } catch {}
  if (code) {
    params.delete('signin_error'); params.delete('provider');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : '') + location.hash);
  }
  if (code) return { tone: 'danger', text: signInProblem(code, provider) };
  if (ended) return { tone: 'info', text: 'Your session ended. Sign in again to carry on where you were.' };
  if (signedOut) return { tone: 'info', text: "You're signed out. Your collection stays safe in the Vault." };
  return null;
}

function signOut({ everywhere = false } = {}) {
  try { sessionStorage.setItem(SIGNED_OUT_KEY, '1'); } catch {}
  window.VaultApi.logout(everywhere).finally(() => { location.hash = ''; location.reload(); });
}

function rememberSignIn(method) { try { localStorage.setItem(LAST_SIGNIN_KEY, method); } catch {} }

function SignIn() {
  const [info, setInfo] = useStateAcc(null);
  const [message, setMessage] = useStateAcc(takeSignInResult);
  const [creating, setCreating] = useStateAcc(false);
  const [newName, setNewName] = useStateAcc('');
  const [pending, setPending] = useStateAcc(null); // 'passkey' | 'signup' | provider name
  const last = (() => { try { return localStorage.getItem(LAST_SIGNIN_KEY); } catch { return null; } })();
  useEffectAcc(() => {
    window.VaultApi.providers().then(setInfo).catch(() => setInfo({ providers: [], failed: true }));
    // Coming Back from a provider's page restores this page from the cache: don't stay "busy".
    const onShow = (e) => { if (e.persisted) setPending(null); };
    window.addEventListener('pageshow', onShow);
    return () => window.removeEventListener('pageshow', onShow);
  }, []);
  const pk = window.VaultApi.passkeys;
  const withPasskeys = !!(info && info.passkeys && pk.supported());
  const busy = pending !== null;
  const run = async (kind, fn) => {
    setMessage(null); setPending(kind);
    try { await fn(); rememberSignIn('passkey'); location.href = '/'; }
    catch (e) { setMessage({ tone: 'danger', text: pk.explain(e) }); setPending(null); }
  };
  const go = (p) => { setMessage(null); setPending(p); rememberSignIn(p); location.href = `/api/auth/login/${p}`; };
  const providers = info ? [...info.providers].sort((a, b) => (b === last) - (a === last)) : [];
  const lastBadge = (m) => m === last && <span className="signin-last">Last used</span>;

  return (
    <main className="signin">
      <div className="signin-card panel">
        <section className="signin-pitch" aria-label="About the Vault">
          <div className="signin-mark" aria-hidden="true">◇</div>
          <h1 className="h1">The Vault</h1>
          <p className="signin-lede">Your Magic: The Gathering collection, priced every day.</p>
          <ul className="signin-points">
            <li>Import from Dragon Shield or Moxfield, and see what changed since last time.</li>
            <li>Market value by card and by set, with Scryfall prices updated daily.</li>
            <li>Check any decklist against what you own, and share with friends.</li>
          </ul>
        </section>

        <section className="signin-form" aria-labelledby="signin-title">
          <h2 className="h2" id="signin-title">Sign in</h2>
          <p className="signin-sub">New here? Any option below creates your vault the first time.</p>
          {message && <p role={message.tone === 'danger' ? 'alert' : 'status'} className={`signin-msg ${message.tone}`}>{message.text}</p>}
          {!info && <div className="signin-skeleton" aria-label="Loading sign-in options"><span /><span /><span /></div>}
          {info && (
            <div className="signin-options">
              {providers.map((p) => (
                <button key={p} type="button" className={`provider-btn provider-${p}`} disabled={busy} onClick={() => go(p)}>
                  <span className="provider-logo">{PROVIDERS[p] ? PROVIDERS[p].logo : null}</span>
                  <span className="provider-label">{pending === p ? `Opening ${PROVIDERS[p] ? PROVIDERS[p].name : p}…` : (PROVIDER_LABELS[p] || p)}</span>
                  {pending === p ? <span className="spinner spinner-xs" /> : lastBadge(p)}
                </button>
              ))}
              {withPasskeys && (
                <>
                  {providers.length > 0 && <div className="signin-or"><span>or use a passkey</span></div>}
                  <button type="button" className="provider-btn provider-passkey" disabled={busy} onClick={() => run('passkey', pk.signIn)}>
                    <span className="provider-logo" aria-hidden="true">⚿</span>
                    <span className="provider-label">{pending === 'passkey' ? 'Waiting for your device…' : 'Sign in with a passkey'}</span>
                    {pending === 'passkey' ? <span className="spinner spinner-xs" /> : lastBadge('passkey')}
                  </button>
                  {!creating ? (
                    <button type="button" className="signin-link" disabled={busy} onClick={() => setCreating(true)}>
                      No account yet? Create one with a passkey
                    </button>
                  ) : (
                    <form className="signin-create" onSubmit={(e) => { e.preventDefault(); run('signup', () => pk.signUp(newName)); }}>
                      <p>Your device makes a passkey: Face ID, Touch ID, Windows Hello or your phone. No password and no e-mail needed.</p>
                      <label className="signin-field">
                        <span>Your name (optional)</span>
                        <input className="input" autoComplete="name" value={newName} onChange={(e) => setNewName(e.target.value)} />
                      </label>
                      <button className="btn primary" disabled={busy}>{pending === 'signup' ? 'Waiting for your device…' : 'Create my vault'}</button>
                      <button type="button" className="signin-link" disabled={busy} onClick={() => setCreating(false)}>Cancel</button>
                    </form>
                  )}
                </>
              )}
              {info.dev_login && (
                <button type="button" className="signin-link" onClick={() => window.VaultApi.devLogin().then(() => location.reload())}>
                  Local dev sign-in
                </button>
              )}
              {!providers.length && !info.dev_login && !withPasskeys && (
                <p className="signin-msg danger" role="alert">
                  {info.failed ? "The Vault can't be reached right now. Check your connection and reload."
                    : info.passkeys ? "This browser can't use passkeys, and no other sign-in is set up on this server. Try a recent Safari, Chrome, Edge or Firefox."
                    : 'No sign-in is set up on this server yet.'}
                </p>
              )}
            </div>
          )}
          {localStorage.getItem('vault_pending_invite') && (
            <p className="signin-msg info" role="status">Sign in (or create your vault) to accept the invite you opened.</p>
          )}
          <p className="signin-fine">
            We only receive your name and e-mail from the provider you choose. Your collection stays private unless you share it.{' '}
            <a href="/privacy.html">Privacy notice</a> · <a href="/terms.html">Terms</a> · <a href="/support.html">Support</a> · <a href="/credits.html">Credits &amp; thanks</a>
          </p>
        </section>
      </div>
      <div className="signin-footer"><VaultFooter /></div>
    </main>
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
          Export a CSV from the{' '}
          <a href="https://mtg.dragonshield.com" target="_blank" rel="noopener noreferrer">Dragon Shield Card Manager</a>{' '}
          (Inventory → Export) or{' '}
          <a href="https://moxfield.com/collection" target="_blank" rel="noopener noreferrer">Moxfield</a>{' '}
          (Collection → More → Export CSV) and upload it here. The format is detected for you. Re-import any
          time: the Vault records what changed. Only the file you choose is read. The Vault never connects
          to your accounts there.
        </p>
        <ImportButton className="btn primary" label="Choose CSV file" onImported={onImported} />
      </div>
    </div>
  );
}

function AccountMenu({ me, onImported, onAccount, readOnly }) {
  return (
    <div className="acct-menu" style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
      {!readOnly && <ImportButton onImported={onImported} />}
      <a className="btn sm ghost acct-help" href="#/help">Help</a>
      <button className="btn sm ghost acct-name" title={me && me.email ? me.email : ''} onClick={onAccount}>
        {me ? (me.name || me.email || 'Account') : 'Account'}
      </button>
      <button className="btn sm ghost acct-signout" onClick={() => signOut()}>Sign out</button>
    </div>
  );
}

Object.assign(window, { SignIn, signOut, SESSION_ENDED_KEY, ImportButton, EmptyVault, AccountMenu, describeChanges });

// ---- Account panel: profile, sharing, shared with me, saved decks, GDPR export/delete ----

// Keyboard handling shared by the dialogs (this panel and the card panel): focus moves into the dialog when it
// opens, Tab stays inside it, and focus returns to whatever opened it when it closes.
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
function useDialogFocus(ref) {
  useEffectAcc(() => {
    const opener = document.activeElement;
    const box = ref.current;
    if (box) (box.querySelector('[data-autofocus]') || box).focus();
    const onKey = (e) => {
      if (e.key !== 'Tab' || !box) return;
      const items = Array.from(box.querySelectorAll(FOCUSABLE)).filter(el => el.offsetParent !== null);
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); if (opener && opener.focus && document.contains(opener)) opener.focus(); };
  }, []);
}

// The × of every close button: drawn lines that never take a tap (pointer-events: none), so a tap
// anywhere on the round button, the × included, lands on the button itself.
function CloseIcon() {
  return (
    <svg className="close-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <path d="M3 3 L13 13 M13 3 L3 13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" fill="none" />
    </svg>
  );
}

function Modal({ title, onClose, children }) {
  const box = useRefAcc(null);
  useDialogFocus(box);
  useEffectAcc(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgb(0 0 0 / 0.55)', zIndex: 50,
      display: 'grid', placeItems: 'start center', overflowY: 'auto', padding: '48px 16px' }}>
      <div className="panel" role="dialog" aria-modal="true" aria-label={title} ref={box} tabIndex={-1}
        onClick={(e) => e.stopPropagation()} style={{ width: 'min(720px, 100%)', outline: 'none' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 className="h1" style={{ fontSize: 28, margin: 0 }}>{title}</h2>
          <button className="btn xs ghost close-x" data-autofocus onClick={onClose} aria-label={`Close ${title}`} title="Close (Esc)"><CloseIcon /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16, marginTop: 16 }}>
      <p className="eyebrow" style={{ marginBottom: 10 }}>{title}</p>
      {children}
    </div>
  );
}

const rowStyle = { display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between', padding: '6px 0' };

function AccountPanel({ me, onClose, onOpenShared, onOpenDeck, onMeChanged, onCollectionChanged }) {
  const [name, setName] = useStateAcc(me?.name || '');
  const [shares, setShares] = useStateAcc([]);
  const [incoming, setIncoming] = useStateAcc([]);
  const [decks, setDecks] = useStateAcc([]);
  const [invite, setInvite] = useStateAcc(null);
  const [showCosts, setShowCosts] = useStateAcc(false);
  const [deleting, setDeleting] = useStateAcc(false);
  const [confirmText, setConfirmText] = useStateAcc('');
  const [downloaded, setDownloaded] = useStateAcc(false);
  const [error, setError] = useStateAcc(null);
  const api = window.VaultApi;

  const [loadFailed, setLoadFailed] = useStateAcc(false);
  const reload = () => {
    setLoadFailed(false);
    const failed = () => setLoadFailed(true);  // offline or server unreachable: say so, keep what's shown
    api.shares().then(setShares, failed);
    api.sharedWithMe().then(setIncoming, failed);
    api.decks().then(setDecks, failed);
  };
  useEffectAcc(reload, []);

  const wrap = (fn) => async (...args) => {
    setError(null);
    try { await fn(...args); } catch (e) { setError(e.message); }
  };
  const share = wrap(async (kind, deckId) => {
    const res = await api.createShare(kind, deckId || null, showCosts);
    setInvite({ ...res, kind, deckId });
    reload();
  });
  const saveName = wrap(async () => { await api.updateName(name); onMeChanged && onMeChanged(); });
  const revoke = wrap(async (id) => { await api.removeShare(id); reload(); });
  const removeDeck = wrap(async (id) => {
    if (confirm('Delete this deck? Anyone you shared it with loses access.')) { await api.deleteDeck(id); reload(); }
  });
  const deleteAccount = wrap(async () => {
    await api.deleteAccount();
    await api.clearLocalData();
    alert('Your account and all of its data have been deleted.');
    location.href = '/';
  });

  return (
    <Modal title="Account" onClose={onClose}>
      {error && <p style={{ color: 'var(--danger)', marginBottom: 8 }}>{error}</p>}
      {(loadFailed || !me) && (
        <p role="status" className="label-mono account-offline" style={{ color: 'var(--danger)', marginBottom: 8 }}>
          {navigator.onLine === false ? "You're offline. " : "The Vault can't be reached right now. "}
          Your profile, shares and decks can't be loaded; try again when you're back online.
        </p>
      )}

      <Section title="Profile">
        <div style={rowStyle}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name"
            style={{ flex: 1 }} />
          <button className="btn sm" onClick={saveName}>Save</button>
        </div>
        {me && (
          <p className="label-mono">
            {me.email || 'No e-mail'} · signed in with {(me.providers || []).join(', ') || '—'}
          </p>
        )}
      </Section>

      <Section title="Share your collection">
        <p className="label-mono" style={{ marginBottom: 8 }}>
          Your collection and decks are private. An invite link gives one person read-only access until you revoke it.
        </p>
        <label className="label-mono" style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <input type="checkbox" checked={showCosts} onChange={(e) => setShowCosts(e.target.checked)} />
          Include what I paid for cards
        </label>
        <button className="btn sm" onClick={() => share('collection')}>Create invite link for my collection</button>
        {invite && (
          <div className="panel panel-tight" style={{ marginTop: 10 }}>
            <p className="label-mono">Send this link to one person. It works once and expires {new Date(invite.expires_at).toLocaleDateString()}.</p>
            <div style={rowStyle}>
              <input className="input" readOnly value={invite.url} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
              <button className="btn xs" onClick={() => navigator.clipboard && navigator.clipboard.writeText(invite.url)}>Copy</button>
            </div>
          </div>
        )}
        {shares.length > 0 && (
          <div style={{ marginTop: 10 }}>
            {shares.map((s) => (
              <div key={s.id} style={rowStyle}>
                <span className="label-mono">
                  {s.kind === 'deck' ? `Deck “${s.deck_name}”` : 'Collection'} →{' '}
                  {s.status === 'active' ? s.with : 'invite not accepted yet'}
                  {s.show_costs ? ' · with prices paid' : ''}
                </span>
                <button className="btn xs ghost" onClick={() => revoke(s.id)}>{s.status === 'active' ? 'Revoke' : 'Cancel'}</button>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Saved decks">
        {decks.length === 0 && <p className="label-mono">No saved decks. Load one on the Decks tab and press “Save deck”.</p>}
        {decks.map((d) => (
          <div key={d.id} style={rowStyle}>
            <span>{d.name}</span>
            <span style={{ display: 'flex', gap: 6 }}>
              <button className="btn xs" onClick={() => onOpenDeck(d)}>Open</button>
              <button className="btn xs" onClick={() => share('deck', d.id)}>Share</button>
              <button className="btn xs ghost" onClick={() => removeDeck(d.id)}>Delete</button>
            </span>
          </div>
        ))}
      </Section>

      <SignInMethods me={me} onChanged={onMeChanged} />

      <MoveSection />

      <CollectionHistorySection onCollectionChanged={onCollectionChanged} />

      <ResetSection onCollectionChanged={onCollectionChanged} />

      <AgentsSection />

      <ConnectedAppsSection />

      <Section title="Shared with me">
        {incoming.length === 0 && <p className="label-mono">Nothing yet. When someone sends you an invite link, open it while signed in.</p>}
        {incoming.map((s) => (
          <div key={s.id} style={rowStyle}>
            <span>{s.kind === 'deck' ? `${s.from}'s deck “${s.deck_name}”` : `${s.from}'s collection`}</span>
            <span style={{ display: 'flex', gap: 6 }}>
              <button className="btn xs" onClick={() => onOpenShared(s)}>Open</button>
              <button className="btn xs ghost" onClick={() => revoke(s.id)}>Leave</button>
            </span>
          </div>
        ))}
      </Section>

      <Section title="Your data">
        <p className="label-mono" style={{ marginBottom: 8 }}>
          Download everything the Vault holds about you: profile, collection (CSV and JSON), import history,
          value history, decks and shares. <a href="/privacy.html" target="_blank" rel="noopener">Privacy notice</a>
        </p>
        <a className="btn sm" href={api.exportUrl} onClick={() => setDownloaded(true)}>Download my data (.zip)</a>
        {!deleting ? (
          <button className="btn sm ghost" style={{ marginLeft: 8, color: 'var(--danger)' }} onClick={() => setDeleting(true)}>
            Delete my account…
          </button>
        ) : (
          <div className="panel panel-tight" style={{ marginTop: 12, borderColor: 'var(--danger)' }}>
            <p style={{ marginBottom: 8 }}><strong>Delete your account and all of its data?</strong></p>
            <p className="label-mono" style={{ marginBottom: 8 }}>
              This removes your collection, import history, value history, decks and sign-in methods, and ends every
              share you gave or received. It can't be undone.
            </p>
            <p className="label-mono" style={{ marginBottom: 8 }}>
              Step 1 — keep a copy (recommended):{' '}
              <a className="btn xs" href={api.exportUrl} onClick={() => setDownloaded(true)}>Download my data</a>
              {downloaded && ' ✓'}
            </p>
            <p className="label-mono" style={{ marginBottom: 6 }}>Step 2 — type DELETE to confirm:</p>
            <div style={rowStyle}>
              <input className="input" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} style={{ flex: 1 }} />
              <button className="btn sm" disabled={confirmText !== 'DELETE'} style={{ color: 'var(--danger)' }} onClick={deleteAccount}>
                Delete permanently
              </button>
              <button className="btn sm ghost" onClick={() => { setDeleting(false); setConfirmText(''); }}>Cancel</button>
            </div>
          </div>
        )}
      </Section>
    </Modal>
  );
}

// ---- Credits footer: shown on every screen, including sign-in ----

function VaultFooter() {
  const style = { color: 'var(--gold)', textDecoration: 'underline', textUnderlineOffset: 2 };
  const link = (href, label) => <a href={href} target="_blank" rel="noopener noreferrer" style={style}>{label}</a>;
  return (
    <footer className="vault-footer" style={{ margin: '48px max(24px, env(safe-area-inset-right)) 24px max(24px, env(safe-area-inset-left))', paddingTop: 16, borderTop: '1px solid var(--border)',
      fontFamily: 'var(--mono)', fontSize: 11, lineHeight: 1.7, color: 'var(--muted)' }}>
      <p>
        Card data, images &amp; prices from {link('https://scryfall.com', 'Scryfall')} (prices sourced by Scryfall from{' '}
        {link('https://www.tcgplayer.com', 'TCGplayer')} and {link('https://www.cardmarket.com', 'Cardmarket')})
        {' · '}Decks from {link('https://archidekt.com', 'Archidekt')}
        {' · '}Collections imported from {link('https://mtg.dragonshield.com', 'Dragon Shield')} or {link('https://moxfield.com', 'Moxfield')}
        {' · '}Card art by the credited artists
        {' · '}<a href="/credits.html" style={style}><strong>Credits &amp; thanks</strong></a>
        {' · '}<a href="/privacy.html" style={style}>Privacy</a>
        {' · '}<a href="/terms.html" style={style}>Terms</a>
        {' · '}<a href="/support.html" style={style}>Support</a>
      </p>
      <p>
        The Vault is unofficial Fan Content permitted under the{' '}
        {link('https://company.wizards.com/en/legal/fancontentpolicy', 'Fan Content Policy')}. Not approved/endorsed by
        Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.
        Not affiliated with or endorsed by Scryfall, Archidekt, Dragon Shield or Moxfield.
      </p>
    </footer>
  );
}

Object.assign(window, { AccountPanel, VaultFooter, useDialogFocus, CloseIcon });


// Personal access tokens: let people connect their own AI agents and scripts (MCP or HTTP API).
function AgentsSection() {
  const api = window.VaultApi;
  const [tokens, setTokens] = useStateAcc([]);
  const [name, setName] = useStateAcc('My agent');
  const [write, setWrite] = useStateAcc(false);
  const [created, setCreated] = useStateAcc(null);
  const [error, setError] = useStateAcc(null);
  const reload = () => { api.tokens().then(setTokens).catch((e) => setError(e.message)); };
  useEffectAcc(reload, []);
  const create = async () => {
    setError(null);
    try {
      setCreated(await api.createToken(name, write ? ['read', 'write'] : ['read']));
      reload();
    } catch (e) { setError(e.message); }
  };
  const remove = async (id) => { await api.deleteToken(id); reload(); };
  const copy = (text) => navigator.clipboard && navigator.clipboard.writeText(text);
  const claudeCmd = created && `claude mcp add --transport http vault ${created.mcp_url} --header "Authorization: Bearer ${created.token}"`;
  return (
    <Section title="Agents & API">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        In Claude, ChatGPT, Perplexity or Codex you don't need a token: add The Vault there and sign in (<a href="/connect.html">how</a>).
        Tokens are for Claude Code, editors and scripts. A token acts as you. Read-only unless you allow changes;
        revoke it any time.{' '}
        <a href="/llms.txt" target="_blank" rel="noopener">How agents use it</a> ·{' '}
        <a href="/api/docs" target="_blank" rel="noopener">API docs</a>
      </p>
      {error && <p style={{ color: 'var(--danger)' }}>{error}</p>}
      <div style={rowStyle}>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Token name" style={{ flex: 1 }} />
        <label className="label-mono" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={write} onChange={(e) => setWrite(e.target.checked)} /> allow changes
        </label>
        <button className="btn sm" onClick={create}>Create token</button>
      </div>
      {created && (
        <div className="panel panel-tight" style={{ marginTop: 10 }}>
          <p className="label-mono">Copy it now: it won't be shown again. Expires {new Date(created.expires_at).toLocaleDateString()}.</p>
          <div style={rowStyle}>
            <input className="input" readOnly value={created.token} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
            <button className="btn xs" onClick={() => copy(created.token)}>Copy</button>
          </div>
          <p className="label-mono" style={{ marginTop: 8 }}>MCP server: {created.mcp_url} · Claude Code:</p>
          <div style={rowStyle}>
            <input className="input" readOnly value={claudeCmd} style={{ flex: 1 }} onFocus={(e) => e.target.select()} />
            <button className="btn xs" onClick={() => copy(claudeCmd)}>Copy</button>
          </div>
        </div>
      )}
      {tokens.map((t) => (
        <div key={t.id} style={rowStyle}>
          <span className="label-mono">
            {t.name} · {t.prefix}… · {t.scopes.includes('write') ? 'read & write' : 'read-only'} ·{' '}
            {t.last_used_at ? 'used ' + new Date(t.last_used_at).toLocaleDateString() : 'never used'}
          </span>
          <button className="btn xs ghost" onClick={() => remove(t.id)}>Revoke</button>
        </div>
      ))}
    </Section>
  );
}


// What changed your collection, newest first: every import and every change an assistant made. The latest assistant
// change can be undone here (docs/owned-cards-updates.md, rule 5): Undo shows what it will put back, and only a second
// press, "Undo this change", applies exactly that.
function CollectionHistorySection({ onCollectionChanged }) {
  const api = window.VaultApi;
  const [items, setItems] = useStateAcc(null);
  const [error, setError] = useStateAcc(null);
  const [asking, setAsking] = useStateAcc(null);   // the undo preview being confirmed: { id, preview }
  const [busy, setBusy] = useStateAcc(false);
  const [note, setNote] = useStateAcc(null);
  const reload = () => api.recentImports().then(setItems).catch((e) => setError(e.message));
  useEffectAcc(() => { reload(); }, []);
  const kindText = (i) => i.kind === 'assistant' ? `Change by ${i.app || 'an assistant'}` : i.kind === 'undo' ? 'Undo of an assistant change'
    : i.kind === 'reset' ? `Reset: ${(i.reset && i.reset.scope) || 'the collection'}` : i.kind === 'reset_undo' ? 'Undo of a reset' : `Import: ${i.filename}`;
  const lineText = (l) => `${l.card}${l.set ? ` (${String(l.set).toUpperCase()} ${l.number})` : ''}: ${l.before} → ${l.after}`;
  const ask = async () => {
    setError(null); setNote(null); setBusy(true);
    try { const preview = await api.undoPreview(); setAsking({ id: preview.undoes, preview }); }
    catch (e) { setError(e.message); reload(); }
    setBusy(false);
  };
  const confirmUndo = async () => {
    setError(null); setBusy(true);
    try {
      await api.undoApply(asking.preview.confirmation);
      setAsking(null);
      setNote('Undone: the copies are back as they were before that change.');
      onCollectionChanged && onCollectionChanged();
    } catch (e) { setError(e.message); setAsking(null); }
    setBusy(false);
    reload();
  };
  return (
    <Section title="Collection history">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Every import and every change an assistant made to which cards you own. The latest assistant change can be undone
        until your collection changes again.
      </p>
      {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
      {note && <p role="status" className="label-mono" style={{ color: 'var(--good)' }}>{note}</p>}
      {items && items.length === 0 && <p className="label-mono">Nothing yet. Import a collection to start.</p>}
      {(items || []).slice(0, 8).map((i) => (
        <div key={i.id} style={{ padding: '6px 0', borderTop: '1px solid var(--border)' }} data-history-id={i.id}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <span className="label-mono">
              <strong>{kindText(i)}</strong> · {new Date(i.created_at).toLocaleString()} · {window.describeChanges(i.changes)}
            </span>
            {i.undoable && !asking && <button className="btn xs" disabled={busy} onClick={ask}>Undo</button>}
          </div>
          {i.kind === 'assistant' && (i.lines || []).length > 0 && (
            <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', lineHeight: 1.5, margin: '4px 0 0' }}>
              {i.lines.slice(0, 4).map(lineText).join(' · ')}{i.lines.length > 4 ? ` · +${i.lines.length - 4} more` : ''}
            </p>
          )}
          {i.kind === 'assistant' && !i.undoable && (
            <p className="muted" style={{ fontSize: 11, margin: '4px 0 0' }}>
              {i.undone ? 'Undone.' : "Can't be undone any more: your collection changed after it."}
            </p>
          )}
          {asking && asking.id === i.id && (
            <div className="panel panel-tight" style={{ marginTop: 8 }} role="group" aria-label="Confirm the undo">
              <p className="label-mono" style={{ marginBottom: 6 }}>
                Undoing this puts back:
              </p>
              {asking.preview.lines.map((l, n) => (
                <p key={n} style={{ fontSize: 13, margin: '2px 0' }}>
                  {l.card}{l.printing && l.printing.set ? ` (${String(l.printing.set).toUpperCase()} ${l.printing.number})` : ''}: {l.copies_before} → {l.copies_after} copies
                </p>
              ))}
              <p className="label-mono" style={{ margin: '6px 0' }}>
                {asking.preview.copies_added} copies added, {asking.preview.copies_removed} removed
                {asking.preview.value_change_usd ? `, value ${asking.preview.value_change_usd > 0 ? '+' : '−'}$${Math.abs(asking.preview.value_change_usd).toFixed(2)} (Scryfall prices)` : ''}.
              </p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn sm primary" disabled={busy} onClick={confirmUndo}>Undo this change</button>
                <button className="btn sm ghost" disabled={busy} onClick={() => setAsking(null)}>Keep it</button>
              </div>
            </div>
          )}
        </div>
      ))}
    </Section>
  );
}


// Reset collection (#129): the whole inventory or one bucket. Preview first (the server says what goes: the browser adds nothing up),
// the download of the export, a typed RESET, then the reset; for 7 days afterwards the section offers the undo.
const rsPlural = (n, one, many) => `${(n || 0).toLocaleString()} ${n === 1 ? one : many}`;
const rsMoney = (v) => '$' + (Math.round((v || 0) * 100) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const RS_WORD = 'RESET';

function ResetPreview({ p }) {
  const r = p.removes, v = r.added_in_the_vault_only, e = r.edited_in_the_vault;
  const where = p.scope.bucket ? `the bucket ${p.scope.bucket.name}` : 'the whole inventory';
  return (
    <div className="reset-preview" role="group" aria-label={`What resetting ${where} would remove`}>
      <p style={{ fontSize: 13, marginBottom: 8 }}>
        Resetting <strong>{where}</strong> removes <strong>{rsPlural(r.copies, 'copy', 'copies')}</strong> in {rsPlural(r.rows, 'row', 'rows')}
        {' '}({rsPlural(r.printings, 'printing', 'printings')}, {rsPlural(r.cards, 'card', 'cards')}), worth <strong>{rsMoney(r.market_value_usd)}</strong> at market prices.
      </p>
      <ul className="reset-facts">
        {v && v.known && v.cards > 0 && <li><strong>{rsPlural(v.cards, 'card', 'cards')} ({rsPlural(v.copies, 'copy', 'copies')})</strong> were added here only: they are in no file you imported, so only the export below or the undo gets them back.</li>}
        {v && !v.known && <li>{v.note}</li>}
        {e && e.cards > 0 && <li>{rsPlural(e.cards, 'card', 'cards')} you changed here since the last import go too.</li>}
        {r.unmatched_rows > 0 && <li>{rsPlural(r.unmatched_rows, 'row has', 'rows have')} no known printing.</li>}
        <li>{p.buckets}</li>
        <li>{p.tags.keep
          ? (p.tags.cards_left_not_owned > 0
            ? `${rsPlural(p.tags.cards_left_not_owned, 'tagged card keeps its tags', 'tagged cards keep their tags')}, shown as “not owned”` + (p.notes.cards > 0 ? `, and ${rsPlural(p.notes.cards, 'note stays', 'notes stay')}.` : '.')
            : 'No tags or notes are affected.')
          : `${rsPlural(p.tags.removed, 'tag', 'tags')} and ${rsPlural(p.notes.removed, 'note', 'notes')} are removed.`}</li>
        <li>{p.history.keep ? 'The import history stays, with an entry for the reset.' : `${rsPlural(p.history.removed, 'history entry', 'history entries')} are cleared.`}</li>
        {p.leaves && <li>Left as they are: {rsPlural(p.leaves.copies, 'copy', 'copies')} in {rsPlural(p.leaves.buckets, 'other bucket', 'other buckets')}.</li>}
      </ul>
      <p style={{ margin: '10px 0' }}>
        <a className="btn xs" href={p.backup.download} download>Download the export of this ({p.backup.format})</a>
      </p>
      <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
        {p.undo.available ? `You can undo this for ${p.undo.days} days, until anything else changes your collection.` : p.undo.note}
      </p>
      {p.refused && <p role="alert" style={{ color: 'var(--danger)', fontSize: 13 }}>{p.refused}</p>}
    </div>
  );
}

function ResetSection({ onCollectionChanged }) {
  const api = window.VaultApi;
  const [buckets, setBuckets] = useStateAcc([]);
  const [bucketId, setBucketId] = useStateAcc('');
  const [clearTags, setClearTags] = useStateAcc(false);
  const [clearHistory, setClearHistory] = useStateAcc(false);
  const [noUndo, setNoUndo] = useStateAcc(false);
  const [preview, setPreview] = useStateAcc(null);
  const [typed, setTyped] = useStateAcc('');
  const [busy, setBusy] = useStateAcc(false);
  const [error, setError] = useStateAcc(null);
  const [note, setNote] = useStateAcc(null);
  const [info, setInfo] = useStateAcc(null);
  const options = { bucket_id: bucketId ? Number(bucketId) : null, keep_tags: !clearTags, keep_history: !(clearHistory && !bucketId), no_undo: noUndo };
  const body = Object.fromEntries(Object.entries(options).filter(([, v]) => v !== null));
  const reload = () => {
    api.buckets().then(setBuckets, () => {});
    api.resetInfo().then(setInfo, () => setInfo(null));
  };
  useEffectAcc(reload, []);
  const changed = (set) => (e) => { set(e.target.type === 'checkbox' ? e.target.checked : e.target.value); setPreview(null); setTyped(''); setNote(null); setError(null); };
  const run = async (fn) => {
    setError(null); setBusy(true);
    try { await fn(); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const ask = () => run(async () => { setNote(null); setPreview(await api.resetPreview(body)); setTyped(''); });
  const doReset = () => run(async () => {
    const res = await api.resetApply(body, preview.confirmation, typed);
    setPreview(null); setTyped('');
    setNote(`Reset: ${rsPlural(res.removed.copies, 'copy', 'copies')} removed from ${res.scope.bucket ? res.scope.bucket.name : 'the whole inventory'}. ` +
            (res.undo.available ? `You can undo it until ${new Date(res.undo.until).toLocaleDateString()}.` : 'It cannot be undone.'));
    onCollectionChanged && onCollectionChanged();
    reload();
  });
  const doUndo = () => run(async () => {
    const res = await api.resetUndo();
    setNote(`Undone: ${rsPlural(res.restores.copies, 'copy is', 'copies are')} back as before the reset.`);
    onCollectionChanged && onCollectionChanged();
    reload();
  });
  return (
    <Section title="Reset collection">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Start over: empty the whole inventory or one bucket. You see what goes first and can download an export; your buckets, tags and notes
        stay unless you clear them. A reset can be undone for 7 days, until anything else changes your collection.
      </p>
      {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
      {note && <p role="status" className="label-mono" style={{ color: 'var(--good)', marginBottom: 8 }}>{note}</p>}
      {info && (
        <div className="panel panel-tight reset-undo" role="group" aria-label="Undo the last reset">
          <p style={{ fontSize: 13, marginBottom: 8 }}>
            Last reset: <strong>{info.scope}</strong>, {rsPlural(info.removed.copies, 'copy', 'copies')} ({rsMoney(info.removed.market_value_usd)}),
            {' '}{new Date(info.created_at).toLocaleString()}. It can be undone until {new Date(info.expires_at).toLocaleString()}.
          </p>
          {info.can_undo
            ? <button type="button" className="btn sm" disabled={busy} onClick={doUndo}>Undo the reset</button>
            : <p className="muted" style={{ fontSize: 12 }}>{info.why_not}</p>}
        </div>
      )}
      <div className="reset-options">
        <label className="label-mono" htmlFor="reset-scope">What to reset</label>
        <select id="reset-scope" className="select" value={bucketId} onChange={changed(setBucketId)}>
          <option value="">The whole inventory</option>
          {buckets.map((b) => <option key={b.id} value={b.id}>{b.name} ({rsPlural(b.copies, 'copy', 'copies')})</option>)}
        </select>
        <label className="reset-check"><input type="checkbox" checked={clearTags} onChange={changed(setClearTags)} />
          <span>Also clear my tags and notes{bucketId ? ' on the cards that leave the inventory' : ''}</span></label>
        <label className="reset-check"><input type="checkbox" checked={clearHistory && !bucketId} disabled={!!bucketId} onChange={changed(setClearHistory)} />
          <span>Also clear my import history{bucketId ? ' (whole inventory only)' : ''}</span></label>
        <label className="reset-check"><input type="checkbox" checked={noUndo} onChange={changed(setNoUndo)} />
          <span>Keep no undo copy (the reset cannot be undone)</span></label>
        <div><button type="button" className="btn sm" disabled={busy} onClick={ask}>{busy && !preview ? 'Reading…' : 'Preview what would be removed'}</button></div>
      </div>
      {preview && (
        <div className="panel reset-confirm" style={{ borderColor: 'var(--danger)' }}>
          <p className="eyebrow" style={{ marginBottom: 8 }}>Nothing is changed until you type {RS_WORD}</p>
          <ResetPreview p={preview} />
          {preview.ready && (
            <>
              <label className="label-mono" htmlFor="reset-typed" style={{ display: 'block', marginBottom: 6 }}>Type {RS_WORD} to confirm:</label>
              <div className="reset-typed">
                <input id="reset-typed" className="input" value={typed} autoComplete="off" autoCapitalize="characters" spellCheck="false"
                       onChange={(e) => setTyped(e.target.value)} />
                <button type="button" className="btn sm" disabled={busy || typed !== RS_WORD} style={{ color: 'var(--danger)' }} onClick={doReset}>
                  {busy ? 'Resetting…' : 'Reset'}
                </button>
                <button type="button" className="btn sm ghost" disabled={busy} onClick={() => { setPreview(null); setTyped(''); }}>Cancel</button>
              </div>
            </>
          )}
        </div>
      )}
    </Section>
  );
}


// Apps connected with OAuth (ChatGPT, Claude, ...): one row per app however many times it was connected (each device or
// re-add is a connection); what it may do, when it last acted, and one click that cuts all of its connections off.
function ConnectedAppsSection() {
  const api = window.VaultApi;
  const [apps, setApps] = useStateAcc(null);
  const [error, setError] = useStateAcc(null);
  const reload = () => { api.apps().then(setApps).catch((e) => setError(e.message)); };
  useEffectAcc(reload, []);
  const disconnect = async (row) => {
    setError(null);
    let a = row;
    try { a = (await api.apps()).find((x) => x.id === row.id) || row; } catch (e) { /* ask with what the page has */ }
    const m = a.used_minutes_ago;  // set only when the app acted within the last hour: the confirmation names it
    const note = m == null ? '' : `${a.name} was used ${m < 1 ? 'less than a minute' : m + ' minute' + (m === 1 ? '' : 's')} ago. `;
    const stops = a.connections > 1 ? `All ${a.connections} of its connections stop` : 'It stops';
    if (!confirm(`${note}Disconnect ${a.name}? ${stops} at once and it has to ask you again.`)) return;
    try { await api.disconnectApp(a.id); reload(); } catch (e) { setError(e.message); }
  };
  return (
    <Section title="Connected apps">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Apps you allowed to use your vault, such as ChatGPT or Claude: one row per app, even if you connected it on several
        devices or added it again. Disconnecting an app stops all of its connections at once; it has to ask you again.
        Add the address <code>{window.location.origin}/api/mcp</code> in the app to connect it.
      </p>
      {error && <p style={{ color: 'var(--danger)' }}>{error}</p>}
      {apps && apps.length === 0 && <p className="label-mono">No apps connected.</p>}
      {(apps || []).map((a) => (
        <div key={a.id} style={rowStyle}>
          <span className="label-mono">
            <strong>{a.name}</strong>{a.domain ? ` (${a.domain})` : ' · unverified'} ·{' '}
            {a.scopes.includes('write') ? 'read & write' : 'read-only'} ·{' '}
            {a.connections} connection{a.connections === 1 ? '' : 's'} · first connected {new Date(a.created_at).toLocaleDateString()} ·{' '}
            {a.last_used_at ? 'used ' + new Date(a.last_used_at).toLocaleDateString() : 'never used'}
            {a.idle && ' · idle (not used for 14 days)'}
            {!a.idle && a.idle_connections > 0 && ` · ${a.idle_connections} idle connection${a.idle_connections === 1 ? '' : 's'}`}
          </span>
          <button className="btn xs ghost" onClick={() => disconnect(a)}>Disconnect</button>
        </div>
      ))}
    </Section>
  );
}


// Take the collection anywhere: every export format, and what imports are accepted.
function MoveSection() {
  const [formats, setFormats] = useStateAcc([]);
  useEffectAcc(() => { window.VaultApi.exportFormats().then(setFormats).catch(() => setFormats([])); }, []);
  return (
    <Section title="Move your collection">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Your collection is yours. Download it for another app, or bring one in: imports accept Dragon Shield,
        Moxfield and generic CSV files. Printings matched on Scryfall are exported with Scryfall's set codes
        and numbers, so other apps recognise them.
      </p>
      {formats.map((f) => (
        <div key={f.format} style={rowStyle}>
          <span className="label-mono"><strong>{f.label}</strong> · {f.description}</span>
          <a className="btn xs" href={f._links.download.href} download>Download</a>
        </div>
      ))}
    </Section>
  );
}


function addedAgo(minutes) {
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
  const h = Math.floor(minutes / 60);
  return `${h} hour${h === 1 ? '' : 's'} ago`;
}

// "Sign out everywhere" (#347) in three steps, in this order on purpose. 1. End every OTHER browser's session now: a copied
// cookie then stops working, so nobody can add a way back in while the person looks. 2. Look at ALL the sign-in methods with
// their dates (the ones added in the last day are marked) and remove what is not theirs. 3. Done, or sign this browser out too.
// What can be removed is the server's answer (`removable`, `removable_reason`): the page never decides.
const WHY_NOT = {
  only_method: 'Your only way to sign in. Add your own passkey first (Add a passkey, above), then this can be removed.',
  provider_too_old: "Linked more than 24 hours ago: it can't be unlinked here yet. A recent-sign-in check that would allow it is proposed, not built (issue #347).",
  needs_older_method: "Can't be removed yet: no other sign-in is more than 24 hours old, so it can't be told from one someone else added. Try again when another is a day old.",
};

function SignOutEverywhere({ onCancel, onRemoved }) {
  const api = window.VaultApi;
  const [step, setStep] = useStateAcc('start'); // start | ending | review
  const [ended, setEnded] = useStateAcc(null); // what step 1 ended, as the server counted it
  const [found, setFound] = useStateAcc(null); // review: null while loading, false if it failed, else the server's page
  const [error, setError] = useStateAcc(null);
  const [busy, setBusy] = useStateAcc(null);
  const box = useRefAcc(null);
  useEffectAcc(() => { if (box.current) box.current.focus(); }, [step]);
  const load = () => api.signInMethods.all().then(setFound).catch(() => setFound(false));
  const endOthers = async () => {
    setError(null); setStep('ending');
    try { setEnded(await api.signInMethods.signOutOthers()); setStep('review'); setFound(null); load(); }
    catch (e) { setError(e.message); setStep('start'); }
  };
  const remove = async (m) => {
    setError(null); setBusy(m.kind + m.id);
    try { await api.signInMethods.remove(m); await load(); onRemoved && onRemoved(); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };
  const items = (found && found.items) || [];
  const recent = items.filter((m) => m.recently_added);
  const hours = (found && found.recent_hours) || 24;
  const removeAllRecent = async () => {
    const n = found.recent_count;
    if (!window.confirm(`Remove the ${n} sign-in method${n === 1 ? '' : 's'} added in the last ${hours} hours? This includes any you added yourself.`)) return;
    setError(null); setBusy('recent');
    try { await api.signInMethods.removeRecent(); await load(); onRemoved && onRemoved(); }
    catch (e) { setError(e.message); }
    finally { setBusy(null); }
  };
  const label = (m) => (m.kind === 'passkey' ? `Passkey: ${m.name}` : m.name);
  const when = (m) => (m.recently_added
    ? `${m.kind === 'passkey' ? 'added' : 'linked'} ${addedAgo(m.added_minutes_ago)} (${new Date(m.created_at).toLocaleString()})`
    : `${m.kind === 'passkey' ? 'added' : 'linked'} ${new Date(m.created_at).toLocaleDateString()}`);
  return (
    <div className="signout-all" role="group" aria-labelledby="signout-all-title" ref={box} tabIndex={-1}>
      <p id="signout-all-title" className="signout-all-title">Sign out everywhere</p>
      {step !== 'review' && (
        <>
          <p className="label-mono">
            Step 1 of 2. This ends every other browser signed in to your account, signs out every app you signed in to with
            the Vault app (they will ask you to sign in again), and removes the personal access tokens and connected apps made in
            the last 24 hours. This browser stays signed in. Older tokens and connected apps are not touched: check them under
            Agents &amp; API and Connected apps. Anyone who copied your session stops here. Then you check your sign-in methods,
            because they could have added a way back in.
          </p>
          {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
          <div className="signout-actions">
            <button className="btn sm" disabled={step === 'ending'} onClick={endOthers}>
              {step === 'ending' ? 'Signing out the others…' : 'Sign out the other browsers'}
            </button>
            <button className="btn sm ghost" onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}
      {step === 'review' && (
        <>
          <p className="label-mono" role="status">
            Every other browser is signed out. This one stays signed in.
            {ended && ended.apps_signed_out > 0 && ` ${ended.apps_signed_out} app sign-in${ended.apps_signed_out === 1 ? '' : 's'} ended.`}
            {ended && ended.tokens_removed > 0 && ` ${ended.tokens_removed} personal access token${ended.tokens_removed === 1 ? '' : 's'} made in the last 24 hours removed.`}
            {ended && ended.connected_apps_removed > 0 && ` ${ended.connected_apps_removed} connected app${ended.connected_apps_removed === 1 ? '' : 's'} made in the last 24 hours removed.`}
            {' '}Step 2 of 2: check every sign-in method is yours. Removing one signs the other browsers out again.
          </p>
          {found === null && <p className="label-mono" role="status">Loading your sign-in methods…</p>}
          {found === false && (
            <p className="label-mono" role="alert" style={{ color: 'var(--danger)' }}>
              Couldn't load your sign-in methods. They are listed under Sign-in methods above; check them there.
            </p>
          )}
          {found && recent.length > 0 && (
            <p className="label-mono" role="status">
              <strong>{recent.length} added in the last {hours} hours</strong> (first below). Not yours? Remove them.
            </p>
          )}
          {found && recent.length === 0 && (
            <p className="label-mono" role="status">
              Nothing was added in the last {hours} hours. The older ones are listed too: a method added earlier would be here. Check each one is yours.
            </p>
          )}
          {found && items.length > 0 && (
            <ul className="signout-methods" aria-label="Your sign-in methods">
              {items.map((m) => (
                <li key={m.kind + m.id} className={'signout-method' + (m.recently_added ? ' signout-recent' : '')}>
                  <span className="label-mono">
                    <strong>{label(m)}</strong> · {when(m)}
                    {m.recently_added && <span className="signout-flag"> · new</span>}
                    {!m.removable && m.removable_reason && <span className="signout-why"><br />{WHY_NOT[m.removable_reason]}</span>}
                  </span>
                  {m.removable && (
                    <button className="btn xs" disabled={busy !== null} onClick={() => remove(m)}
                      aria-label={`Remove ${m.kind === 'passkey' ? 'passkey ' : ''}${m.name}, ${when(m)}`}>
                      {busy === m.kind + m.id ? 'Removing…' : 'Remove'}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {found && found._links && found._links.next && (
            <p className="label-mono" role="alert" style={{ color: 'var(--danger)' }}>
              More than {items.length} sign-in methods: only the newest are shown. Remove the ones you don't recognise, then open this again.
            </p>
          )}
          {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
          <div className="signout-actions">
            {/* Only when the server says it can succeed (something recent and an older method stays) and this list is the whole list. */}
            {found && found.recent_removable && !(found._links && found._links.next) && (
              <button className="btn sm" disabled={busy !== null} onClick={removeAllRecent}>
                {busy === 'recent' ? 'Removing…' : `Remove the ${found.recent_count} added in the last ${hours} hours`}
              </button>
            )}
            <button className="btn sm" onClick={onCancel}>Done</button>
            <button className="btn sm ghost" disabled={busy !== null} onClick={() => signOut({ everywhere: true })}>Also sign out of this browser</button>
          </div>
        </>
      )}
    </div>
  );
}

// Passkeys and linked providers: add a passkey to this account, remove one, link another provider.
function SignInMethods({ me, onChanged }) {
  const api = window.VaultApi;
  const [keys, setKeys] = useStateAcc([]);
  const [info, setInfo] = useStateAcc(null);
  const [error, setError] = useStateAcc(null);
  const [leaving, setLeaving] = useStateAcc(false); // the "Sign out everywhere" check is open
  const reload = () => { api.passkeys.list().then(setKeys).catch(() => setKeys([])); };
  useEffectAcc(() => { reload(); api.providers().then(setInfo).catch(() => {}); }, []);
  const add = async () => {
    setError(null);
    try { await api.passkeys.add(); reload(); onChanged && onChanged(); } catch (e) { setError(api.passkeys.explain(e)); }
  };
  const remove = async (id) => {
    setError(null);
    try { await api.passkeys.remove(id); reload(); onChanged && onChanged(); } catch (e) { setError(e.message); }
  };
  const linked = new Set((me && me.providers) || []);
  // Passkeys get their own rows below; every other sign-in is one row with its logo.
  const providerRows = [...linked].filter((p) => p !== 'passkey');
  const linkable = info ? info.providers.filter((p) => !linked.has(p)) : [];
  const providerName = (p) => (PROVIDERS[p] && PROVIDERS[p].name) || (p === 'dev' ? 'Local dev sign-in' : p);
  return (
    <Section title="Sign-in methods">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Add a passkey to sign in with Face ID, Touch ID, Windows Hello or your phone. Linking another provider lets you
        use either. A provider that already opened its own, empty Vault account moves here and that account is removed;
        one whose account holds data is never merged.
      </p>
      {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
      <ul aria-label="Linked sign-in methods" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
        {providerRows.map((p) => (
          <li key={p} style={{ ...rowStyle, justifyContent: 'flex-start' }}>
            <span className="provider-logo" aria-hidden="true">{PROVIDERS[p] ? PROVIDERS[p].logo : '⚿'}</span>
            <span className="label-mono">{providerName(p)} · linked</span>
          </li>
        ))}
        {keys.map((k) => (
          <li key={'pk' + k.id} style={rowStyle}>
            <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="provider-logo" aria-hidden="true">⚿</span>
              <span className="label-mono">
                Passkey: {k.name}{k.synced ? ' · synced' : ''} · added {new Date(k.created_at).toLocaleDateString()}
                {k.last_used_at ? ' · used ' + new Date(k.last_used_at).toLocaleDateString() : ''}
              </span>
            </span>
            <button className="btn xs ghost" aria-label={`Remove passkey ${k.name}`} onClick={() => remove(k.id)}>Remove</button>
          </li>
        ))}
        {providerRows.length === 0 && keys.length === 0 && <li className="label-mono">—</li>}
      </ul>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6 }}>
        {info && info.passkeys && api.passkeys.supported() && <button className="btn sm" onClick={add}>Add a passkey</button>}
        {linkable.map((p) => <a key={p} className="btn sm ghost" href={`/api/auth/login/${p}`}>Link {p[0].toUpperCase() + p.slice(1)}</a>)}
        {/* On a phone the top bar has no room for Sign out, so it is here (layout.css shows .m-only). */}
        <button className="btn sm m-only" onClick={() => signOut()}>Sign out</button>
        <button className="btn sm ghost" title="Opens a check: first signs out the other browsers (this one stays signed in), then lists your sign-in methods"
                aria-expanded={leaving} onClick={() => setLeaving(true)}>Sign out everywhere</button>
      </div>
      {leaving && <SignOutEverywhere onCancel={() => setLeaving(false)} onRemoved={() => { reload(); onChanged && onChanged(); }} />}
    </Section>
  );
}
