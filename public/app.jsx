// Main app — router + drawer + data loading
const { useState: useStateApp, useEffect: useEffectApp } = React;

// Shared freshness helper — describes how stale a calculated value is.
window.vaultFreshness = function (iso) {
  if (!iso) return { rel: 'unknown', abs: '—', tone: 'muted', days: null };
  const then = new Date(iso);
  const days = Math.max(0, Math.floor((Date.now() - then.getTime()) / 86400000));
  const abs = then.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  let rel;
  if (days === 0) rel = 'updated today';
  else if (days === 1) rel = 'updated yesterday';
  else if (days < 60) rel = `updated ${days} days ago`;
  else rel = `updated ${Math.round(days / 30)} months ago`;
  const tone = days <= 2 ? 'fresh' : days <= 14 ? 'aging' : 'stale';
  return { rel, abs, tone, days };
};

// Pick the live USD price for a card from the Scryfall cache (foil-aware).
window.vaultPriceFor = function (card) {
  const s = window.Scryfall && window.Scryfall.cached(card.n, card.s, card.cn);
  if (!s || !s.prices) return null;
  const foil = /foil/i.test(card.p || '');
  const raw = foil ? (s.prices.usd_foil || s.prices.usd) : (s.prices.usd || s.prices.usd_foil);
  const v = parseFloat(raw);
  return isFinite(v) ? v : null;
};

// Recompute a data object's market values from freshly-cached Scryfall prices.
// `at` becomes the new "calculated" timestamp. Cost basis (what you paid) never changes.
window.vaultRecompute = function (base, at) {
  const setVal = {};
  let totalMarket = 0;
  const cards = base.cards.map((c) => {
    const live = window.vaultPriceFor(c);
    const mk = live != null ? live : c.mk;
    const v = mk * (c.q || 0);
    totalMarket += v;
    setVal[c.s] = (setVal[c.s] || 0) + v;
    return mk === c.mk ? c : { ...c, mk };
  });
  const sets = base.sets
    .map((s) => ({ ...s, value: setVal[s.code] != null ? setVal[s.code] : s.value }))
    .sort((a, b) => b.value - a.value);
  const meta = { ...base.meta, totalMarket, generatedAt: at };
  return { ...base, cards, sets, meta };
};

const VAULT_REFRESH_KEY = 'vault_refreshed_at';
const VAULT_INVITE_KEY = 'vault_pending_invite';

// An invite link (/?invite=TOKEN) may arrive before sign-in: park the token, clean the URL.
(() => {
  const params = new URLSearchParams(location.search);
  const token = params.get('invite');
  if (token) {
    try { localStorage.setItem(VAULT_INVITE_KEY, token); } catch {}
    params.delete('invite');
    history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params : ''));
  }
})();
const { useTweaks, TweaksPanel, TweakSection, TweakSlider, TweakToggle, TweakRadio, TweakSelect, TweakColor, TweakButton } = window;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#c79b3f",
  "currency": "USD",
  "compactNumbers": false,
  "density": "regular",
  "landing": "dashboard",
  "graphTopN": 200,
  "imageQuality": "normal",
  "showPnL": true
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [data, setData] = useStateApp(null);
  const [route, setRoute] = useStateApp({ view: t.landing || 'dashboard' });
  const [drawerCard, setDrawerCard] = useStateApp(null);
  const [loadProgress, setLoadProgress] = useStateApp('Reading vault…');
  const [refreshing, setRefreshing] = useStateApp(false);
  const [refreshProgress, setRefreshProgress] = useStateApp(null);
  const [refreshError, setRefreshError] = useStateApp(null);
  const [auth, setAuth] = useStateApp('checking'); // 'checking' | 'signed-out' | 'signed-in'
  const [me, setMe] = useStateApp(null);
  const [notice, setNotice] = useStateApp(null);
  const [accountOpen, setAccountOpen] = useStateApp(false);
  const [viewing, setViewing] = useStateApp(null); // { id, from } while looking at someone's shared collection
  const [deckText, setDeckText] = useStateApp(null); // deck opened from saved/shared decks

  // Apply theme tweaks to CSS variables
  useEffectApp(() => {
    document.documentElement.style.setProperty('--gold', t.accent);
    // Derive a slightly brighter gold-2 by mixing with white
    const gold2 = t.accent;
    document.documentElement.style.setProperty('--gold-2', gold2);
    // Density: adjust base font size & section padding
    if (t.density === 'compact') {
      document.body.style.fontSize = '13px';
    } else if (t.density === 'comfy') {
      document.body.style.fontSize = '15px';
    } else {
      document.body.style.fontSize = '14px';
    }
    // Expose helpers globally for views to use
    window.__vault = {
      currency: t.currency,
      compactNumbers: t.compactNumbers,
      imageQuality: t.imageQuality,
      showPnL: t.showPnL,
    };
  }, [t]);

  // Load the signed-in user's collection from the server (prices are refreshed there daily).
  const loadCollection = async () => {
    try {
      setLoadProgress('Opening your vault…');
      const [who, j] = await Promise.all([window.VaultApi.me(), window.VaultApi.collection()]);
      setMe(who);
      setAuth('signed-in');
      setViewing(null);
      acceptPendingInvite();
      // If the user pulled live prices in this browser since the server's last sync, replay them.
      const at = localStorage.getItem(VAULT_REFRESH_KEY);
      const serverAt = j.meta.generatedAt;
      if (at && (!serverAt || at > serverAt) && window.Scryfall && window.Scryfall.cacheSize() > 0) {
        setData(window.vaultRecompute(j, at));
      } else {
        setData(j);
      }
    } catch (e) {
      if (e.status === 401) setAuth('signed-out');
      else setLoadProgress('Failed to load collection: ' + e.message);
    }
  };
  useEffectApp(() => { loadCollection(); }, []);

  async function acceptPendingInvite() {
    let token = null;
    try { token = localStorage.getItem(VAULT_INVITE_KEY); localStorage.removeItem(VAULT_INVITE_KEY); } catch {}
    if (!token) return;
    try {
      const res = await window.VaultApi.acceptInvite(token);
      const what = res.kind === 'deck' ? `their deck “${res.deck_name}”` : 'their collection';
      setNotice(`${res.from} shared ${what} with you. Open it from Account → Shared with me.`);
    } catch (e) {
      setNotice('Invite link: ' + e.message);
    }
  }

  const openShared = async (s) => {
    setAccountOpen(false);
    try {
      if (s.kind === 'collection') {
        const j = await window.VaultApi.sharedCollection(s.id);
        setData(j);
        setViewing({ id: s.id, from: s.from });
        setRoute({ view: 'dashboard' });
      } else {
        const d = await window.VaultApi.sharedDeck(s.id);
        setDeckText(d.text);
        setNotice(`${d.from}'s deck “${d.name}”, checked against your collection.`);
        if (viewing) await backToMine();
        setRoute({ view: 'decks' });
      }
    } catch (e) {
      setNotice('Could not open: ' + e.message);
    }
  };
  const backToMine = async () => {
    setViewing(null);
    setData(null);
    await loadCollection();
  };
  const openDeck = (text) => {
    setAccountOpen(false);
    setDeckText(text);
    setRoute({ view: 'decks' });
  };
  const noticeBanner = notice && (
    <div className="panel panel-tight" style={{ margin: '12px 24px 0', display: 'flex', justifyContent: 'space-between' }}>
      <span className="label-mono">{notice}</span>
      <button className="btn xs ghost" onClick={() => setNotice(null)}>✕</button>
    </div>
  );
  const accountPanel = accountOpen && (
    <AccountPanel
      me={me}
      onClose={() => setAccountOpen(false)}
      onOpenShared={openShared}
      onOpenDeck={openDeck}
      onMeChanged={() => window.VaultApi.me().then(setMe)}
    />
  );

  const onImported = (res) => {
    setNotice(`Imported ${res.copies.toLocaleString()} cards: ${window.describeChanges(res.changes)}.`);
    setData(null);
    loadCollection();
  };

  // Force-pull current prices for every printing from Scryfall, then recompute.
  const doRefresh = async () => {
    if (refreshing || !data) return;
    setRefreshError(null);
    setRefreshing(true);
    const ids = data.cards.map((c) => ({ name: c.n, set: c.s, collector_number: c.cn }));
    setRefreshProgress({ done: 0, total: ids.length });
    try {
      const results = await window.Scryfall.collection(
        ids,
        (p) => setRefreshProgress({ done: p.done, total: p.total }),
        { force: true }
      );
      const got = results.filter(Boolean).length;
      const failedBatches = results.netErrors || 0;
      const completed = failedBatches === 0; // every batch came back from Scryfall
      const blocked = failedBatches >= (results.batches || 1);

      if (completed) {
        // Full pass — safe to stamp "as of now". (got < total just means some
        // printings genuinely have no price on Scryfall; that's not staleness.)
        const at = new Date().toISOString();
        localStorage.setItem(VAULT_REFRESH_KEY, at);
        setData(window.vaultRecompute(data, at));
        setRefreshError(
          got < ids.length
            ? `Updated. ${(ids.length - got).toLocaleString()} printings have no Scryfall price and kept their last value.`
            : null
        );
      } else {
        // Incomplete — DON'T claim freshness. Leave values and timestamp untouched.
        setRefreshError(
          blocked
            ? "Can't reach Scryfall on this network — nothing was updated, so values still show the last good prices. Try again off VPN / without an ad-blocker."
            : `Update didn't finish — ${failedBatches} of ${results.batches} batches couldn't be reached. Nothing was changed; press Update now to retry.`
        );
      }
    } catch (e) {
      setRefreshError('Update failed: ' + e.message);
    } finally {
      setRefreshing(false);
      setRefreshProgress(null);
    }
  };

  // The server syncs Scryfall's daily bulk file for everyone; this just reloads its latest prices.
  const doBulkSync = async () => {
    if (refreshing || !data) return;
    setRefreshError(null);
    setRefreshing(true);
    try {
      localStorage.removeItem(VAULT_REFRESH_KEY);
      const j = viewing ? await window.VaultApi.sharedCollection(viewing.id) : await window.VaultApi.collection();
      setData(j);
      setRefreshError(j.meta.generatedAt ? null : 'The server has not synced prices yet; values use your file\'s prices.');
    } catch (e) {
      setRefreshError('Reload failed: ' + e.message);
    } finally {
      setRefreshing(false);
      setRefreshProgress(null);
    }
  };

  if (auth === 'signed-out') return <SignIn />;
  if (data && data.meta.totalQty === 0) {
    return (
      <div className="app">
        <header className="topbar">
          <div className="brand"><span className="mark"><span>V</span></span><span className="title">The Vault</span></div>
          <AccountMenu me={me} onImported={onImported} onAccount={() => setAccountOpen(true)} />
        </header>
        {noticeBanner}
        <main><EmptyVault onImported={onImported} /></main>
        {accountPanel}
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontFamily: 'var(--display)', fontSize: 36, color: 'var(--gold)', marginBottom: 16 }}>◇</div>
          <p className="label-mono">{loadProgress}</p>
          <div className="spinner" style={{ marginTop: 16, width: 18, height: 18 }}></div>
        </div>
      </div>
    );
  }

  const nav = (view, extra = {}) => setRoute({ view, ...extra });

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="mark"><span>V</span></span>
          <span className="title">The Vault</span>
          <span className="subtitle">MTG Collection</span>
        </div>
        <nav className="nav">
          <button className={route.view === 'dashboard' ? 'active' : ''} onClick={() => nav('dashboard')}>Vault</button>
          <button className={route.view === 'browse' ? 'active' : ''} onClick={() => nav('browse')}>Browse</button>
          <button className={route.view === 'sets' || route.view === 'setdetail' ? 'active' : ''} onClick={() => nav('sets')}>Sets</button>
          <button className={route.view === 'decks' ? 'active' : ''} onClick={() => nav('decks')}>Decks</button>
          <button className={route.view === 'lab' ? 'active' : ''} onClick={() => nav('lab')}>Lab</button>
          <button className={route.view === 'graph' ? 'active' : ''} onClick={() => nav('graph')}>Graph</button>
        </nav>
        <AccountMenu me={me} onImported={onImported} onAccount={() => setAccountOpen(true)} readOnly={!!viewing} />
      </header>
      {viewing && (
        <div className="panel panel-tight" style={{ margin: '12px 24px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderColor: 'var(--gold)' }}>
          <span className="label-mono">
            Viewing {viewing.from}'s collection · read-only{data.meta.costsHidden ? ' · prices paid are private' : ''}
          </span>
          <button className="btn xs" onClick={backToMine}>Back to my vault</button>
        </div>
      )}
      {noticeBanner}

      <main>
        {route.view === 'dashboard' && (
          <Dashboard
            data={data}
            gotoBrowse={(q) => nav('browse', { initialQuery: q })}
            gotoSet={code => nav('setdetail', { code })}
            gotoValuation={() => nav('valuation')}
            onRefresh={doRefresh}
            onBulkSync={doBulkSync}
            refreshing={refreshing}
            refreshProgress={refreshProgress}
            refreshError={refreshError}
            openCard={c => setDrawerCard(c)}
          />
        )}
        {route.view === 'browse' && (
          <Browse data={data} openCard={c => setDrawerCard(c)} initialQuery={route.initialQuery} />
        )}
        {route.view === 'sets' && (
          <Sets data={data} onSetClick={code => nav('setdetail', { code })} />
        )}
        {route.view === 'setdetail' && (
          <SetDetail data={data} code={route.code} onBack={() => nav('sets')} openCard={c => setDrawerCard(c)} />
        )}
        {route.view === 'decks' && (
          <DeckView key={deckText || 'deck'} data={data} openCard={c => setDrawerCard(c)} initialText={deckText} />
        )}
        {route.view === 'lab' && (
          <Lab data={data} openCard={c => setDrawerCard(c)} />
        )}
        {route.view === 'graph' && (
          <GraphView data={data} openCard={c => setDrawerCard(c)} />
        )}
        {route.view === 'valuation' && (
          <Valuation
            data={data}
            onBack={() => nav('dashboard')}
            onRefresh={doRefresh}
            onBulkSync={doBulkSync}
            refreshing={refreshing}
            refreshProgress={refreshProgress}
            refreshError={refreshError}
            openCard={c => setDrawerCard(c)}
          />
        )}
      </main>

      {drawerCard && <CardDrawer card={drawerCard} onClose={() => setDrawerCard(null)} />}
      {accountPanel}

      <TweaksPanel title="Tweaks">
        <TweakSection label="Theme" />
        <TweakColor
          label="Accent"
          value={t.accent}
          options={['#c79b3f', '#b86a3a', '#5b8f8b', '#7a6da8', '#9aa86b', '#d27d6f']}
          onChange={(v) => setTweak('accent', v)}
        />
        <TweakRadio
          label="Density"
          value={t.density}
          options={['compact', 'regular', 'comfy']}
          onChange={(v) => setTweak('density', v)}
        />

        <TweakSection label="Data display" />
        <TweakRadio
          label="Currency"
          value={t.currency}
          options={['USD', 'EUR']}
          onChange={(v) => setTweak('currency', v)}
        />
        <TweakToggle
          label="Compact numbers"
          subtitle="$1.2K vs $1,234"
          value={t.compactNumbers}
          onChange={(v) => setTweak('compactNumbers', v)}
        />
        <TweakToggle
          label="Show P&L on dashboard"
          value={t.showPnL}
          onChange={(v) => setTweak('showPnL', v)}
        />

        <TweakSection label="Defaults" />
        <TweakSelect
          label="Landing tab"
          value={t.landing}
          options={['dashboard', 'browse', 'sets', 'decks', 'lab', 'graph']}
          onChange={(v) => setTweak('landing', v)}
        />
        <TweakSlider
          label="Graph default top-N"
          value={t.graphTopN}
          min={50}
          max={800}
          step={10}
          onChange={(v) => setTweak('graphTopN', v)}
        />
        <TweakRadio
          label="Image quality"
          value={t.imageQuality}
          options={['small', 'normal']}
          onChange={(v) => setTweak('imageQuality', v)}
        />

        <TweakSection label="Cache" />
        <TweakButton
          label="Clear Scryfall cache"
          subtitle={`${window.Scryfall?.cacheSize?.() || 0} cards cached`}
          onClick={() => {
            window.Scryfall.clearCache();
            alert('Scryfall cache cleared. Refresh to start over.');
          }}
        />
      </TweaksPanel>
    </div>
  );
}

function CardDrawer({ card, onClose }) {
  const [scry, setScry] = useStateApp(() => card._scry || window.Scryfall.cached(card.n, card.s, card.cn));
  useEffectApp(() => {
    if (scry) return;
    let dead = false;
    window.Scryfall.collection([{ name: card.n, set: card.s, collector_number: card.cn }]).then(arr => {
      if (!dead && arr[0]) setScry(arr[0]);
    });
    return () => { dead = true; };
  }, [card]);

  const total = (card.mk || 0) * (card.q || 0);
  const pnl = total - (card.pd || 0);

  useEffectApp(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose}></div>
      <div className="drawer">
        {/* Blurred set icon as ambient background */}
        {card.s && window.SetIcons?.get(card.s)?.icon && (
          <div className="drawer-bg">
            <img src={window.SetIcons.get(card.s).icon} alt="" style={{ filter: 'brightness(0) invert(1)' }} />
          </div>
        )}
        <button className="close" onClick={onClose}>×</button>
        <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 20, marginBottom: 24 }}>
          <div style={{ aspectRatio: '488 / 680', background: 'var(--bg-2)', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }}>
            {scry?.img_normal ? (
              <img src={scry.img_normal} alt={card.n} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
            ) : (
              <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: 'var(--muted)', padding: 12, textAlign: 'center', fontFamily: 'var(--mono)', fontSize: 11, background: 'repeating-linear-gradient(135deg, var(--surface-2) 0 8px, var(--surface) 8px 16px)' }}>
                {card.n}
              </div>
            )}
          </div>
          <div>
            <h2 className="h2" style={{ fontSize: 26, marginBottom: 6 }}>{card.n}</h2>
            {scry?.mana_cost && <p style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--muted)' }}>{scry.mana_cost}</p>}
            {scry?.type_line && <p style={{ fontSize: 13, marginTop: 8 }}>{scry.type_line}</p>}
            {scry?.color_identity && <div style={{ marginTop: 8 }}><ColorIdentity colors={scry.color_identity} /></div>}
            <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontFamily: 'var(--mono)', fontSize: 11 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span className="muted">Set</span>
                {window.SetIcon ? <SetIcon code={card.s} size={16} /> : null}
                <span style={{ color: 'var(--gold)' }}>{card.s}</span>
                <span className="muted">·</span>
                <span>{card.sn}</span>
              </div>
              <div><span className="muted">#</span> {card.cn}</div>
              <div><span className="muted">Print</span> {card.p}</div>
              <div><span className="muted">Cond</span> {card.c}</div>
            </div>
          </div>
        </div>

        {scry?.oracle_text && (
          <div className="panel" style={{ marginBottom: 20 }}>
            <p className="eyebrow" style={{ marginBottom: 8 }}>Oracle text</p>
            <p style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{scry.oracle_text}</p>
            {(scry.power || scry.loyalty) && (
              <p style={{ fontFamily: 'var(--mono)', fontSize: 12, marginTop: 10, color: 'var(--gold)' }}>
                {scry.power ? `${scry.power}/${scry.toughness}` : `Loyalty ${scry.loyalty}`}
              </p>
            )}
          </div>
        )}

        {card.q > 0 && (
          <div className="panel" style={{ marginBottom: 20 }}>
            <p className="eyebrow" style={{ marginBottom: 12 }}>Your holdings</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              <Stat label="Quantity" value={card.q} />
              <Stat label="Total value" value={`$${total.toFixed(2)}`} color="var(--gold)" />
              <Stat label="Spent" value={`$${(card.pd || 0).toFixed(2)}`} muted />
              <Stat label="P&L" value={`${pnl >= 0 ? '+' : '−'}$${Math.abs(pnl).toFixed(2)}`} color={pnl >= 0 ? 'var(--good)' : 'var(--danger)'} />
            </div>
            {card.fd && (
              <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 12 }}>
                first bought {card.fd}{card.fd !== card.ld ? ` · last bought ${card.ld}` : ''}
              </p>
            )}
          </div>
        )}

        {card._ownEntries && card._ownEntries.length > 0 && (
          <div className="panel" style={{ marginBottom: 20 }}>
            <p className="eyebrow" style={{ marginBottom: 12 }}>All printings you own</p>
            <table className="tbl">
              <thead>
                <tr><th>Set</th><th>#</th><th>Print</th><th className="num">Qty</th><th className="num">Each</th></tr>
              </thead>
              <tbody>
                {card._ownEntries.map((e, i) => (
                  <tr key={i}>
                    <td style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      {window.SetIcon && <SetIcon code={e.s} size={14} />}
                      <span style={{ color: 'var(--gold)', fontFamily: 'var(--mono)', fontSize: 11 }}>{e.s}</span> {e.sn}
                    </td>
                    <td className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{e.cn}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{e.p}</td>
                    <td className="num">{e.q}</td>
                    <td className="num" style={{ color: 'var(--gold)' }}>${e.mk.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {scry?.prices && (
          <div className="panel">
            <p className="eyebrow" style={{ marginBottom: 12 }}>Live Scryfall prices</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              <Stat label="USD" value={scry.prices.usd ? `$${scry.prices.usd}` : '—'} />
              <Stat label="USD Foil" value={scry.prices.usd_foil ? `$${scry.prices.usd_foil}` : '—'} />
              <Stat label="EUR" value={scry.prices.eur ? `€${scry.prices.eur}` : '—'} />
              <Stat label="EUR Foil" value={scry.prices.eur_foil ? `€${scry.prices.eur_foil}` : '—'} />
            </div>
            {scry.scryfall_uri && (
              <a href={scry.scryfall_uri} target="_blank" rel="noopener noreferrer" style={{ display: 'inline-block', marginTop: 14, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--gold)', textDecoration: 'underline' }}>
                Open on Scryfall ↗
              </a>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function Stat({ label, value, color, muted }) {
  return (
    <div style={{ padding: 10, background: 'var(--bg-2)', borderRadius: 4, border: '1px solid var(--border)' }}>
      <div className="label-mono">{label}</div>
      <div style={{ fontFamily: 'var(--mono)', fontSize: 14, fontWeight: 500, marginTop: 4, color: color || (muted ? 'var(--muted)' : 'var(--text)') }}>{value}</div>
    </div>
  );
}

function RefreshButton({ refreshing, refreshProgress, onRefresh, className }) {
  const pct = refreshProgress && refreshProgress.total
    ? Math.round((refreshProgress.done / refreshProgress.total) * 100) : 0;
  return (
    <button
      className={`btn sm refresh-btn ${className || ''}`}
      disabled={refreshing}
      onClick={(e) => { e.stopPropagation(); onRefresh(); }}
      title="Re-fetch current prices from Scryfall"
    >
      {refreshing ? (
        <>
          <span className="spinner spinner-xs"></span>
          <span>Updating… {pct}%</span>
        </>
      ) : (
        <>
          <span className="rfx">↻</span>
          <span>Update now</span>
        </>
      )}
    </button>
  );
}
window.RefreshButton = RefreshButton;

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);