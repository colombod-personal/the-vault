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
        {localStorage.getItem('vault_pending_invite') && (
          <p className="label-mono" style={{ marginTop: 16 }}>Sign in to accept the invite you opened.</p>
        )}
        <p className="label-mono" style={{ marginTop: 20 }}>
          Your collection stays private unless you share it.{' '}
          <a href="/privacy.html" style={{ color: 'var(--gold)', textDecoration: 'underline' }}>Privacy notice</a>
          {' · '}<a href="/credits.html" style={{ color: 'var(--gold)', textDecoration: 'underline' }}>Credits &amp; thanks</a>
        </p>
      </div>
      <div style={{ width: 'min(720px, 100%)' }}><VaultFooter /></div>
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
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginLeft: 'auto' }}>
      {!readOnly && <ImportButton onImported={onImported} />}
      <button className="btn sm ghost" title={me && me.email ? me.email : ''} onClick={onAccount}>
        {me ? (me.name || me.email || 'Account') : 'Account'}
      </button>
      <button className="btn sm ghost" onClick={() => window.VaultApi.logout().then(() => location.reload())}>Sign out</button>
    </div>
  );
}

Object.assign(window, { SignIn, ImportButton, EmptyVault, AccountMenu, describeChanges });

// ---- Account panel: profile, sharing, shared with me, saved decks, GDPR export/delete ----

function Modal({ title, onClose, children }) {
  useEffectAcc(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgb(0 0 0 / 0.55)', zIndex: 50,
      display: 'grid', placeItems: 'start center', overflowY: 'auto', padding: '48px 16px' }}>
      <div className="panel" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(720px, 100%)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h2 className="h1" style={{ fontSize: 28, margin: 0 }}>{title}</h2>
          <button className="btn xs ghost" onClick={onClose}>✕</button>
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

function AccountPanel({ me, onClose, onOpenShared, onOpenDeck, onMeChanged }) {
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

  const reload = () => {
    api.shares().then(setShares);
    api.sharedWithMe().then(setIncoming);
    api.decks().then(setDecks);
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

      <Section title="Profile">
        <div style={rowStyle}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Display name"
            style={{ flex: 1 }} />
          <button className="btn sm" onClick={saveName}>Save</button>
        </div>
        <p className="label-mono">
          {me?.email || 'No e-mail'} · signed in with {(me?.providers || []).join(', ') || '—'}
        </p>
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
              <button className="btn xs" onClick={() => onOpenDeck(d.text)}>Open</button>
              <button className="btn xs" onClick={() => share('deck', d.id)}>Share</button>
              <button className="btn xs ghost" onClick={() => removeDeck(d.id)}>Delete</button>
            </span>
          </div>
        ))}
      </Section>

      <MoveSection />

      <AgentsSection />

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
    <footer className="vault-footer" style={{ margin: '48px 24px 24px', paddingTop: 16, borderTop: '1px solid var(--border)',
      fontFamily: 'var(--mono)', fontSize: 11, lineHeight: 1.7, color: 'var(--muted)' }}>
      <p>
        Card data, images &amp; prices from {link('https://scryfall.com', 'Scryfall')} (prices sourced by Scryfall from{' '}
        {link('https://www.tcgplayer.com', 'TCGplayer')} and {link('https://www.cardmarket.com', 'Cardmarket')})
        {' · '}Decks from {link('https://archidekt.com', 'Archidekt')}
        {' · '}Collections imported from {link('https://mtg.dragonshield.com', 'Dragon Shield')} or {link('https://moxfield.com', 'Moxfield')}
        {' · '}Card art by the credited artists
        {' · '}<a href="/credits.html" style={style}><strong>Credits &amp; thanks</strong></a>
        {' · '}<a href="/privacy.html" style={style}>Privacy</a>
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

Object.assign(window, { AccountPanel, VaultFooter });


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
        Connect your own AI agents (Claude, ChatGPT, scripts) to your vault through its MCP server or HTTP API.
        A token acts as you. Read-only unless you allow changes; revoke it any time.{' '}
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
