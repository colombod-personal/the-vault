// Decks — your saved decks, and a page per deck. The library lists each deck with how much of it
// you own and what finishing it costs (GET /decks/{id}, coverage against your collection). A deck's
// page has tabs: Cards (what you own of each, grouped by type), Stats, Legality, Upgrades (marking
// the ones you already own), Combos and a Buy list, all computed by the server (/decks/*).
const { useState: useStateD, useMemo: useMemoD, useEffect: useEffectD, useRef: useRefD } = React;

// "just now", "3 minutes ago": how old the copy of a deck read from its site is (the Vault keeps one for ten minutes).
const ageText = (seconds) => {
  if (seconds == null) return '';
  if (seconds < 60) return 'just now';
  const m = Math.round(seconds / 60);
  return m < 60 ? `${m} minute${m === 1 ? '' : 's'} ago` : `${Math.round(m / 60)} hour${Math.round(m / 60) === 1 ? '' : 's'} ago`;
};

// Only the deck's actual Commander category, not user categories like "Commander Synergy".
const isCommander = (c) => c.section === 'commander' || (c.categories || []).some((x) => String(x).trim().toLowerCase() === 'commander');

// A deck's cards as a decklist the server parses ("4 Name (SET) 123" per line); commanders go under
// a "Commander" header so stats, legality and combos know them.
function deckListText(cards) {
  const line = (c) => `${c.qty} ${c.name}` + (c.set && c.collector_number ? ` (${c.set.toUpperCase()}) ${c.collector_number}` : '');
  const cmd = cards.filter(isCommander), rest = cards.filter((c) => !isCommander(c));
  return (cmd.length ? 'Commander\n' + cmd.map(line).join('\n') + '\n\nDeck\n' : '') + rest.map(line).join('\n');
}

// The server's coverage line for each deck card, by name (front face, case-folded).
const nameKey = (n) => n.split(' // ')[0].trim().toLowerCase();
function coverageFor(cards, lines) {
  const byName = {};
  for (const l of lines) byName[nameKey(l.name)] = l;
  return cards.map((c) => byName[nameKey(c.name)] || null);
}

// A card listed more than once (two printings of a land, say) as one line: the server counts what
// you own by name. When the rows name different printings, the line names none, so it's priced by
// name rather than as the first row's printing.
function mergeDeckCards(cards) {
  const merged = [], at = {};
  for (const c of cards) {
    const k = nameKey(c.name);
    if (!(k in at)) { at[k] = merged.length; merged.push({ ...c }); continue; }
    const m = merged[at[k]];
    const same = (m.set || '') === (c.set || '') && String(m.collector_number || '') === String(c.collector_number || '');
    merged[at[k]] = { ...m, qty: m.qty + c.qty, ...(same ? {} : { set: '', collector_number: '' }),
      categories: [...new Set([...(m.categories || []), ...(c.categories || [])])] };
  }
  return merged;
}

const money = (v) => (v == null ? '?' : '$' + Number(v).toFixed(2));
const scryfallLink = (name) => 'https://scryfall.com/search?q=' + encodeURIComponent(`!"${name}"`);
// Every format the server's analysis supports (vault/deck_tools.py FORMATS).
const FORMAT_CHOICES = ['commander', 'standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'brawl', 'standardbrawl',
  'historic', 'timeless', 'oathbreaker', 'paupercommander', 'premodern', 'penny', 'duel', 'predh', 'oldschool', 'gladiator', 'alchemy'];
const TYPE_ORDER = ['Commander', 'Creature', 'Planeswalker', 'Battle', 'Instant', 'Sorcery', 'Artifact', 'Enchantment', 'Land', 'Other'];
const primaryType = (r) => {
  if (isCommander(r)) return 'Commander';
  const t = r.scry?.type_line || '';
  return TYPE_ORDER.find((k) => k !== 'Commander' && k !== 'Other' && t.includes(k)) || 'Other';
};

// A saved deck has its own address (#/decks/{id}), so refresh, bookmarks and Back work; a deck
// opened from a link or a pasted list lives on screen until it's saved.
function DeckView({ data, openCard, initialText, deckId, onOpenDeckId, swap }) {
  const [myDecks, setMyDecks] = useStateD(null); // your saved decks, with summaries (null while loading, 'error' if that failed)
  // A pasted list, or a shared deck ({ text, credit: { name, url, author } }) shown with its source's credit.
  const [local, setLocalRaw] = useStateD(initialText ? (typeof initialText === 'string' ? { text: initialText, key: 1 }
    : { text: initialText.text, credit: initialText.credit, key: 1 }) : null);
  const setLocal = (o) => setLocalRaw(o && { ...o, key: Date.now() });
  const refreshDecks = () => { setMyDecks((d) => (d === 'error' ? null : d));
    return window.VaultApi.decks(true).then((d) => { setMyDecks(d); return d; }).catch(() => { setMyDecks('error'); return 'error'; }); };
  // The deck at #/decks/{id}: fetched on its own (the library's summaries are only loaded for the library).
  const [routed, setRouted] = useStateD(undefined); // undefined while loading, null when it isn't yours, { error } when it couldn't load
  const [tries, setTries] = useStateD(0);
  useEffectD(() => {
    if (!deckId) { refreshDecks(); return; }
    let stop = false;
    setRouted(undefined);
    window.VaultApi.deck(deckId).then((d) => !stop && setRouted(d)).catch((e) => {
      if (stop) return;
      if (e.status === 404) { setRouted(null); refreshDecks(); } else setRouted({ error: e.message }); // only a 404 means it's gone
    });
    return () => { stop = true; };
  }, [deckId, tries]);

  if (deckId) {
    if (routed === undefined) return <p className="muted label-mono">Loading your deck…</p>;
    if (routed && routed.error) return (
      <div className="panel" style={{ padding: 24, textAlign: 'center' }}>
        <p style={{ marginBottom: 10, color: 'var(--danger)' }}>Couldn't load this deck ({routed.error}).</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
          <button className="btn sm" onClick={() => setTries((n) => n + 1)}>Try again</button>
          <button className="btn sm ghost" onClick={() => onOpenDeckId(null)}>← Your decks</button>
        </div>
      </div>
    );
    if (routed === null) return <DeckLibrary myDecks={myDecks} onRetry={refreshDecks} onOpen={(o) => { if (o.saved) onOpenDeckId(o.saved.id); else { setLocal(o); onOpenDeckId(null); } }} notice="That deck isn't in your decks any more." />;
    return <DeckPage key={'saved' + routed.id} source={{ saved: routed, url: routed.source_url || null, text: routed.source_url ? null : routed.text }}
      myDecks={myDecks} refreshDecks={refreshDecks} openCard={openCard} onBack={() => onOpenDeckId(null)}
      onSaved={(d) => setRouted((r) => ({ ...r, ...d }))} swap={swap} />;
  }
  if (local) {
    return <DeckPage key={local.key} source={local} myDecks={myDecks} refreshDecks={refreshDecks} openCard={openCard}
      onBack={() => { setLocal(null); refreshDecks(); }}
      onSaved={(d) => refreshDecks().then(() => { setLocal(null); onOpenDeckId(d.id); })} />;
  }
  return <DeckLibrary myDecks={myDecks} onRetry={refreshDecks} onOpen={(o) => o.saved ? onOpenDeckId(o.saved.id) : setLocal(o)} />;
}

// -- the library ----------------------------------------------------------------------------------

function DeckLibrary({ myDecks, onOpen, onRetry, notice }) {
  const list = Array.isArray(myDecks) ? myDecks : [];
  const [tab, setTab] = useStateD('url');
  const [src, setSrc] = useStateD('');
  // Each deck against your collection, from the list's summaries (one request for the library).
  const covers = useMemoD(() => Object.fromEntries(list.map((d) => [d.id, d.summary ? {
    need: d.summary.need, have: d.summary.have, missingCards: d.summary.missing, pct: d.summary.need ? d.summary.have / d.summary.need : 0,
    cost: d.summary.missing_cost || 0, unpriced: d.summary.missing_unpriced || 0 } : 'error'])), [myDecks]);

  const sorted = useMemoD(() => list.slice().sort((a, b) => {
    const ca = covers[a.id], cb = covers[b.id];
    const pa = ca && ca !== 'error' ? ca.pct : -1, pb = cb && cb !== 'error' ? cb.pct : -1;
    return pb - pa || a.name.localeCompare(b.name);
  }), [myDecks, covers]);

  const add = () => {
    const v = src.trim();
    if (!v) return;
    onOpen(tab === 'url' ? { url: v } : { text: v });
  };

  return (
    <div data-screen-label="04 Decks">
      <div className="page-head" style={{ marginBottom: 20 }}>
        <div>
          <p className="eyebrow">Decks</p>
          <h1 className="h1" style={{ marginTop: 6 }}>Your decks.</h1>
          <p className="muted" style={{ fontSize: 13, marginTop: 8, maxWidth: 560, lineHeight: 1.5 }}>
            Every deck you save, checked against your collection: how much of it you own, and what it costs to finish.
            Open one for its cards, stats, legality, upgrades, combos and a buy list.
          </p>
        </div>
      </div>

      {notice && <div className="panel" style={{ marginBottom: 16, color: 'var(--gold)' }}>{notice}</div>}
      <div className="panel" style={{ marginBottom: 20 }}>
        <p className="eyebrow" style={{ marginBottom: 10 }}>Add a deck</p>
        <div className="row" style={{ gap: 4, marginBottom: 10 }}>
          <button className={`chip ${tab === 'url' ? 'active' : ''}`} onClick={() => setTab('url')}>From a link</button>
          <button className={`chip ${tab === 'text' ? 'active' : ''}`} onClick={() => setTab('text')}>Paste a list</button>
        </div>
        {tab === 'url' ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input className="input" style={{ flex: '1 1 240px' }} placeholder="Paste an Archidekt link" value={src}
              onChange={(e) => setSrc(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
            <button className="btn primary" onClick={add} disabled={!src.trim()}>Open deck</button>
          </div>
        ) : (
          <>
            <textarea className="input" rows="8" value={src} onChange={(e) => setSrc(e.target.value)}
              placeholder={'Commander\n1 Atraxa, Praetors\' Voice\n\nDeck\n1 Sol Ring\n4 Lightning Bolt\n…'} />
            <button className="btn primary" style={{ marginTop: 8 }} onClick={add} disabled={!src.trim()}>Open list</button>
          </>
        )}
        <p className="muted" style={{ fontSize: 11, marginTop: 8, fontFamily: 'var(--mono)', lineHeight: 1.5 }}>
          Archidekt decks are fetched by the Vault server, one deck each time you ask. Archidekt's terms do not allow listing
          your decks automatically, so you paste each link (<a href="https://github.com/colombod-personal/the-vault/blob/main/docs/compliance.md#archidekt" target="_blank" rel="noopener noreferrer">why</a>).
          Private decks cannot be read: make the deck public or unlisted on Archidekt. The Vault never asks for your Archidekt login.
          For Moxfield, export the list as text and paste it.
        </p>
      </div>

      {myDecks === null ? <p className="muted label-mono">Loading your decks…</p> :
       myDecks === 'error' ? (
         <div className="panel" style={{ padding: 24, textAlign: 'center' }}>
           <p style={{ marginBottom: 10, color: 'var(--danger)' }}>Couldn't load your decks.</p>
           <button className="btn sm" onClick={onRetry}>Try again</button>
         </div>
       ) :
       myDecks.length === 0 ? (
         <div className="panel" style={{ padding: 32, textAlign: 'center' }}>
           <p className="h-display" style={{ fontSize: 20, marginBottom: 8 }}>No decks yet.</p>
           <p className="muted" style={{ fontSize: 13, lineHeight: 1.6 }}>Add one above, then press “Save to your decks” on its page.</p>
         </div>
       ) : (
         <div className="deck-library">
           {sorted.map((d) => {
             const c = covers[d.id];
             return (
               <button key={d.id} className="panel deck-tile" onClick={() => onOpen({ saved: d, url: d.source_url || null, text: d.source_url ? null : d.text })}>
                 <div style={{ fontWeight: 600, fontSize: 15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</div>
                 <div className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 2 }}>
                   {d.source_url ? d.source_url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/decks\//, ' · ') : 'pasted list'}
                 </div>
                 <div className="deck-tile-what" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.4 }}>
                   <strong>{d.overview && d.overview.format ? d.overview.format : 'format unknown'}</strong>
                   {d.overview && d.overview.commanders && d.overview.commanders.length > 0
                     ? <span> · {d.overview.commanders.join(' + ')}</span>
                     : <span className="muted"> · {d.source_url ? 'no commander recorded: open the deck and press Refresh' : 'no commander'}</span>}
                 </div>
                 {!c ? <p className="muted label-mono" style={{ marginTop: 12 }}>Checking your collection…</p> :
                  c === 'error' ? <p className="muted label-mono" style={{ marginTop: 12 }}>Couldn't check this deck</p> : (
                   <>
                     <div className="progress-bar" style={{ marginTop: 12 }}><div style={{ width: `${c.pct * 100}%`, background: 'var(--good)' }}></div></div>
                     <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8, fontSize: 12 }}>
                       <span><strong>{Math.round(c.pct * 100)}%</strong> <span className="muted">owned · {c.have}/{c.need}</span></span>
                       <span style={{ color: c.missingCards ? 'var(--gold)' : 'var(--good)' }}>
                         {c.missingCards ? `${c.missingCards} missing · ` + (c.cost ? `${c.unpriced ? '≥ ' : ''}${money(c.cost)}` : c.unpriced ? 'no price' : money(0)) : 'complete'}
                       </span>
                     </div>
                   </>
                 )}
               </button>
             );
           })}
         </div>
       )}
    </div>
  );
}

// -- a deck's page ----------------------------------------------------------------------------------

function DeckPage({ source, myDecks, refreshDecks, openCard, onBack, onSaved, swap }) {
  const [deck, setDeck] = useStateD(null);
  const [rows, setRows] = useStateD(null);
  const [coverage, setCoverage] = useStateD(null);
  const [error, setError] = useStateD('');
  const [loading, setLoading] = useStateD(true);
  const [progress, setProgress] = useStateD({ done: 0, total: 0 });
  const [tab, setTab] = useStateD('cards');
  const [filter, setFilter] = useStateD('all');
  const [format, setFormat] = useStateD(null);
  const [justSaved, setJustSaved] = useStateD(false);
  const [reload, setReload] = useStateD(0);
  const [since, setSince] = useStateD(null); // what changed since the person last looked at this saved deck (#93)
  const askAgain = useRefD(false); // the Refresh button asks the site again; opening the page may use the Vault's ten-minute copy
  const [changing, setChanging] = useStateD(false); // the change flow (cut and add cards; deck_change.jsx) is open
  const swapRead = useRefD(undefined); // the swap in the address (the Ideas view's "Swap into the deck"), read once
  const changeButton = useRefD(null);

  async function load() {
    setError(''); setLoading(true); setRows(null); setCoverage(null);
    try {
      let d;
      if (source.url) {
        try {
          const refresh = askAgain.current; askAgain.current = false;
          d = await window.DeckSrc.fetchUrl(source.url.trim(), refresh);
          window.VaultApi.rememberDeckAuthor(source.saved, d.author);
        } catch (e) {
          if (!source.saved) throw e;
          d = await window.DeckSrc.parseText(source.saved.text); // the source is unreachable: the saved copy
          d = { ...d, title: source.saved.name, url: source.saved.source_url, author: source.saved.source_author || '', offline: e.message };
        }
      } else {
        d = await window.DeckSrc.parseText(source.text);
        if (source.saved) d = { ...d, title: source.saved.name };
        if (source.credit) d = { ...d, title: source.credit.name || d.title, url: source.credit.url, author: source.credit.author };
      }
      if (!d.cards.length) throw new Error('No cards found. Use "4 Card Name" per line.');
      const merged = mergeDeckCards(d.cards);
      d = { ...d, cards: merged };
      setDeck(d);
      setFormat((f) => f || (merged.some(isCommander) ? 'commander' : 'standard'));
      const cov = await window.VaultApi.deckCoverage(deckListText(merged));
      const lines = coverageFor(merged, cov.cards);
      setProgress({ done: 0, total: merged.length });
      const scry = await window.Scryfall.collection(merged.map((c) => ({ name: c.name, set: c.set, collector_number: c.collector_number })),
        (p) => setProgress({ done: p.done, total: p.total }));
      setRows(merged.map((c, i) => {
        const l = lines[i];
        const unit = l ? l.unit_price : null;
        return { ...c, scry: scry[i], owned: l ? l.have : 0, need: l ? l.missing : c.qty, status: l ? l.status : 'missing',
          maybeOwned: l && l.maybe_owned ? l.maybe_owned : [], unitPrice: unit, priced: unit != null,
          rowCost: l && l.missing_cost != null ? l.missing_cost : 0,
          ownEntries: (l ? l.owned_printings : []).filter((o) => o.quantity > 0).map((o) => ({ s: o.set, sn: '', cn: o.collector_number, p: o.printing, q: o.quantity, mk: o.unit_price })) };
      }));
      setCoverage(cov);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  }
  useEffectD(() => { load(); }, [reload]);

  const text = useMemoD(() => (deck ? deckListText(deck.cards) : ''), [deck]);
  const saved = source.saved || (deck && deck.url && Array.isArray(myDecks) ? myDecks.find((d) => window.DeckSrc.sourceKey(d.source_url) === window.DeckSrc.sourceKey(deck.url)) : null);
  // Opening a saved deck: the Vault records the list shown if its cards changed, and says what changed since the last look.
  const savedId = saved ? saved.id : null;
  useEffectD(() => {
    if (!savedId || !text) return;
    let stop = false;
    window.VaultApi.deckSeen(savedId, text).then((a) => { if (!stop) setSince(a.since_last_looked || null); }).catch(() => {});
    return () => { stop = true; };
  }, [savedId, text]);
  // A swap in the address opens the change flow with its cards, once the deck is shown and only for a deck of the person's own.
  useEffectD(() => {
    if (swap && swapRead.current === undefined && rows && source.saved && savedId) {
      swapRead.current = window.VaultDeckChange.parseSwap(swap);
      setChanging(true);
      try { // the swap is spent once read: a refresh or Back does not fill the flow in again
        const s = history.state || {};
        if (s.route && s.route.swap) history.replaceState({ ...s, route: { ...s.route, swap: undefined } }, '', location.pathname + location.search + location.hash.split('?')[0]);
      } catch {}
    }
  }, [swap, rows, savedId]);
  const closeChange = () => {
    setChanging(false);
    swapRead.current = null;
    setTimeout(() => changeButton.current && changeButton.current.focus(), 0);
  };
  // The change was saved: the Vault marks the new list as seen (it is the person's own change), and the page reads the saved deck again,
  // unless the page reads its list from the deck's site, which this change does not touch.
  async function changeApplied(d, newText) {
    try { await window.VaultApi.deckSeen(d.id, newText); } catch {}
    onSaved(d);
    if (!source.url) { setSince(null); setJustSaved(false); setReload((n) => n + 1); }
  }
  const summary = useMemoD(() => {
    if (!rows || !coverage) return null;
    let total = 0, ownedQty = 0, missingQty = 0, ownedFully = 0, ownedPartial = 0, missingAll = 0;
    for (const r of rows) {
      total += r.qty; ownedQty += Math.min(r.owned, r.qty); missingQty += r.need;
      if (r.status === 'owned') ownedFully++; else if (r.status === 'partial') ownedPartial++; else missingAll++;
    }
    return { total, ownedQty, missingQty, missingCost: coverage.missing_cost || 0, unpricedQty: coverage.missing_unpriced || 0, ownedFully, ownedPartial, missingAll };
  }, [rows, coverage]);

  async function save() {
    const name = saved ? saved.name : deck.title === 'Pasted decklist' ? (prompt('Name this deck', 'My deck') || 'My deck') : deck.title;
    try {
      // The author goes with the copy, so it is still credited when the source can't be reached.
      const author = (deck.url && (deck.author || (saved && saved.source_author))) || null;
      const d = saved ? await window.VaultApi.updateDeck(saved.id, name, text, deck.url || null, author)
        : await window.VaultApi.saveDeck(name, text, deck.url || null, author);
      setJustSaved(true); onSaved(d); // the caller refreshes what it needs
    } catch (e) { setError('Saving failed: ' + e.message); }
  }
  async function remove() {
    if (!saved || !confirm(`Remove “${saved.name}” from your decks?`)) return;
    try { await window.VaultApi.deleteDeck(saved.id); await refreshDecks(); onBack(); }
    catch (e) { setError('Removing failed: ' + e.message); }
  }

  const TABS = [['cards', 'Cards'], ['does', 'What it does'], ['stats', 'Stats'], ['opening', 'Opening turns'], ['legality', 'Legality'], ['upgrades', 'Upgrades'], ['combos', 'Combos'], ['buy', 'Buy list'],
    ...(savedId ? [['history', 'History']] : [])];

  return (
    <div data-screen-label="04 Deck">
      <button className="btn xs ghost" onClick={onBack} style={{ marginBottom: 12 }}>← Your decks</button>
      <div className="page-head" style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'flex-end', marginBottom: 16 }}>
        <div style={{ minWidth: 0 }}>
          <p className="eyebrow">{saved ? 'Saved deck' : 'Deck'}</p>
          <h1 className="h1" style={{ marginTop: 6 }}>{deck ? deck.title : loading ? 'Loading…' : 'Deck'}</h1>
          {deck && (
            <p className="muted" style={{ fontSize: 11, fontFamily: 'var(--mono)', marginTop: 6 }}>
              {deck.author && <>by {deck.author} · </>}
              {deck.url && <a href={deck.url} target="_blank" rel="noopener noreferrer">{window.DeckSrc.isArchidekt(deck.url) ? 'on Archidekt' : 'original'} ↗</a>}
              {deck.url && window.DeckSrc.isArchidekt(deck.url) && <> · deck list from Archidekt, thanks to its author</>}
              {deck.cache && <> · read from Archidekt {ageText(deck.cache.age_seconds)}{deck.cache.from_cache ? ' (the Vault’s copy; Refresh asks Archidekt again)' : ''}</>}
            </p>
          )}
          {deck && deck.offline && <p style={{ fontSize: 12, color: 'var(--gold)', marginTop: 6 }}>Couldn't reach the deck's site ({deck.offline}), so this is your saved copy.</p>}
        </div>
        {deck && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <button className="btn sm primary" onClick={save} disabled={justSaved || (!Array.isArray(myDecks) && !source.saved) || !rows}
              title={!Array.isArray(myDecks) && !source.saved ? 'Waiting for your saved decks, to update rather than duplicate' : undefined}>
              {justSaved ? 'Saved ✓' : saved ? 'Update saved copy' : 'Save to your decks'}
            </button>
            {deck.url && <button className="btn sm" onClick={() => { setJustSaved(false); askAgain.current = true; setReload((n) => n + 1); }} disabled={loading}>Refresh</button>}
            {saved && source.saved && rows && (
              <button ref={changeButton} className="btn sm" aria-expanded={changing} onClick={() => (changing ? closeChange() : setChanging(true))}>Change this deck</button>
            )}
            {saved && <button className="btn sm ghost" onClick={remove}>Remove</button>}
          </div>
        )}
      </div>

      {error && (
        <div className="panel" style={{ borderColor: 'oklch(0.62 0.18 25 / 0.5)', marginBottom: 16, fontSize: 13 }}>
          <strong style={{ color: 'var(--danger)' }}>Couldn't load this deck:</strong> {error}
        </div>
      )}
      {loading && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <span className="spinner"></span> <span className="muted">Checking the deck against your collection…</span>
          {progress.total > 0 && <div className="progress-bar" style={{ marginTop: 10 }}><div style={{ width: `${(progress.done / progress.total) * 100}%` }}></div></div>}
        </div>
      )}

      {since && (
        <div className="panel deck-changed" role="status" style={{ marginBottom: 16 }}>
          <p className="label-mono">Changed since you last looked</p>
          <p className="deck-big" style={{ fontSize: 18 }}>
            <span style={{ color: 'var(--good)' }}>+{since.summary.copies_in}</span> / <span style={{ color: 'var(--danger)' }}>−{since.summary.copies_out}</span>
            <span className="muted" style={{ fontSize: 12 }}> copies</span>
          </p>
          <ChangeList changes={since.changes} />
          <button className="btn xs" onClick={() => setSince(null)}>Got it</button>
        </div>
      )}

      {changing && source.saved && (
        <DeckChange deckId={savedId} deckName={saved.name} rows={rows} format={format || 'commander'} setFormat={setFormat} archidekt={!!source.url}
          initial={swapRead.current} onClose={closeChange} onApplied={changeApplied} onShowHistory={() => { setTab('history'); }} />
      )}

      {summary && (
        <div className="deck-summary panel" style={{ marginBottom: 16 }}>
          <CoverageDonut summary={summary} size={84} />
          <div><p className="label-mono">Owned</p><p className="deck-big" style={{ color: 'var(--good)' }}>{summary.ownedQty}<span className="muted" style={{ fontSize: 15 }}>/{summary.total}</span></p></div>
          <div><p className="label-mono">Missing</p><p className="deck-big" style={{ color: summary.missingQty ? 'var(--danger)' : 'var(--good)' }}>{summary.missingQty}</p></div>
          <div><p className="label-mono">To finish</p><p className="deck-big" style={{ color: 'var(--gold)' }}>{summary.missingQty && !summary.missingCost && summary.unpricedQty ? 'no price' : (summary.unpricedQty ? '≥ ' : '') + money(summary.missingCost)}</p>
            {summary.unpricedQty > 0 && <p className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)' }}>{summary.unpricedQty} without a price</p>}</div>
        </div>
      )}

      {rows && (
        <>
          <div className="deck-tabs" role="group" aria-label="Deck views">
            {TABS.map(([k, label]) => (
              <button key={k} aria-pressed={tab === k} className={`chip ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{label}</button>
            ))}
          </div>
          {tab === 'cards' && <DeckCards rows={rows} summary={summary} filter={filter} setFilter={setFilter} openCard={openCard} />}
          {tab === 'stats' && <DeckStats text={text} onOpening={() => setTab('opening')} />}
          {tab === 'does' && <window.DeckRoles text={text} rows={rows} cardFor={deckRowCard} openCard={openCard} />}
          {tab === 'opening' && <DeckOpening text={text} title={deck.title} format={format || 'commander'} setFormat={setFormat} />}
          {tab === 'legality' && <DeckLegality text={text} format={format} setFormat={setFormat} />}
          {tab === 'upgrades' && <DeckUpgrades text={text} format={format} setFormat={setFormat} />}
          {tab === 'combos' && <DeckCombos text={text} />}
          {tab === 'buy' && <DeckBuyList text={text} />}
          {tab === 'history' && savedId && <DeckHistory deckId={savedId} />}
        </>
      )}
    </div>
  );
}

// -- History: the deck's earlier lists (#93) -----------------------------------------------------

const VERSION_SOURCE = { saved: 'Saved', edited: 'Edited', imported: 'Imported from a link', refreshed: 'Refreshed from its link',
  opened: 'Seen when opened (the list at its source then)' };

// "+1 Mind Stone", "−1 Arcane Signet", "2 → 3 Forest": what changed, one line a card.
function ChangeList({ changes }) {
  if (!changes || !changes.length) return <p className="muted" style={{ fontSize: 12 }}>No card changed.</p>;
  return (
    <ul className="deck-changes">
      {changes.map((c, i) => {
        const gone = c.after === 0, added = c.before === 0;
        return (
          <li key={i}>
            <span style={{ color: added ? 'var(--good)' : gone ? 'var(--danger)' : 'var(--gold)' }}>
              {added ? '+' + c.after : gone ? '−' + c.before : c.before + ' → ' + c.after}
            </span> {c.card}{c.section !== 'Deck' && <span className="muted"> ({c.section})</span>}
          </li>
        );
      })}
    </ul>
  );
}

function DeckHistory({ deckId }) {
  const [state, setState] = useStateD({ loading: true });
  const [shown, setShown] = useStateD({}); // version id -> its list
  useEffectD(() => {
    let stop = false;
    setState({ loading: true });
    window.VaultApi.deckVersions(deckId).then((a) => !stop && setState({ data: a })).catch((e) => !stop && setState({ error: e.message }));
    return () => { stop = true; };
  }, [deckId]);
  if (state.loading) return <div className="panel"><span className="spinner"></span> <span className="muted">Reading the deck's history…</span></div>;
  if (state.error) return <div className="panel"><strong style={{ color: 'var(--danger)' }}>Couldn't read the history:</strong> {state.error}</div>;
  const { items, keep } = state.data;
  async function show(v) {
    if (shown[v.id] !== undefined) { setShown((s) => { const n = { ...s }; delete n[v.id]; return n; }); return; }
    try { const a = await window.VaultApi.deckVersion(deckId, v.id); setShown((s) => ({ ...s, [v.id]: a.text })); }
    catch (e) { setShown((s) => ({ ...s, [v.id]: 'Could not read this version: ' + e.message })); }
  }
  return (
    <div className="deck-history">
      <p className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
        The Vault keeps this deck's list each time its cards change (the last {keep}, oldest dropped first). A new name or a different order is not a change.
      </p>
      <ol className="panel">
        {items.map((v) => (
          <li key={v.id}>
            <div className="deck-history-head">
              <strong>{new Date(v.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</strong>
              <span className="muted"> · {VERSION_SOURCE[v.source] || v.source} · {v.cards} cards</span>
            </div>
            {v.changes ? <ChangeList changes={v.changes} /> : <p className="muted" style={{ fontSize: 12 }}>The oldest list kept.</p>}
            <button className="btn xs" onClick={() => show(v)} aria-expanded={shown[v.id] !== undefined}>{shown[v.id] !== undefined ? 'Hide this list' : 'Show this list'}</button>
            {shown[v.id] !== undefined && <pre className="deck-version-text">{shown[v.id]}</pre>}
          </li>
        ))}
      </ol>
    </div>
  );
}

// Calls a deck analysis endpoint when `key` changes; { result } or { error }.
function useDeckAnswer(call, key) {
  const [state, setState] = useStateD({ loading: true });
  useEffectD(() => {
    let stop = false;
    setState({ loading: true });
    call().then((a) => !stop && setState({ result: a.result })).catch((e) => !stop && setState({ error: e.message }));
    return () => { stop = true; };
  }, [key]);
  return state;
}
const Waiting = ({ state, what }) => state.loading ? <div className="panel"><span className="spinner"></span> <span className="muted">Working out {what}…</span></div>
  : state.error ? <div className="panel"><strong style={{ color: 'var(--danger)' }}>Couldn't work out {what}:</strong> {state.error}</div> : null;

function FormatPicker({ format, setFormat }) {
  return (
    <label className="label-mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      Format
      <select className="select" value={format} onChange={(e) => setFormat(e.target.value)}>
        {FORMAT_CHOICES.map((f) => <option key={f} value={f}>{f}</option>)}
      </select>
    </label>
  );
}

// -- Cards: what you own of each, grouped by type ----------------------------------------------

// A deck row in the shape the card panel opens (the Cards tab and the "What it does" tab both open the card this way).
const deckRowCard = (r) => ({ n: r.name, s: r.scry ? r.scry.set : '', cn: r.scry ? r.scry.collector_number : '', p: 'Normal', c: 'Mint', l: 'English', q: r.owned, mk: r.unitPrice || 0, lo: 0, mi: 0, pd: 0, fd: '', ld: '', _scry: r.scry, _ownEntries: r.ownEntries, _deckRow: r });

function DeckCards({ rows, summary, filter, setFilter, openCard }) {
  const groups = useMemoD(() => {
    const g = {};
    for (const r of rows) if (filter === 'all' || r.status === filter) (g[primaryType(r)] = g[primaryType(r)] || []).push(r);
    return TYPE_ORDER.filter((t) => g[t]).map((t) => [t, g[t].sort((a, b) => a.name.localeCompare(b.name))]);
  }, [rows, filter]);
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, margin: '12px 0' }}>
        <FilterPill active={filter === 'owned'} onClick={() => setFilter(filter === 'owned' ? 'all' : 'owned')} count={summary.ownedFully} label="Have" color="var(--good)" />
        <FilterPill active={filter === 'partial'} onClick={() => setFilter(filter === 'partial' ? 'all' : 'partial')} count={summary.ownedPartial} label="Partial" color="var(--gold)" />
        <FilterPill active={filter === 'missing'} onClick={() => setFilter(filter === 'missing' ? 'all' : 'missing')} count={summary.missingAll} label="Need" color="var(--danger)" />
      </div>
      <div className="panel panel-flush">
        <div className="deck-row head"><div>Qty</div><div>Card</div><div className="num">Have</div><div className="num">Need × $</div><div className="num">Cost</div></div>
        {groups.map(([type, list]) => (
          <React.Fragment key={type}>
            <div className="deck-group">{type} <span className="muted">· {list.reduce((n, r) => n + r.qty, 0)}</span></div>
            {list.map((r) => (
              <div className={`deck-row ${r.status}`} key={r.name} {...(r.scry ? window.vaultPressable(() => openCard(deckRowCard(r)), r.name) : {})} style={{ cursor: r.scry ? 'pointer' : 'default' }}>
                <div className="qty">{r.qty}×</div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{r.name}</div>
                  {r.need > 0 && <BuyMenu card={r.name} />}
                  {r.maybeOwned.length > 0 && <div style={{ fontSize: 11, color: 'var(--gold)', marginTop: 2 }}>You may own {r.maybeOwned.map((m) => `${m.quantity} as “${m.name}”`).join(', ')}</div>}
                  {r.ownEntries.length > 0 && (
                    <div className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)', marginTop: 2 }}>
                      in your vault: {r.ownEntries.slice(0, 3).map((o) => `${o.q}× ${String(o.s).toUpperCase()}${o.p && o.p !== 'Normal' ? ' ' + o.p.toLowerCase() : ''}`).join(', ')}{r.ownEntries.length > 3 ? ` +${r.ownEntries.length - 3} more` : ''}
                    </div>
                  )}
                  {r.scry && !r.ownEntries.length && (
                    <div className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)', marginTop: 2, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      <ColorIdentity colors={r.scry.color_identity} /><span>{r.scry.type_line?.split(' — ')[0]}</span>
                    </div>
                  )}
                </div>
                <div className={`have ${r.status === 'owned' ? 'full' : r.status === 'partial' ? 'part' : 'none'}`}>
                  {r.owned > 0 ? `${Math.min(r.owned, r.qty)}/${r.qty}` : '0'}
                  {r.owned > r.qty && <div className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)' }}>you own {r.owned}</div>}
                </div>
                <div className="cost muted">{r.need > 0 ? `${r.need} × ${r.priced ? money(r.unitPrice) : '?'}` : '—'}</div>
                <div className="cost" style={{ color: r.rowCost > 0 ? 'var(--gold)' : 'var(--muted)' }}>{r.need > 0 ? (r.priced ? money(r.rowCost) : '?') : '✓'}</div>
              </div>
            ))}
          </React.Fragment>
        ))}
      </div>
    </>
  );
}

// -- Stats --------------------------------------------------------------------------------------

// The mana curve (cards that are not lands, by mana value) from POST /decks/stats: the Stats tab and the Opening turns tab draw this one
// component from the same field, so they cannot disagree. The bars are drawn from the answer's numbers, `role="img"` lists every value,
// and with `full` the caption and a table of the same numbers (reachable by keyboard) go under it.
function DeckCurve({ stats, full }) {
  const c = window.VaultDeckSim.curve(stats);
  return (
    <>
      <div className="deck-curve" role="img" aria-label={c.label}>
        {c.bars.map((b) => (
          <div key={b.mv} className="deck-curve-col" aria-hidden="true">
            <span className="label-mono">{b.n || ''}</span>
            <div style={{ height: `${b.height}%` }}></div>
            <span className="label-mono">{b.mv}</span>
          </div>
        ))}
      </div>
      {full && (
        <>
          <p className="ds-sub">{c.caption}</p>
          <details className="ds-numbers">
            <summary>Show the curve as a table</summary>
            <table className="ds-small">
              <thead><tr><th scope="col">Mana value</th><th scope="col">Cards</th></tr></thead>
              <tbody>{c.bars.map((b) => <tr key={b.mv}><th scope="row">{b.mv}</th><td>{b.n}</td></tr>)}</tbody>
            </table>
          </details>
        </>
      )}
    </>
  );
}

function DeckStats({ text, onOpening }) {
  const s = useDeckAnswer(() => window.VaultApi.deckStats(text), text);
  if (!s.result) return <Waiting state={s} what="the deck's stats" />;
  const r = s.result;
  const roles = Object.entries(r.roles).filter(([, v]) => v.count > 0).sort((a, b) => b[1].count - a[1].count);
  return (
    <div className="deck-panels">
      <div className="panel">
        <p className="eyebrow">At a glance</p>
        <div className="deck-glance">
          <div><p className="label-mono">Cards</p><p className="deck-big">{r.cards}</p></div>
          <div><p className="label-mono">Lands</p><p className="deck-big">{r.lands}</p></div>
          <div><p className="label-mono">Avg. mana value</p><p className="deck-big">{r.average_mana_value_nonland}</p></div>
          <div><p className="label-mono">Deck value</p><p className="deck-big">{r.priced_cards ? (r.unpriced_cards ? '≥ ' : '') + money(r.estimated_cost_usd) : '?'}</p>
            {r.priced_cards > 0 && r.unpriced_cards > 0 && <p className="muted" style={{ fontSize: 10, fontFamily: 'var(--mono)' }}>{r.unpriced_cards} without a price</p>}</div>
        </div>
        <p className="muted" style={{ fontSize: 11, marginTop: 10, display: 'flex', gap: 6, alignItems: 'center' }}>Colour identity <ColorIdentity colors={r.color_identity} /></p>
        {r.unmatched.length > 0 && <p style={{ fontSize: 12, color: 'var(--gold)', marginTop: 6 }}>Not in the card catalog: {r.unmatched.join(', ')}</p>}
      </div>
      <div className="panel">
        <p className="eyebrow">Mana curve <span className="muted">(non-land cards)</span></p>
        <DeckCurve stats={r} />
        <p style={{ marginTop: 10 }}><button className="btn sm ghost" onClick={onOpening}>See how this curve plays: Opening turns</button></p>
      </div>
      <div className="panel">
        <p className="eyebrow">Card types</p>
        {Object.entries(r.types).sort((a, b) => b[1] - a[1]).map(([t, n]) => (
          <div key={t} className="deck-kv"><span>{t}</span><strong>{n}</strong></div>
        ))}
      </div>
      <div className="panel">
        <p className="eyebrow">Roles <span className="muted">(Scryfall Tagger tags, a community's opinion)</span></p>
        {roles.length === 0 ? <p className="muted" style={{ fontSize: 12 }}>No role tags found for these cards.</p> :
          roles.map(([role, v]) => (
            <details key={role} className="deck-kv-details">
              <summary className="deck-kv"><span>{role.replace(/_/g, ' ')}</span><strong>{v.count}</strong></summary>
              <p className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>{v.cards.map((c) => c.name).join(', ')}</p>
            </details>
          ))}
        <p className="muted" style={{ fontSize: 10, marginTop: 8 }}>{r.role_note}</p>
      </div>
    </div>
  );
}

// -- Legality -----------------------------------------------------------------------------------

function DeckLegality({ text, format, setFormat }) {
  const s = useDeckAnswer(() => window.VaultApi.deckLegality(text, format), text + '|' + format);
  return (
    <div className="panel">
      <FormatPicker format={format} setFormat={setFormat} />
      {!s.result ? <div style={{ marginTop: 12 }}><Waiting state={s} what="legality" /></div> : (
        <>
          <p className="h-display" style={{ fontSize: 22, margin: '14px 0 8px', color: s.result.legal ? 'var(--good)' : 'var(--danger)' }}>
            {s.result.legal ? `Legal in ${s.result.format}.` : `${s.result.issues.length} ${s.result.issues.length === 1 ? 'problem' : 'problems'} in ${s.result.format}.`}
          </p>
          {s.result.issues.map((i, n) => (
            <div key={n} className="deck-kv"><span>{i.card ? <a href={scryfallLink(i.card)} target="_blank" rel="noopener noreferrer">{i.card}</a> : 'Deck'}</span><span className="muted" style={{ textAlign: 'right' }}>{i.detail}</span></div>
          ))}
          <p className="muted" style={{ fontSize: 11, marginTop: 12, lineHeight: 1.5 }}>Not checked: {s.result.not_checked.join('; ')}.</p>
        </>
      )}
    </div>
  );
}

// -- Upgrades: candidates within a budget, marking the ones already in your collection -------------

// Role guidelines (ramp 10, draw 10, …) exist only for 100-card decks (vault/deck_tools.py SIZE_100);
// for other formats you pick the roles to look for.
const GUIDED_FORMATS = ['commander', 'duel', 'predh', 'paupercommander'];
const ROLES = ['ramp', 'draw', 'removal', 'sweeper', 'counterspell', 'tutor', 'recursion', 'sacrifice_outlet'];

function DeckUpgrades({ text, format, setFormat }) {
  const [budget, setBudget] = useStateD(5);
  const [asked, setAsked] = useStateD(5);
  const [roles, setRoles] = useStateD(['removal', 'draw']);
  const guided = GUIDED_FORMATS.includes(format);
  const pick = guided ? null : roles;
  const s = useDeckAnswer(() => (pick && !pick.length ? Promise.reject(new Error('Pick at least one role to look for.'))
    : window.VaultApi.deckUpgrades(text, format, Number(asked) || 0, pick)), text + '|' + format + '|' + asked + '|' + (pick || []).join());
  const toggle = (r) => setRoles((rs) => (rs.includes(r) ? rs.filter((x) => x !== r) : [...rs, r]));
  return (
    <div className="panel">
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <FormatPicker format={format} setFormat={setFormat} />
        <label className="label-mono" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          Up to $<input className="input" type="number" min="0" step="1" style={{ width: 90 }} value={budget} onChange={(e) => setBudget(e.target.value)} />
          a card
        </label>
        <button className="btn sm primary" onClick={() => setAsked(budget)}>Find upgrades</button>
      </div>
      {!guided && (
        <div style={{ marginTop: 12 }}>
          <p className="muted" style={{ fontSize: 12, marginBottom: 6 }}>There are no usual role counts for {format}; pick what to look for.</p>
          <div className="row" style={{ gap: 4, flexWrap: 'wrap' }} role="group" aria-label="Roles to look for">
            {ROLES.map((r) => <button key={r} className={`chip ${roles.includes(r) ? 'active' : ''}`} aria-pressed={roles.includes(r)} onClick={() => toggle(r)}>{r.replace(/_/g, ' ')}</button>)}
          </div>
        </div>
      )}
      {!s.result ? <div style={{ marginTop: 12 }}><Waiting state={s} what="upgrade candidates" /></div> : (
        <>
          {guided && Object.keys(s.result.gaps).length === 0 && <p className="muted" style={{ marginTop: 12 }}>The deck meets the usual role counts for {s.result.format}; no gaps to fill.</p>}
          {Object.entries(s.result.candidates).map(([role, list]) => (
            <div key={role} style={{ marginTop: 16 }}>
              <p className="eyebrow">{role.replace(/_/g, ' ')} <span className="muted">· the deck has {s.result.gaps[role]?.have}{s.result.gaps[role]?.guideline ? `, about ${s.result.gaps[role].guideline} is usual` : ''}</span></p>
              {list.length === 0 ? <p className="muted" style={{ fontSize: 12 }}>Nothing within the budget.</p> : list.map((c) => {
                const have = c.owned_copies || 0; // yours already: free to add, suggested whatever the budget
                return (
                  <div key={c.name} className="deck-kv">
                    <span><a href={scryfallLink(c.name)} target="_blank" rel="noopener noreferrer">{c.name}</a>
                      {have > 0 && <span className="deck-badge">in your vault ×{have}</span>}
                      <span className="muted" style={{ display: 'block', fontSize: 11 }}>{c.type_line}</span></span>
                    <strong style={{ color: have ? 'var(--good)' : 'var(--gold)' }}>{have ? 'free' : money(c.price_usd)}</strong>
                  </div>
                );
              })}
            </div>
          ))}
          {s.result.cut_candidates.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <p className="eyebrow">Cards to consider cutting</p>
              <p className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{s.result.cut_candidates.map((c) => c.name).join(', ')}</p>
            </div>
          )}
          <p className="muted" style={{ fontSize: 10, marginTop: 12, lineHeight: 1.5 }}>{s.result.notes.slice(0, 3).join(' ')}</p>
        </>
      )}
    </div>
  );
}

// -- Combos ------------------------------------------------------------------------------------------

function DeckCombos({ text }) {
  const s = useDeckAnswer(() => window.VaultApi.deckCombos(text), text);
  if (!s.result) return <Waiting state={s} what="combos" />;
  const r = s.result;
  const combo = (v, i) => (
    <div key={v.id || i} className="deck-kv" style={{ alignItems: 'flex-start' }}>
      <span style={{ minWidth: 0 }}>
        <a href={v.url} target="_blank" rel="noopener noreferrer">{v.cards.join(' + ')}</a>
        <span className="muted" style={{ display: 'block', fontSize: 11 }}>{v.produces.join(', ')}</span>
        {v.missing.length > 0 && <span style={{ display: 'block', fontSize: 11, color: 'var(--gold)' }}>needs {v.missing.join(', ')}</span>}
      </span>
    </div>
  );
  return (
    <div className="deck-panels">
      <div className="panel"><p className="eyebrow">In this deck · {r.totals.included}</p>{r.included.length ? r.included.map(combo) : <p className="muted" style={{ fontSize: 12 }}>None found.</p>}</div>
      <div className="panel"><p className="eyebrow">One card away · {r.totals.almost_included}</p>{r.almost_included.length ? r.almost_included.map(combo) : <p className="muted" style={{ fontSize: 12 }}>None found.</p>}</div>
      <p className="muted" style={{ fontSize: 10 }}>Combos from <a href="https://commanderspellbook.com" target="_blank" rel="noopener noreferrer">Commander Spellbook</a>, written by its community.</p>
    </div>
  );
}

// -- Buy list -------------------------------------------------------------------------------------------

function DeckBuyList({ text }) {
  const s = useDeckAnswer(() => window.VaultApi.deckShopping(text), text);
  const [copied, setCopied] = useStateD(false);
  if (!s.result) return <Waiting state={s} what="the buy list" />;
  const r = s.result;
  if (!r.lines.length) return <div className="panel"><p className="h-display" style={{ fontSize: 20, color: 'var(--good)' }}>You own every card in this deck.</p></div>;
  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <p className="eyebrow">{r.lines.reduce((n, l) => n + l.quantity, 0)} cards to buy · {r.unpriced_lines === r.lines.length ? 'no prices yet' : (r.unpriced_lines ? '≥ ' : '') + money(r.total_usd)}</p>
        <button className="btn sm primary" onClick={() => navigator.clipboard.writeText(r.text).then(() => setCopied(true))}>{copied ? 'Copied ✓' : 'Copy list'}</button>
      </div>
      {r.lines.map((l) => (
        <div key={l.name} className="deck-kv"><span>{l.quantity}× <a href={scryfallLink(l.name)} target="_blank" rel="noopener noreferrer">{l.name}</a></span><span className="muted">{l.unit_price_usd == null ? '?' : money(l.unit_price_usd * l.quantity)}</span></div>
      ))}
      <p className="muted" style={{ fontSize: 10, marginTop: 10, lineHeight: 1.5 }}>{r.notes.join(' ')}</p>
    </div>
  );
}


function FilterPill({ active, onClick, count, label, color }) {
  return (
    <button onClick={onClick} style={{
      padding: '12px 8px',
      background: active ? `${color.replace(')', ' / 0.12)').replace('oklch(', 'oklch(')}` : 'var(--bg-2)',
      border: `1px solid ${active ? color : 'var(--border)'}`,
      borderRadius: 'var(--radius)',
      cursor: 'pointer',
      transition: 'all .15s',
    }}>
      <div style={{ fontFamily: 'var(--display)', fontSize: 22, fontWeight: 600, color, lineHeight: 1 }}>{count}</div>
      <div className="label-mono" style={{ marginTop: 4 }}>{label}</div>
    </button>
  );
}

function ColorIdentity({ colors }) {
  if (!colors || !colors.length) return <span className="pip sm C">C</span>;
  return (
    <span className="mana">
      {colors.map(c => <span key={c} className={`pip sm ${c}`}>{c}</span>)}
    </span>
  );
}

function CoverageDonut({ summary, size = 120 }) {
  const r = 50, c = 2 * Math.PI * r;
  const ownedPct = summary.total > 0 ? summary.ownedQty / summary.total : 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
      <svg width={size} height={size} viewBox="0 0 120 120" className="donut">
        <circle cx="60" cy="60" r={r} stroke="oklch(0.32 0.014 65)" strokeWidth="10" />
        <circle
          cx="60" cy="60" r={r}
          stroke="oklch(0.66 0.14 145)"
          strokeWidth="10"
          strokeDasharray={`${c * ownedPct} ${c}`}
          strokeLinecap="butt"
        />
      </svg>
      <div>
        <div style={{ fontFamily: 'var(--display)', fontSize: 36, fontWeight: 600, lineHeight: 1 }}>
          {(ownedPct * 100).toFixed(0)}<span style={{ fontSize: 18, color: 'var(--muted)' }}>%</span>
        </div>
        <div className="label-mono" style={{ marginTop: 4 }}>Coverage</div>
      </div>
    </div>
  );
}

window.DeckView = DeckView;
window.ColorIdentity = ColorIdentity;
