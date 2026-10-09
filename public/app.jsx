// Main app — router + drawer + data loading
const { useState: useStateApp, useEffect: useEffectApp, useRef: useRefApp } = React;

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

// Load what a view shows from the server (VaultApi's collection client): { data, error, loading }.
// `load` runs again when `deps` change (e.g. the collection's version, after an import or a price
// refresh); the previous answer stays on screen while the next one loads.
window.useVaultQuery = function (load, deps) {
  const [state, setState] = useStateApp({ data: null, error: null, loading: true });
  useEffectApp(() => {
    let live = true;
    setState((s) => ({ ...s, error: null, loading: true }));
    Promise.resolve().then(load).then(
      (data) => { if (live) setState({ data, error: null, loading: false }); },
      (error) => { if (live) setState((s) => ({ data: s.data, error, loading: false })); });
    return () => { live = false; };
  }, deps);
  return state;
};

// One printing's "Spent" and "P&L" cells, from the server's `paid` and `gain` (the P&L of the
// copies with a known cost): "private" when the owner hides costs, "—" when no cost is known.
window.vaultSpentText = (card, costsHidden) =>
  costsHidden ? 'private' : card.gain != null && card.pd != null ? `$${card.pd.toFixed(2)}` : '—';
window.vaultGainText = (card, costsHidden) =>
  costsHidden ? 'private' : card.gain == null ? '—' : `${card.gain >= 0 ? '+' : '−'}$${Math.abs(card.gain).toFixed(2)}`;

// Props that make a clickable tile work from the keyboard and for screen readers, like a button:
// focusable, announced as a button, activated with Enter or Space.
window.vaultPressable = (action, label) => ({
  role: 'button', tabIndex: 0, onClick: action, ...(label ? { 'aria-label': label } : {}),
  onKeyDown: (e) => {
    if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); action(); }
  },
});

// Older versions kept the time of the last "Update now" here and recomputed values in the
// browser; prices are refreshed and values computed by the server now, so drop it.
try { localStorage.removeItem('vault_refreshed_at'); } catch {}
const VAULT_INVITE_KEY = 'vault_pending_invite';

// An invite link (/?invite=TOKEN) may arrive before sign-in: park the token, clean the URL.
// "Link Google" (etc.) comes back as /?linked=… or /?link_error=… (lib/linkNotice.js words it):
// show it once, clean the URL.
const VAULT_START_NOTICE = (() => {
  const params = new URLSearchParams(location.search);
  const token = params.get('invite');
  const link = window.VaultLinkNotice.noticeFromSearch(location.search);
  if (token) {
    try { localStorage.setItem(VAULT_INVITE_KEY, token); } catch {}
  }
  const rest = link ? link.params : params;
  rest.delete('invite');
  if (token || link) history.replaceState(null, '', location.pathname + (rest.toString() ? '?' + rest : ''));
  return link ? link.text : null;
})();
// Navigation lives in the URL hash (#/browse, #/sets/MKM, …), so the browser's Back and Forward
// buttons, refresh and bookmarks work. Opening a card or the account panel adds a history
// entry too, so Back closes it before leaving the view.
const VAULT_VIEWS = ['dashboard', 'browse', 'sets', 'decks', 'lab', 'graph', 'valuation', 'help'];
const VAULT_TAG = /^[a-z0-9:-]{1,40}$/;  // a tag's shape (the server's rule): anything else in the address is ignored
// The views that follow the scope (#130): one bucket, one tag or both, kept in the address (#/dashboard?bucket=3&tag=trade). It is global
// to the app until cleared: choosing it in Browse, the Vault, Sets or Value keeps it in the other three. Lab, Graph and Decks read the
// whole inventory and keep the choice for when the person comes back. VAULT_ANALYTICS are the views whose figures come from the
// selection's own summary (Browse lists the selection's cards itself).
const VAULT_SCOPED = new Set(['browse', 'dashboard', 'sets', 'setdetail', 'valuation']);
const VAULT_ANALYTICS = new Set(['dashboard', 'sets', 'setdetail', 'valuation']);
const vaultScopeOf = (r) => ({ ...(r && r.bucket ? { bucket: r.bucket } : {}), ...(r && r.tag ? { tag: r.tag } : {}) });
function vaultScopeFromSearch(search) {
  const q = new URLSearchParams(search || '');
  const bucket = Number(q.get('bucket')), tag = q.get('tag') || '';
  return { ...(Number.isInteger(bucket) && bucket > 0 ? { bucket } : {}), ...(VAULT_TAG.test(tag) ? { tag } : {}) };
}
function vaultRouteFromHash(fallback) {
  const [path, search] = location.hash.replace(/^#\/?/, '').split('?');
  const [view, arg] = path.split('/').map((p) => decodeURIComponent(p || ''));
  if (view === 'sets' && arg) return { view: 'setdetail', code: arg, ...vaultScopeFromSearch(search) };
  if (view === 'decks' && arg) return { view: 'decks', deckId: arg };
  if (view === 'help') return { view: 'help', section: arg || '' };
  const known = VAULT_VIEWS.includes(view) ? view : fallback;
  return { view: known, ...(VAULT_SCOPED.has(known) ? vaultScopeFromSearch(search) : {}) };
}
function vaultHashFor(route) {
  let hash;
  if (route.view === 'setdetail') hash = `#/sets/${encodeURIComponent(route.code)}`;
  else if (route.view === 'decks' && route.deckId) return `#/decks/${encodeURIComponent(route.deckId)}`;
  else if (route.view === 'help') return helpHashFor(route.section);
  else hash = `#/${route.view}`;
  if (!VAULT_SCOPED.has(route.view)) return hash;
  const parts = [route.bucket ? `bucket=${encodeURIComponent(route.bucket)}` : '', route.tag && VAULT_TAG.test(route.tag) ? `tag=${route.tag}` : ''];
  const query = parts.filter(Boolean).join('&');
  return query ? `${hash}?${query}` : hash;
}
const vaultUrlFor = (route) => location.pathname + location.search + vaultHashFor(route);

// useTweaks, TweaksPanel, TweakSection, … are globals from tweaks-panel.jsx (same bundle).

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
  const [route, setRouteState] = useStateApp(() => vaultRouteFromHash(t.landing || 'dashboard'));
  const [drawerCard, setDrawerCard] = useStateApp(null);
  const [loadProgress, setLoadProgress] = useStateApp('Reading vault…');
  const [loadError, setLoadError] = useStateApp(null); // shown instead of a spinner that never ends
  const [refreshing, setRefreshing] = useStateApp(false);
  const [refreshProgress, setRefreshProgress] = useStateApp(null);
  const [refreshError, setRefreshError] = useStateApp(null);
  const [auth, setAuth] = useStateApp('checking'); // 'checking' | 'signed-out' | 'signed-in'
  const [me, setMe] = useStateApp(null);
  const [notice, setNotice] = useStateApp(VAULT_START_NOTICE);
  const [accountOpen, setAccountOpen] = useStateApp(false);
  const [welcome, setWelcome] = useStateApp(() => !welcomeSeen());
  const [viewing, setViewing] = useStateApp(null); // { id, from } while looking at someone's shared collection
  const [deckText, setDeckText] = useStateApp(null); // deck opened from saved/shared decks
  const refreshingRef = useRefApp(false);      // a server refresh is running (manual or automatic)
  const autoRefreshTried = useRefApp(false);   // the automatic one runs once per visit
  const refreshAgain = useRefApp(null);        // asked for while one was running: true (auto) or 'manual'
  const viewingRef = useRefApp(null);
  viewingRef.current = viewing;
  // The scope (#130) is the route's while on a view that follows it, else the last one chosen (kept for the way back). A shared
  // collection has none: buckets and tags are the owner's own.
  const [keptScope, setKeptScope] = useStateApp(() => (VAULT_SCOPED.has(route.view) ? vaultScopeOf(route) : {}));
  const scope = viewing ? {} : VAULT_SCOPED.has(route.view) ? vaultScopeOf(route) : keptScope;
  const scopeRef = useRefApp(scope);
  scopeRef.current = scope;
  useEffectApp(() => {
    if (VAULT_SCOPED.has(route.view)) setKeptScope((k) => {
      const next = vaultScopeOf(route);
      return k.bucket === next.bucket && k.tag === next.tag ? k : next;
    });
  }, [route]);
  const scoped = window.useScoped(data, scope, !viewing && VAULT_ANALYTICS.has(route.view));

  // Go to a view: a new history entry, unless an open card or panel's entry can be reused. The views that follow the scope
  // get the current one unless the caller names its own.
  const setRoute = (raw) => {
    const next = VAULT_SCOPED.has(raw.view) && !viewingRef.current ? { ...scopeRef.current, ...raw } : raw;
    setRouteState(next);
    setDrawerCard(null);
    setAccountOpen(false);
    const entry = { route: next };
    if (history.state && history.state.overlay) history.replaceState(entry, '', vaultUrlFor(next));
    else if (location.hash !== vaultHashFor(next) || next.initialQuery) history.pushState(entry, '', vaultUrlFor(next));
    window.scrollTo(0, 0);
  };
  // A card or the account panel: its own history entry, so Back closes it.
  const openOverlay = () => {
    if (!(history.state && history.state.overlay)) history.pushState({ route, overlay: true }, '', vaultUrlFor(route));
  };
  const closeOverlay = (close) => {
    if (history.state && history.state.overlay) history.back(); // popstate closes it
    else close();
  };
  // The scope lives in the address (#/browse?bucket=3, #/dashboard?bucket=3&tag=trade) without a history entry of its own, like
  // Browse's other filters. Choosing one on a view that follows it changes it for all of them; {} clears it.
  const setScope = (chosen) => {
    const picked = vaultScopeOf(chosen);
    scopeRef.current = picked;
    setKeptScope(picked);
    const { bucket, tag, ...rest } = route;
    const next = VAULT_SCOPED.has(route.view) ? { ...rest, ...picked } : route;
    setRouteState(next);
    history.replaceState({ route: next }, '', vaultUrlFor(next));
  };
  const setBucket = (bucket) => setScope({ ...scope, bucket });
  const setTag = (tag) => setScope({ ...scope, tag });
  // After copies moved between buckets: the collection's version changed, so ask again (without blanking the page).
  const reloadCollection = async () => {
    const j = await window.VaultApi.collection();
    setData(j);
  };
  const openCard = (c) => { setDrawerCard(c); openOverlay(); };
  const openAccount = () => { setAccountOpen(true); openOverlay(); };
  useEffectApp(() => {
    history.replaceState({ route }, '', vaultUrlFor(route));
    const onPop = (e) => {
      const s = e.state || {};
      if (!s.overlay) { setDrawerCard(null); setAccountOpen(false); }
      setRouteState(s.route || vaultRouteFromHash(t.landing || 'dashboard'));
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

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

  // Load the signed-in user's collection summary from the server; each view asks the server for
  // what it shows. Prices are refreshed there daily, and on demand (runRefresh).
  const loadCollection = async ({ afterImport = false } = {}) => {
    try {
      setLoadError(null);
      setLoadProgress('Opening your vault…');
      const j = await window.VaultApi.collection();
      if (j.meta.offline) {
        setNotice(`You're offline: showing your collection as saved on this device ${new Date(j.meta.savedAt).toLocaleString()}.`);
      } else {
        setMe(await window.VaultApi.me());
      }
      setAuth('signed-in');
      setViewing(null);
      acceptPendingInvite();
      setData(j);
      if (!j.meta.offline) enrichInBackground(j, afterImport);
    } catch (e) {
      if (e.status === 401) setAuth('signed-out');
      else setLoadError(e.message || 'Unknown error');
    }
  };
  useEffectApp(() => { loadCollection(); }, []);
  // A 401 while signed in means the session ended: back to the sign-in screen, which says why.
  const authRef = useRefApp(auth); authRef.current = auth;
  useEffectApp(() => {
    const ended = () => {
      if (authRef.current !== 'signed-in') return;
      try { sessionStorage.setItem(window.SESSION_ENDED_KEY, '1'); } catch {}
      window.VaultApi.logout().catch(() => {});
      setAuth('signed-out');
    };
    window.addEventListener('vault:unauthorized', ended);
    return () => window.removeEventListener('vault:unauthorized', ended);
  }, []);

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
        viewingRef.current = { id: s.id, from: s.from };  // before the route, so the address carries no bucket or tag
        setViewing({ id: s.id, from: s.from });
        setRoute({ view: 'dashboard' });
      } else {
        const d = await window.VaultApi.sharedDeck(s.id);
        // The shared copy, still credited to its source and author (Archidekt's attribution terms).
        setDeckText({ shareId: s.id, text: d.text, credit: { name: d.name, url: d.source_url || '', author: d.source_author || '' } });
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
  // A deck from Account: your own saved deck opens at its address (#/decks/{id}); a shared one by its list.
  const openDeck = (deck) => {
    setAccountOpen(false);
    if (deck && typeof deck === 'object') { setDeckText(null); setRoute({ view: 'decks', deckId: String(deck.id) }); return; }
    setDeckText(deck);
    setRoute({ view: 'decks' });
  };
  const noticeBanner = notice && (
    <div className="panel panel-tight" style={{ margin: '12px 24px 0', display: 'flex', justifyContent: 'space-between' }}>
      <span className="label-mono">{notice}</span>
      <button className="btn xs ghost close-x" onClick={() => setNotice(null)} aria-label="Dismiss message" title="Dismiss"><window.CloseIcon /></button>
    </div>
  );
  const accountPanel = accountOpen && (
    <AccountPanel
      me={me}
      onClose={() => closeOverlay(() => setAccountOpen(false))}
      onOpenShared={openShared}
      onOpenDeck={openDeck}
      onMeChanged={() => window.VaultApi.me().then(setMe)}
      onCollectionChanged={() => { setData(null); loadCollection({ afterImport: true }); }}
    />
  );

  const onImported = (res) => {
    const merge = res.merge || {};
    const kept = (merge.kept_vault_edits && merge.kept_vault_edits.count) || 0;
    const both = (merge.conflicts && merge.conflicts.count) || 0;
    const edits = kept + both ? ` Kept ${kept + both} card${kept + both === 1 ? '' : 's'} you changed through an assistant`
      + (both ? ` (${both} also changed in your app: your edit here was kept)` : '') + '.' : '';
    setNotice(`Imported ${res.copies.toLocaleString()} cards: ${window.describeChanges(res.changes)}.${edits}`);
    setData(null);
    loadCollection({ afterImport: true });
  };

  // Refresh your collection's card data and today's prices on the server (POST
  // /collection/refresh, a chunk per call, until nothing remains), then reload the summary: its
  // new version makes every view ask the server again. The browser never calls Scryfall and
  // keeps nothing about the refresh. `auto`: started by the app, so it stays quiet on errors.
  const runRefresh = async (auto) => {
    if (refreshingRef.current) { refreshAgain.current = refreshAgain.current || auto || 'manual'; return; }
    refreshingRef.current = true;
    setRefreshError(null);
    setRefreshing(true);
    setRefreshProgress({ done: 0, total: 0, auto });
    try {
      await window.VaultApi.refreshCollection({ onProgress: (p) => setRefreshProgress({ ...p, auto }) });
    } catch (e) {
      if (!auto) {
        setRefreshError(
          e.status === 503 ? "The Vault couldn't reach Scryfall just now, so values still show the last good prices. Try again in a few minutes."
            : e.status === 429 ? 'Prices were just updated several times in a row. Try again in a minute.'
            : 'Update failed: ' + e.message
        );
      }
    } finally {
      // What was refreshed before an error is saved on the server too: show it.
      if (!viewingRef.current) {
        try { setData(await window.VaultApi.collection()); } catch {}
      }
      refreshingRef.current = false;
      setRefreshing(false);
      setRefreshProgress(null);
      // asked for again while running (e.g. an import finished): once more, for what is new
      const again = refreshAgain.current;
      refreshAgain.current = null;
      if (again) runRefresh(again === true);
    }
  };

  // Server-side enrichment nobody has to ask for: after an import, and once per visit when the
  // prices are older than today or some printings have no card data yet. Runs in the background
  // with a quiet progress note; a failure isn't retried until the next visit.
  const enrichInBackground = async (j, afterImport) => {
    if (!j.meta.totalQty || (!afterImport && autoRefreshTried.current)) return;
    autoRefreshTried.current = true;
    if (!afterImport) {
      const today = new Date().toISOString().slice(0, 10);  // the server's day (UTC)
      let stale = !j.meta.pricesAsOf || j.meta.pricesAsOf < today;
      if (!stale) {
        const b = await j.api.breakdowns().catch(() => null);
        stale = !!b && b.colors.some((x) => x.key === 'unknown' && x.copies > 0);
      }
      if (!stale) return;
    }
    runRefresh(true);
  };

  // The server syncs Scryfall's daily bulk file for everyone; this just reloads its latest values.
  const doBulkSync = async () => {
    if (refreshing || !data) return;
    setRefreshError(null);
    setRefreshing(true);
    try {
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
  const nav = (view, extra = {}) => setRoute({ view, ...extra });
  const dismissWelcome = () => { welcomeDone(); setWelcome(false); };
  const welcomeBanner = welcome && !viewing && data && <Welcome hasCollection={data.meta.totalQty > 0} onDismiss={dismissWelcome} />;
  const viewingBanner = viewing && data && (
    <div className="panel panel-tight" style={{ margin: '12px 24px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderColor: 'var(--gold)' }}>
      <span className="label-mono">
        Viewing {viewing.from}'s collection · read-only{data.meta.costsHidden ? ' · prices paid are private' : ''}
      </span>
      <button className="btn xs" onClick={backToMine}>Back to my vault</button>
    </div>
  );
  let body;
  if (data && data.meta.totalQty === 0) {
    body = (
      <div className="app">
        <header className="topbar">
          <button className="brand" onClick={() => setRoute({ view: 'dashboard' })} aria-label="The Vault: home"><span className="mark"><span>V</span></span><span className="title">The Vault</span></button>
          <AccountMenu me={me} onImported={onImported} onAccount={openAccount} readOnly={!!viewing} />
        </header>
        {viewing && viewingBanner}
        {noticeBanner}
        {welcomeBanner}
        <main>
          {route.view === 'help' ? <Help section={route.section} /> : viewing ? (
            // Someone else's collection, shared but empty: nothing to import here.
            <><div className="help-row"><HelpHint view="dashboard" shared /></div><div style={{ display: 'grid', placeItems: 'center', minHeight: '60vh' }}>
              <div className="panel" style={{ width: 'min(460px, 100%)', textAlign: 'center' }}>
                <p className="eyebrow">Shared collection</p>
                <h1 className="h1" style={{ margin: '8px 0 12px' }}>{viewing.from}'s collection is empty</h1>
                <p className="label-mono">
                  There are no cards in it yet. When {viewing.from} imports a collection, it shows up here.
                </p>
              </div>
            </div></>
          ) : <><div className="help-row"><HelpHint view="dashboard" /></div><EmptyVault onImported={onImported} /></>}
        </main>
        <VaultFooter />
      </div>
    );
  } else if (!data) {
    body = (
      <main style={{ display: 'grid', placeItems: 'center', minHeight: '100vh' }}>
        <div style={{ textAlign: 'center', maxWidth: 560, padding: 16 }}>
          <div style={{ fontFamily: 'var(--display)', fontSize: 36, color: 'var(--gold)', marginBottom: 16 }}>◇</div>
          {loadError ? (
            <div role="alert">
              <h1 className="h1" style={{ fontSize: 24, margin: '0 0 8px' }}>Your vault couldn't be opened</h1>
              <p className="label-mono" style={{ marginBottom: 16 }}>{loadError}</p>
              <button className="btn" onClick={() => loadCollection()}>Try again</button>
            </div>
          ) : (
            <>
              <p className="label-mono" aria-live="polite">{loadProgress}</p>
              <div className="spinner" style={{ margin: '16px auto 0', width: 18, height: 18 }}></div>
            </>
          )}
        </div>
      </main>
    );
  } else {
    body = (
      <div className="app with-tabs">
        <header className="topbar">
          <button className="brand" onClick={() => nav('dashboard')} aria-label="The Vault: home">
            <span className="mark"><span>V</span></span>
            <span className="title">The Vault</span>
            <span className="subtitle">MTG Collection</span>
          </button>
          <nav className="nav" aria-label="Sections">
            <button aria-current={route.view === 'dashboard' ? 'page' : undefined} className={route.view === 'dashboard' ? 'active' : ''} onClick={() => nav('dashboard')}>Vault</button>
            <button aria-current={route.view === 'browse' ? 'page' : undefined} className={route.view === 'browse' ? 'active' : ''} onClick={() => nav('browse')}>Browse</button>
            <button aria-current={route.view === 'sets' || route.view === 'setdetail' ? 'page' : undefined} className={route.view === 'sets' || route.view === 'setdetail' ? 'active' : ''} onClick={() => nav('sets')}>Sets</button>
            <button aria-current={route.view === 'decks' ? 'page' : undefined} className={route.view === 'decks' ? 'active' : ''} onClick={() => { setDeckText(null); nav('decks'); }}>Decks</button>
            <button aria-current={route.view === 'lab' ? 'page' : undefined} className={route.view === 'lab' ? 'active' : ''} onClick={() => nav('lab')}>Lab</button>
            <button aria-current={route.view === 'graph' ? 'page' : undefined} className={route.view === 'graph' ? 'active' : ''} onClick={() => nav('graph')}>Graph</button>
          </nav>
          {refreshing && refreshProgress && refreshProgress.auto && (
            // the automatic refresh after an import or on a new day: a quiet note, no prompt
            <span className="label-mono auto-refresh" role="status" style={{ fontSize: 10, whiteSpace: 'nowrap' }}>
              Updating prices{refreshProgress.total ? ` ${Math.round((refreshProgress.done / refreshProgress.total) * 100)}%` : '…'}
            </span>
          )}
          <AccountMenu me={me} onImported={onImported} onAccount={openAccount} readOnly={!!viewing} />
        </header>
        {viewing && viewingBanner}
        {noticeBanner}
        {welcomeBanner}

        <main>
          {route.view !== 'help' && <div className="help-row"><HelpHint view={route.view} shared={!!viewing} /></div>}
          {route.view === 'help' && <Help section={route.section} />}
          {(route.view === 'lab' || route.view === 'graph') && window.scopeActive(scope) && (
            <p className="muted scope-aside" role="status">This page reads your whole inventory. Your bucket or tag choice is kept for the Vault, Browse, Sets and Value pages.</p>
          )}
          {route.view === 'dashboard' && (
            <ScopeFrame inventory={data} scoped={scoped} scope={scope} onScope={setScope} viewing={!!viewing}>
              {(d) => (
                <Dashboard
                  data={d}
                  gotoBrowse={(q) => nav('browse', { initialQuery: q })}
                  gotoSets={() => nav('sets')}
                  gotoSet={code => nav('setdetail', { code })}
                  gotoValuation={() => nav('valuation')}
                  onRefresh={viewing ? null : () => runRefresh(false)}
                  onBulkSync={doBulkSync}
                  refreshing={refreshing}
                  refreshProgress={refreshProgress}
                  refreshError={refreshError}
                  openCard={openCard}
                />
              )}
            </ScopeFrame>
          )}
          {route.view === 'browse' && (
            <Browse data={data} openCard={openCard} initialQuery={route.initialQuery} bucket={route.bucket} onBucket={setBucket}
                    tag={route.tag} onTag={setTag}
                    onChanged={reloadCollection} readOnly={!!viewing} />
          )}
          {route.view === 'sets' && (
            <ScopeFrame inventory={data} scoped={scoped} scope={scope} onScope={setScope} viewing={!!viewing}>
              {(d) => <Sets data={d} onSetClick={code => nav('setdetail', { code })} />}
            </ScopeFrame>
          )}
          {route.view === 'setdetail' && (
            <ScopeFrame inventory={data} scoped={scoped} scope={scope} onScope={setScope} viewing={!!viewing}>
              {(d) => <SetDetail data={d} code={route.code} onBack={() => nav('sets')} openCard={openCard} />}
            </ScopeFrame>
          )}
          {route.view === 'decks' && (
            <DeckView key={deckText && deckText.shareId ? 'share' + deckText.shareId : deckText || 'deck'} data={data} openCard={openCard} initialText={deckText}
              deckId={route.deckId} onOpenDeckId={(id) => { setDeckText(null); nav('decks', id ? { deckId: String(id) } : {}); }} />
          )}
          {route.view === 'lab' && (
            <Lab data={data} openCard={openCard} />
          )}
          {route.view === 'graph' && (
            <GraphView data={data} openCard={openCard} />
          )}
          {route.view === 'valuation' && (
            <ScopeFrame inventory={data} scoped={scoped} scope={scope} onScope={setScope} viewing={!!viewing}>
              {(d) => (
                <Valuation
                  data={d}
                  onBack={() => nav('dashboard')}
                  onRefresh={viewing ? null : () => runRefresh(false)}
                  onBulkSync={doBulkSync}
                  refreshing={refreshing}
                  refreshProgress={refreshProgress}
                  refreshError={refreshError}
                  openCard={openCard}
                />
              )}
            </ScopeFrame>
          )}
        </main>

        <VaultFooter />
        {drawerCard && <CardDrawer card={drawerCard} costsHidden={!!data?.meta?.costsHidden} canMove={!viewing && !!data} canTag={!viewing && !!data}
                                   version={data?.meta?.version} onMoved={reloadCollection}
                                   onClose={() => closeOverlay(() => setDrawerCard(null))} />}

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
        </TweaksPanel>
      </div>
    );
  }
  return <>{body}{accountPanel}</>;
}

function CardDrawer({ card, onClose, costsHidden, canMove, canTag, version, onMoved }) {
  const { buckets } = window.useBuckets(version, !!canMove);
  const { tags } = window.useTags(version, !!canTag);
  const [scry, setScry] = useStateApp(() => card._scry || card.scry || window.Scryfall.cached(card.n, card.s, card.cn));
  useEffectApp(() => {
    if (scry) return;
    let dead = false;
    window.Scryfall.collection([{ name: card.n, set: card.s, collector_number: card.cn }]).then(arr => {
      if (!dead && arr[0]) setScry(arr[0]);
    });
    return () => { dead = true; };
  }, [card]);

  // the server's value for a printing you own; a deck line's card shows its unit price × copies owned
  const total = card.v != null ? card.v : (card.mk || 0) * (card.q || 0);

  useEffectApp(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const panel = useRefApp(null);
  window.useDialogFocus(panel);  // focus into the panel, Tab stays inside, focus back to the row when it closes

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose}></div>
      <div className="drawer" role="dialog" aria-modal="true" aria-label={card.n} ref={panel} tabIndex={-1} style={{ outline: 'none' }}>
        {/* Blurred set icon as ambient background */}
        {card.s && window.SetIcons?.get(card.s)?.icon && (
          <div className="drawer-bg">
            <img src={window.SetIcons.get(card.s).icon} alt="" style={{ filter: 'brightness(0) invert(1)' }} />
          </div>
        )}
        {/* Its own sticky row, so the close button never sits under the card header and stays in reach
            while the panel scrolls. The whole 44px square is the button; Esc and a click outside also close. */}
        <div className="drawer-bar">
          <button className="close" data-autofocus onClick={onClose} aria-label="Close card details" title="Close (Esc)">
            <window.CloseIcon />
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 20, marginBottom: 24 }}>
          <div style={{ aspectRatio: '488 / 680', background: 'var(--bg-2)', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }}>
            {scry?.img_normal ? (
              <img src={scry.img_normal} alt={card.n + (scry.artist ? ', illustrated by ' + scry.artist : '')}
                style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            ) : (
              <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center', color: 'var(--muted)', padding: 12, textAlign: 'center', fontFamily: 'var(--mono)', fontSize: 11, background: 'repeating-linear-gradient(135deg, var(--surface-2) 0 8px, var(--surface) 8px 16px)' }}>
                {card.n}
              </div>
            )}
          </div>
          <div>
            {scry?.img_normal && (
              <p className="label-mono" style={{ fontSize: 10, marginBottom: 8 }}>
                {scry.artist ? <>Illustrated by <strong>{scry.artist}</strong> · </> : null}
                image via <a href={scry.scryfall_uri || 'https://scryfall.com'} target="_blank" rel="noopener noreferrer">Scryfall</a>
                {' · '}© Wizards of the Coast
              </p>
            )}
            <h2 className="h2" style={{ fontSize: 26, marginBottom: 6 }}>{card.n}</h2>
            {scry?.mana_cost && <p style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--muted)' }}>{scry.mana_cost}</p>}
            {scry?.type_line && <p style={{ fontSize: 13, marginTop: 8 }}>{scry.type_line}</p>}
            {scry?.color_identity && <div style={{ marginTop: 8 }}><ColorIdentity colors={scry.color_identity} /></div>}
            <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontFamily: 'var(--mono)', fontSize: 11 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span className="muted">Set</span>
                {window.SetIcon ? <SetIcon code={card.s} size={16} fallback={false} /> : null}
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
            <div className="m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
              <Stat label="Quantity" value={card.q} />
              <Stat label="Total value" value={`$${total.toFixed(2)}`} color="var(--gold)" />
              <Stat label="Spent" value={window.vaultSpentText(card, costsHidden)} muted />
              {costsHidden || card.gain == null ? <Stat label="P&L" value={window.vaultGainText(card, costsHidden)} muted /> : (
                <Stat label="P&L" value={window.vaultGainText(card, costsHidden)} color={card.gain >= 0 ? 'var(--good)' : 'var(--danger)'} />
              )}
            </div>
            {card.fd && (
              <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 12 }}>
                first bought {card.fd}{card.fd !== card.ld ? ` · last bought ${card.ld}` : ''}
              </p>
            )}
          </div>
        )}

        {canMove && card.q > 0 && <window.BucketMover card={card} buckets={buckets} onMoved={onMoved} />}

        {canTag && card.href && card.key && <window.CardTags card={card} tags={tags} onChanged={onMoved} />}
        {canTag && card.href && card.key && <window.CardMetadata card={card} />}

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
                      {window.SetIcon && <SetIcon code={e.s} size={14} fallback={false} />}
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
            <p className="eyebrow" style={{ marginBottom: 4 }}>Live Scryfall prices</p>
            <p className="label-mono" style={{ fontSize: 10, marginBottom: 12 }}>
              Scryfall sources USD prices from <a href="https://www.tcgplayer.com" target="_blank" rel="noopener noreferrer">TCGplayer</a>{' '}
              and EUR prices from <a href="https://www.cardmarket.com" target="_blank" rel="noopener noreferrer">Cardmarket</a>, updated about daily.
            </p>
            <div className="m-2col" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
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
      title="Ask the Vault to fetch current prices and card data from Scryfall"
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