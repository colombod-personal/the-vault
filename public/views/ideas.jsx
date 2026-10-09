// The deck ideas lab (#163, docs/deck-ideas-lab-design.md): pick a saved deck, see what the collection covers in role lanes, pick a card
// and see which owned cards could stand in, what another deck holds and what it would cost to buy. Every number is the server's
// (GET /decks/{id}/ideas and /ideas/alternatives); the words and the formatting are public/lib/ideas.js (window.VaultIdeas); this file
// lays them out as plain text rows in a grid (no physics, no full-size art). Own account only: a shared collection has no Ideas.
// The lab proposes and applies nothing: Swap into the deck opens the deck page's change flow (#/decks/{id}?swap={"cut":[...],"add":[...]}).
const { useEffect: useEffectI, useMemo: useMemoI, useRef: useRefI, useState: useStateI } = React;

const IDEAS_FORMAT_KEY = 'vault_ideas_format';  // the format the person chose, remembered in this browser (a convenience, never needed)
const ideasFormatGet = () => {
  try { const f = localStorage.getItem(IDEAS_FORMAT_KEY); return window.VaultIdeas.FORMATS.includes(f) ? f : null; } catch { return null; }
};
const ideasFormatSet = (f) => { try { localStorage.setItem(IDEAS_FORMAT_KEY, f); } catch { /* private window: it just is not remembered */ } };

// The phone layout (the same 760 px as layout.css): lanes become lists the person opens one at a time.
function useIdeasCompact() {
  const query = '(max-width: 760px)';
  const [compact, setCompact] = useStateI(() => !!(window.matchMedia && window.matchMedia(query).matches));
  useEffectI(() => {
    if (!window.matchMedia) return undefined;
    const m = window.matchMedia(query);
    const on = () => setCompact(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, []);
  return compact;
}

// A query that can be asked again from its error message.
function useIdeasQuery(load, deps) {
  const [tick, setTick] = useStateI(0);
  const q = window.useVaultQuery(load, [...deps, tick]);
  return { ...q, retry: () => setTick((t) => t + 1) };
}

// A link that opens inside the app on a plain click and keeps the browser's own behaviour (new tab, copy address) otherwise.
const ideasLink = (go) => (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  go();
};

function IdeasFail({ what, error, retry }) {
  return (
    <div className="ideas-fail" role="alert">
      <span>Couldn't load {what}: {window.VaultIdeas.errorText(error)}</span>
      <button type="button" className="btn xs" onClick={retry}>Try again</button>
    </div>
  );
}

function IdeasLoading({ what }) {
  return <p className="ideas-loading" role="status"><span className="spinner spinner-xs"></span> Reading {what}…</p>;
}

function IdeasTitle({ children }) {
  return (
    <div className="ideas-head-title">
      <p className="eyebrow">Deck ideas</p>
      <h1 className="h1">Ideas</h1>
      {children}
    </div>
  );
}

// ---- The page ---------------------------------------------------------------------------------------------------------------------

function Ideas({ data, route, onGo, onUp, readOnly, onImported, openCard }) {
  if (readOnly) return <IdeasShared />;
  if (!data.meta.totalQty) return <IdeasEmpty onImported={onImported} />;
  if (data.meta.offline) return <IdeasOffline />;
  if (!route.deckId) return <IdeasStart data={data} onGo={onGo} />;
  return <IdeasDeck key={route.deckId} data={data} route={route} onGo={onGo} onUp={onUp} openCard={openCard} />;
}

// Nothing imported: the page is one panel.
function IdeasEmpty({ onImported }) {
  return (
    <div data-screen-label="06 Ideas" className="ideas">
      <div className="ideas-head"><IdeasTitle /></div>
      <div className="panel ideas-note">
        <p className="ideas-note-text">Import your collection to see what your decks could use.</p>
        <window.ImportButton className="btn primary" label="Import a file" onImported={onImported} />
      </div>
    </div>
  );
}

// A shared collection has no Ideas: it compares your own saved decks with your own copies, and decks are not shared with a collection.
function IdeasShared() {
  return (
    <div data-screen-label="06 Ideas" className="ideas">
      <div className="ideas-head"><IdeasTitle /></div>
      <div className="panel ideas-note">
        <p className="ideas-note-text">A shared collection has no Ideas.</p>
        <p className="muted">Ideas compares your own saved decks with your own copies, so it belongs to your own vault. Use “Back to my vault” above to return to it; the Vault overview and Browse show the shared collection.</p>
      </div>
    </div>
  );
}

function IdeasOffline() {
  return (
    <div data-screen-label="06 Ideas" className="ideas">
      <div className="ideas-head"><IdeasTitle /></div>
      <div className="panel ideas-note" role="alert">
        <p className="ideas-note-text">Ideas needs a connection.</p>
        <p className="muted">It asks the Vault's server about your decks and keeps no copy of the answers on this device.</p>
        <button type="button" className="btn" onClick={() => window.location.reload()}>Try again</button>
      </div>
    </div>
  );
}

// ---- State 1: the start, nothing picked ------------------------------------------------------------------------------------------

function IdeasStart({ data, onGo }) {
  const Text = window.VaultIdeas;
  const q = useIdeasQuery(() => window.VaultApi.decks(true), [data.meta.version]);
  const decks = q.data;
  return (
    <div data-screen-label="06 Ideas" className="ideas">
      <div className="ideas-head"><IdeasTitle><p className="ideas-lede">Pick a deck to see what you could build.</p></IdeasTitle></div>
      {q.error && !decks && <IdeasFail what="your decks" error={q.error} retry={q.retry} />}
      {!decks && !q.error && <IdeasLoading what="your decks" />}
      {decks && decks.length === 0 && (
        <div className="panel ideas-note">
          <p className="ideas-note-text">No saved decks yet.</p>
          <p className="muted">Ideas explores a deck you have saved. Add one on the Decks page, then come back.</p>
          <a className="btn primary" href="#/decks" onClick={ideasLink(() => onGo({ view: 'decks' }))}>Add a deck</a>
        </div>
      )}
      {decks && decks.length > 0 && (
        <>
          <ul className="ideas-decks" aria-label="Your saved decks">
            {decks.map((d) => {
              const t = Text.tileLine(d);
              const route = Text.ideasRoute(d.id);
              return (
                <li key={d.id}>
                  <a className="panel ideas-deck" href={Text.ideasHash(route)} onClick={ideasLink(() => onGo(route))}>
                    <span className="ideas-deck-name">{d.name}</span>
                    <span className="ideas-deck-facts">{Text.deckFacts(d.overview).join(' · ')}</span>
                    <span className="ideas-bar" aria-hidden="true"><span style={{ width: `${t.width}%`, background: 'var(--good)' }}></span></span>
                    <span className="ideas-deck-owned"><strong>{t.owned}</strong>{t.missing && <span className={t.missing === 'complete' ? 'good' : 'gold'}> · {t.missing}</span>}</span>
                    <span className="ideas-deck-open">Open<span className="ideas-sr"> {d.name} in Ideas</span> ›</span>
                  </a>
                </li>
              );
            })}
          </ul>
          <div className="panel ideas-add">
            <p>To explore a deck that is not here, save it first: paste a list or an Archidekt link on the Decks page.</p>
            <a className="btn" href="#/decks" onClick={ideasLink(() => onGo({ view: 'decks' }))}>Paste a list or an Archidekt link</a>
          </div>
        </>
      )}
    </div>
  );
}

// ---- States 2 to 5: a deck ------------------------------------------------------------------------------------------------------------

function IdeasDeck({ data, route, onGo, onUp, openCard }) {
  const Text = window.VaultIdeas;
  const deckId = route.deckId, cardName = route.card || null;
  const ideas = useIdeasQuery(() => window.VaultApi.deckIdeas(deckId), [deckId, data.meta.version]);
  const d = ideas.data && String(ideas.data.deck.id) === String(deckId) ? ideas.data : null;
  const [format, setFormatState] = useStateI(ideasFormatGet);
  const setFormat = (f) => { ideasFormatSet(f); setFormatState(f); };
  const compact = useIdeasCompact();
  const [filter, setFilter] = useStateI(null);
  const [openLanes, setOpenLanes] = useStateI({});
  const [combos, setCombos] = useStateI({ state: 'idle' });
  const [zoom, setZoom] = useStateI(null);
  const zoomRef = useRefI(null); zoomRef.current = zoom;
  const lastRow = useRefI(null);

  const clear = () => onGo(Text.ideasRoute());
  const stepOut = () => (cardName ? onUp(Text.ideasRoute(deckId)) : clear());
  const stepRef = useRefI(stepOut); stepRef.current = stepOut;
  const open = (c) => { lastRow.current = c.card; onGo(Text.ideasRoute(deckId, c.card)); };

  // Esc steps out: the enlarged image, then the selected card (back to the deck), then the deck (back to the start). The card drawer
  // and the account panel are dialogs with their own Esc.
  useEffectI(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const t = e.target;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (zoomRef.current) { e.preventDefault(); setZoom(null); return; }
      if (document.querySelector('.drawer, [role="dialog"]')) return;
      e.preventDefault();
      stepRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Focus follows the step: into the panel when a card opens, back to its row when it closes.
  useEffectI(() => {
    if (cardName) {
      const h = document.getElementById('ideas-panel-h');
      if (h) h.focus();
    } else if (lastRow.current) {
      const row = Array.from(document.querySelectorAll('.ideas-row')).find((b) => b.dataset.card === lastRow.current);
      lastRow.current = null;
      if (row) row.focus({ preventScroll: true });
    }
  }, [cardName]);

  const askCombos = async () => {
    setCombos({ state: 'busy' });
    try {
      const page = await window.VaultApi.deckIdeas(deckId, { lane: 'lands', limit: 1, include_combos: 'true' });
      setCombos({ state: 'done', combos: page.combos });
    } catch (e) {
      setCombos({ state: 'error', message: Text.errorText(e) });
    }
  };
  const comboSet = useMemoI(() => Text.comboKeys(combos.combos), [combos.combos]);

  if (ideas.error && !d) {
    return (
      <div data-screen-label="06 Ideas" className="ideas">
        <div className="ideas-head"><IdeasTitle /></div>
        {ideas.error.status === 404 ? (
          <div className="panel ideas-note" role="alert">
            <p className="ideas-note-text">That deck isn't in your decks any more.</p>
            <a className="btn" href="#/ideas" onClick={ideasLink(clear)}>Pick another deck</a>
          </div>
        ) : <IdeasFail what="this deck" error={ideas.error} retry={ideas.retry} />}
      </div>
    );
  }
  if (!d) {
    return (
      <div data-screen-label="06 Ideas" className="ideas">
        <div className="ideas-head"><IdeasTitle /></div>
        <IdeasLoading what="the deck" />
      </div>
    );
  }

  const s = d.summary, head = Text.headline(s);
  const covered = Text.isCovered(s);
  const overview = d.deck.overview || {};
  const chosen = format || (Text.FORMATS.includes(overview.format) ? overview.format : 'commander');
  const lanes = d.lanes.filter((l) => l.total > 0);
  const colours = Text.colourLetters(overview.color_identity);
  const deckRoute = { view: 'decks', deckId: String(deckId) };
  const filterNow = filter || Text.defaultFilter(s);
  const decisions = Text.decisionCards(lanes), borrowedList = Text.borrowedCards(lanes);
  const ownedCombos = Text.ownedCombos(combos.combos);

  return (
    <div data-screen-label="06 Ideas" className="ideas">
      <div className="ideas-head">
        <div className="ideas-head-title">
          <p className="eyebrow">Deck ideas</p>
          <h1 className="h1">{d.deck.name}</h1>
          <p className="ideas-facts">
            {Text.deckFacts(overview, format).join(' · ')}
            {colours.length > 0 && <span className="ideas-colours" role="img" aria-label={`colour identity ${colours.join('')}`}>{window.ColorIdentity ? <window.ColorIdentity colors={colours} /> : colours.join('')}</span>}
          </p>
        </div>
        <div className="ideas-head-side">
          <label className="label-mono ideas-format">Format
            <select className="select" value={chosen} onChange={(e) => setFormat(e.target.value)} aria-label="Format for legality and copy limits">
              {Text.FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <button type="button" className="btn" onClick={clear} title="Back to your decks (Esc)">Clear</button>
        </div>
      </div>

      <p className="ideas-counts" role="status">
        <strong>{head.covered}</strong><span aria-hidden="true"> · </span><span className={s.missing ? 'gold' : ''}>{head.missing}</span>
        <span aria-hidden="true"> · </span><span className={s.borrowed ? 'gold' : ''}>{head.borrowed}</span>
      </p>

      {covered && !cardName && (
        <div className="panel ideas-covered">
          <p className="ideas-covered-text">{Text.COVERED_TEXT}</p>
          <div className="ideas-actions">
            <a className="btn primary" href={`#/decks/${encodeURIComponent(deckId)}`} onClick={ideasLink(() => onGo(deckRoute))}>Open the deck page</a>
            <a className="btn" href={`#/decks/${encodeURIComponent(deckId)}`} onClick={ideasLink(() => onGo(deckRoute))}>See upgrades</a>
          </div>
        </div>
      )}

      <div className={`ideas-main ${cardName ? 'has-card' : ''} ${compact ? 'compact' : ''}`}>
        {(!compact || !cardName) && (
          <div className="ideas-lanes-wrap">
            {compact && !covered && (
              <div className="ideas-filters" role="group" aria-label="Which cards to list first">
                <button type="button" className={`chip ${filterNow === 'missing' ? 'active' : ''}`} aria-pressed={filterNow === 'missing'} onClick={() => setFilter('missing')}>Missing ({s.missing})</button>
                <button type="button" className={`chip ${filterNow === 'borrowed' ? 'active' : ''}`} aria-pressed={filterNow === 'borrowed'} onClick={() => setFilter('borrowed')}>Borrowed ({s.borrowed})</button>
                <button type="button" className={`chip ${filterNow === 'all' ? 'active' : ''}`} aria-pressed={filterNow === 'all'} onClick={() => setFilter('all')}>All</button>
              </div>
            )}
            {compact && !covered && filterNow !== 'all' && (
              <IdeasList title={filterNow === 'missing' ? 'Missing cards' : 'Borrowed cards'} cards={filterNow === 'missing' ? decisions : borrowedList}
                         selected={cardName} onOpen={open} combos={comboSet} rowH={44} empty={filterNow === 'missing' ? 'No card is missing.' : 'No card is borrowed.'} />
            )}
            <div className="ideas-legend" aria-hidden="true">
              <span><i className="ideas-dot owned"></i> owned</span><span><i className="ideas-dot partial"></i> partly</span>
              <span><i className="ideas-dot missing"></i> missing</span><span><i className="ideas-dot borrowed"></i> held by another deck</span>
            </div>
            <div className="ideas-lanes">
              {lanes.map((lane) => (
                <IdeasLane key={lane.lane} lane={lane} selected={cardName} onOpen={open} combos={comboSet} compact={compact}
                           open={!!openLanes[lane.lane]} onToggle={() => setOpenLanes((o) => ({ ...o, [lane.lane]: !o[lane.lane] }))} />
              ))}
            </div>
            <div className="ideas-combos">
              {combos.state === 'idle' && (
                <button type="button" className="btn sm" onClick={askCombos}>Show combos you already own</button>
              )}
              {combos.state === 'busy' && <IdeasLoading what="combos from Commander Spellbook" />}
              {combos.state === 'error' && <IdeasFail what="the combos" error={{ message: combos.message }} retry={askCombos} />}
              {combos.state === 'done' && (combos.combos && combos.combos.checked === false ? (
                <p className="ideas-fine" role="status">Commander Spellbook could not be asked just now: {combos.combos.reason}</p>
              ) : (
                <>
                  <h2 className="ideas-h3">Combos you already own</h2>
                  {ownedCombos.length === 0
                    ? <p className="ideas-fine" role="status">{combos.combos.total ? `None of the ${Text.plural(combos.combos.total, 'combo', 'combos')} Commander Spellbook lists for this deck is complete in your collection.` : 'Commander Spellbook lists no combo for this deck.'}</p>
                    : <ul className="ideas-combo-list">{ownedCombos.map((c) => <li key={c.url || c.cards.join('|')}><a href={c.url} target="_blank" rel="noopener noreferrer">{Text.comboLine(c)}</a></li>)}</ul>}
                  <p className="ideas-fine">Combos are written by Commander Spellbook's community, asked for on request with the deck's card names. Cards in a complete combo are marked “combo”.</p>
                </>
              ))}
            </div>
          </div>
        )}

        <aside className="ideas-side" aria-label={cardName ? 'The selected card' : 'What needs a decision'}>
          {cardName
            ? <IdeasPanel key={cardName + '|' + (format || '')} deck={d.deck} deckId={deckId} cardName={cardName} format={format} data={data}
                          rowCard={lanes.flatMap((l) => l.items).find((c) => c.card.toLowerCase() === cardName.toLowerCase())}
                          onGo={onGo} onUp={() => onUp(Text.ideasRoute(deckId))} openCard={openCard} setZoom={setZoom} />
            : !compact && !covered && (
              <IdeasList title="Needs a decision" cards={decisions} selected={cardName} onOpen={open} combos={comboSet} rowH={36}
                         empty="Nothing is missing. Cards another deck holds are listed in their lanes." />
            )}
        </aside>
      </div>
      {zoom && <IdeasZoom card={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

// ---- Lanes and rows ---------------------------------------------------------------------------------------------------------------------

// Rows: plain text (a dot, the name, the status word). A lane with more than WINDOW_AT cards draws only the rows in view.
function IdeasRows({ cards, rowH, label, selected, onOpen, combos, laneLabel }) {
  const Text = window.VaultIdeas;
  const [top, setTop] = useStateI(0);
  const box = useRefI(null);
  const windowed = Text.needsWindow(cards.length);
  const view = rowH * Text.WINDOW_ROWS;
  const range = windowed ? Text.windowRange(top, rowH, view, cards.length) : { start: 0, end: cards.length };
  const row = (c, i) => {
    const st = Text.rowStatus(c);
    const on = !!selected && selected.toLowerCase() === c.card.toLowerCase();
    return (
      <li key={c.card} style={{ height: rowH }} aria-setsize={cards.length} aria-posinset={i + 1}>
        <button type="button" className={`ideas-row ${on ? 'selected' : ''}`} data-card={c.card} data-i={i} aria-current={on ? 'true' : undefined}
                aria-label={Text.rowLabel(c, laneLabel || c.laneLabel)} title={c.card} onClick={() => onOpen(c)}>
          <span className={`ideas-dot ${st.tone}`} aria-hidden="true"></span>
          <span className="ideas-name">{c.card}</span>
          {combos && combos.has(c.card.toLowerCase()) && <span className="ideas-tag" aria-hidden="true">combo</span>}
          <span className={`ideas-state ${st.tone}`} aria-hidden="true">{st.word}</span>
        </button>
      </li>
    );
  };
  // Arrow keys walk a windowed list: the next row may not be drawn yet, so scroll to it first.
  const onKey = (e) => {
    if (!windowed || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
    const from = e.target.closest && e.target.closest('[data-i]');
    if (!from) return;
    const to = Number(from.dataset.i) + (e.key === 'ArrowDown' ? 1 : -1);
    if (to < 0 || to >= cards.length) return;
    e.preventDefault();
    const el = box.current;
    if (el) {
      if (to * rowH < el.scrollTop) el.scrollTop = to * rowH;
      else if ((to + 1) * rowH > el.scrollTop + view) el.scrollTop = (to + 1) * rowH - view;
    }
    window.requestAnimationFrame(() => { const next = box.current && box.current.querySelector(`[data-i="${to}"]`); if (next) next.focus({ preventScroll: true }); });
  };
  if (!windowed) return <ul className="ideas-rows" aria-label={label} style={{ '--row': rowH + 'px' }}>{cards.map(row)}</ul>;
  return (
    <div className="ideas-window" ref={box} style={{ height: view, '--row': rowH + 'px' }} onScroll={(e) => setTop(e.currentTarget.scrollTop)} onKeyDown={onKey}
         role="group" aria-label={`${label}: ${cards.length} cards, scroll or use the arrow keys for the others`}>
      <ul className="ideas-rows" aria-label={label} style={{ paddingTop: range.start * rowH, paddingBottom: (cards.length - range.end) * rowH }}>
        {cards.slice(range.start, range.end).map((c, i) => row(c, range.start + i))}
      </ul>
    </div>
  );
}

function IdeasList({ title, cards, selected, onOpen, combos, rowH, empty }) {
  return (
    <section className="ideas-list" aria-label={title}>
      <h2 className="ideas-h3">{title}</h2>
      {cards.length === 0
        ? <p className="ideas-fine">{empty}</p>
        : <IdeasRows cards={cards} rowH={rowH} label={title} selected={selected} onOpen={onOpen} combos={combos} />}
    </section>
  );
}

// One lane: the server's first page, then its own `next` links.
function IdeasLane({ lane, selected, onOpen, combos, compact, open, onToggle }) {
  const Text = window.VaultIdeas;
  const [more, setMore] = useStateI({ items: [], next: undefined, busy: false, error: null });
  useEffectI(() => { setMore({ items: [], next: undefined, busy: false, error: null }); }, [lane]);
  const next = more.next !== undefined ? more.next : lane._links && lane._links.next ? lane._links.next.href : null;
  const cards = lane.items.concat(more.items);
  const loadMore = async () => {
    if (!next || more.busy) return;
    setMore((m) => ({ ...m, busy: true, error: null }));
    try {
      const page = await window.VaultApi.follow(next);
      const got = page.lanes && page.lanes[0] ? page.lanes[0] : { items: [], _links: {} };
      const after = got._links && got._links.next && got.items.length ? got._links.next.href : null;
      setMore((m) => ({ items: m.items.concat(got.items), next: after === next ? null : after, busy: false, error: null }));
    } catch (e) {
      setMore((m) => ({ ...m, busy: false, error: Text.errorText(e) }));
    }
  };
  const id = 'ideas-lane-' + lane.lane;
  const note = Text.laneNote(lane);
  const shown = !compact || open;
  return (
    <section className="ideas-lane" aria-labelledby={id + '-h'}>
      <h2 className="ideas-lane-h" id={id + '-h'}>
        {compact ? (
          <button type="button" className="ideas-lane-toggle" aria-expanded={open} aria-controls={id} onClick={onToggle}>
            <span>{Text.laneTitle(lane)}</span>
            {note && <span className="ideas-lane-note">{note}</span>}
            <span className="ideas-chev" aria-hidden="true">{open ? '⌃' : '›'}</span>
          </button>
        ) : (
          <>
            <span>{Text.laneTitle(lane)}</span>
            {note && <span className="ideas-lane-note">{note}</span>}
          </>
        )}
      </h2>
      <div id={id} hidden={!shown}>
        {shown && <IdeasRows cards={cards} rowH={compact ? 44 : 36} label={lane.label} selected={selected} onOpen={onOpen} combos={combos} laneLabel={lane.label} />}
        {shown && next && (
          <div className="ideas-more">
            <button type="button" className="btn xs" disabled={more.busy} onClick={loadMore}>{more.busy ? 'Loading…' : Text.moreLabel(lane, cards.length)}</button>
            {more.error && <span role="alert" className="ideas-inline-error">Couldn't load more: {more.error}</span>}
          </div>
        )}
      </div>
    </section>
  );
}

// ---- States 3 and 4: a selected card ------------------------------------------------------------------------------------------------------

function IdeasThumb({ name, image, onZoom }) {
  const Text = window.VaultIdeas;
  if (!image || !image.normal) return <span className="ideas-thumb none" aria-hidden="true"></span>;
  return (
    <button type="button" className="ideas-thumb" onClick={() => onZoom({ name, normal: image.normal, artist: image.artist })} aria-label={`Enlarge the image of ${name}`}>
      <img src={Text.thumbUrl(image.normal)} width="40" height="56" loading="lazy" decoding="async" alt="" />
    </button>
  );
}

// One owned card that could stand in: what it does, what it does not, both Oracle texts, and what to do with it.
function IdeasAlt({ alt, t, name, deck, deckId, inDeck, onGo, openOwned, setZoom }) {
  const Text = window.VaultIdeas;
  const deckRoute = (swap) => ({ view: 'decks', deckId: String(deckId), ...(swap ? { swap } : {}) });
  const does = Text.altDoes(alt), lacks = Text.altLacks(alt, name), extra = Text.altExtra(alt), kind = Text.typeNote(alt, name);
  return (
    <li className={`ideas-alt tier-${alt.tier}`}>
      <IdeasThumb name={alt.card} image={alt.image} onZoom={setZoom} />
      <div className="ideas-alt-body">
        <p className="ideas-alt-name">
          <button type="button" className="row-link" onClick={() => openOwned(alt.card)}>{alt.card}</button>
          <span className="ideas-copies">{Text.ownedText(alt)}</span>
        </p>
        <p className="ideas-meta">{[alt.type_line, alt.mana_cost, Text.mv(alt.mana_value)].filter(Boolean).join(' · ')}</p>
        {does && <p className="ideas-does">{does}</p>}
        {lacks && <p className="ideas-lacks">{lacks}</p>}
        {extra && <p className="ideas-does">{extra}</p>}
        {kind && <p className="ideas-meta">{kind}</p>}
        <p className="ideas-why">{alt.why}</p>
        {Text.altBadges(alt).length > 0 && (
          <p className="ideas-badges">{Text.altBadges(alt).map((b) => <span key={b.kind} className={`ideas-badge ${b.kind === 'borrowed' ? 'borrowed' : ''}`}>{b.text}</span>)}</p>
        )}
        {alt.oracle_text && (
          <details className="ideas-texts">
            <summary>Oracle text of both cards</summary>
            <p className="ideas-text-name">{name}</p>
            <p className="ideas-text">{t.oracle_text}</p>
            <p className="ideas-text-name">{alt.card}</p>
            <p className="ideas-text">{alt.oracle_text}</p>
            <p className="ideas-credit">{Text.TEXT_CREDIT}</p>
          </details>
        )}
        <div className="ideas-actions">
          {!alt.borrowed && inDeck && (
            <a className="btn sm primary" href={Text.swapHash(deckId, [name], [alt.card])}
               onClick={ideasLink(() => onGo(deckRoute({ cut: [name], add: [alt.card] })))}>Swap into the deck<span className="ideas-sr">: {alt.card} for {name}</span></a>
          )}
          {!alt.borrowed && !inDeck && (
            <a className="btn sm primary" href={Text.swapHash(deckId, [], [alt.card])}
               onClick={ideasLink(() => onGo(deckRoute({ cut: [], add: [alt.card] })))}>Swap into the deck<span className="ideas-sr">: add {alt.card}</span></a>
          )}
          {alt.move && <IdeasMove move={alt.move} cardName={alt.card} toDeck={deck} cut={inDeck ? [name] : []} add={[alt.card]} onGo={onGo} />}
          {alt.borrowed && alt.buy && (
            <a className="btn sm" href={alt._links && alt._links.scryfall ? alt._links.scryfall.href : Text.scryfallSearch(alt.card)} target="_blank" rel="noopener noreferrer">
              {Text.buyLabel(alt.buy)}<span className="ideas-sr"> {alt.card} on Scryfall</span></a>
          )}
        </div>
        {alt.image && alt.image.artist && <p className="ideas-credit">Art: {alt.image.artist} · Scryfall</p>}
      </div>
    </li>
  );
}

function IdeasPanel({ deck, deckId, cardName, format, data, rowCard, onGo, onUp, openCard, setZoom }) {
  const Text = window.VaultIdeas;
  const q = useIdeasQuery(() => window.VaultApi.deckAlternatives(deckId, cardName, { format, limit: Text.ALT_PAGE }), [deckId, cardName, format, data.meta.version]);
  const a = q.data;
  const [rest, setRest] = useStateI({ items: [], next: undefined, busy: false, error: null });
  const [showSimilar, setShowSimilar] = useStateI(false);
  useEffectI(() => { setRest({ items: [], next: undefined, busy: false, error: null }); }, [a]);
  useEffectI(() => { setShowSimilar(false); }, [deckId, cardName, format]);
  const next = rest.next !== undefined ? rest.next : a && a._links && a._links.next ? a._links.next.href : null;
  const items = a ? a.items.concat(rest.items) : [];
  const more = async () => {
    if (!next || rest.busy) return;
    setRest((r) => ({ ...r, busy: true, error: null }));
    try {
      const page = await window.VaultApi.follow(next);
      const after = page._links && page._links.next && page.items.length ? page._links.next.href : null;
      setRest((r) => ({ items: r.items.concat(page.items), next: after === next ? null : after, busy: false, error: null }));
    } catch (e) {
      setRest((r) => ({ ...r, busy: false, error: Text.errorText(e) }));
    }
  };
  const t = a ? a.card : null;
  const name = t ? t.card : cardName;
  const status = t ? Text.targetStatus(t) : null;
  const deckRoute = (swap) => ({ view: 'decks', deckId: String(deckId), ...(swap ? { swap } : {}) });
  const inDeck = !!t && t.in_deck > 0;
  const openOwned = (n) => data.api.topPrinting(n).then((c) => c && openCard(c)).catch(() => {});
  const scryfall = a && a._links && a._links.scryfall ? a._links.scryfall.href : Text.scryfallSearch(name);
  const lacking = !!t && t.status != null && t.status !== 'owned';
  const toBuy = !!t && t.not_owned > 0;
  const noRole = !!a && a.reason === 'no_role';
  const counts = a && a.tiers ? a.tiers : { same_job: 0, similar: 0 };
  const same = Text.byTier(items, 'same_job'), similar = Text.byTier(items, 'similar');
  const noSameJob = !!a && !noRole && counts.same_job === 0;  // nothing does exactly this job: the message says so (and lists the similar ones, collapsed)
  const moreWanted = !!next && (same.length < counts.same_job || showSimilar);
  const altProps = { t, name, deck, deckId, inDeck, onGo, openOwned, setZoom };

  return (
    <section className="panel ideas-panel" aria-labelledby="ideas-panel-h">
      <div className="ideas-panel-top">
        <button type="button" className="btn sm" onClick={onUp} title="Back to the deck (Esc)">Back</button>
      </div>
      <div className="ideas-target">
        {t && <IdeasThumb name={name} image={t.image} onZoom={setZoom} />}
        <div className="ideas-target-body">
          <h2 className="ideas-panel-h" id="ideas-panel-h" tabIndex={-1}>{name}</h2>
          {t && (
            <p className="ideas-badges">
              {status && status.tone !== 'owned' && <span className={`ideas-badge ${status.tone}`}>{status.word}</span>}
              {status && status.tone === 'owned' && <span className="ideas-badge owned">{t.status == null ? 'not in the deck' : 'owned'}</span>}
              {toBuy && <span className="ideas-badge missing">not owned: {t.not_owned} to buy</span>}
              {t.held_by_other_deck > 0 && <span className="ideas-badge borrowed">borrowed from {t.borrowed_from || 'another deck'}</span>}
            </p>
          )}
          {t && <p className="ideas-meta">{[t.type_line, t.mana_cost, Text.mv(t.mana_value)].filter(Boolean).join(' · ')}</p>}
          {t && <p className="ideas-role">{Text.roleLine(t.roles)}</p>}
          {t && <p className="ideas-meta">{Text.allocationLine(t)}</p>}
        </div>
      </div>

      {q.error && !a && <IdeasFail what="the alternatives" error={q.error} retry={q.retry} />}
      {!a && !q.error && <IdeasLoading what="what you own that could stand in" />}

      {a && lacking && rowCard && rowCard.move && (
        <div className="ideas-actions">
          {rowCard && rowCard.move && <IdeasMove move={rowCard.move} cardName={name} toDeck={deck} cut={[]} add={[name]} onGo={onGo} />}
        </div>
      )}

      {noRole && (
        <div className="ideas-none ideas-norole" role="status">
          <p className="ideas-none-text">{a.message || Text.NO_ROLE_NOTE}</p>
          {t && t.oracle_text && (
            <details className="ideas-texts" open>
              <summary>Oracle text</summary>
              <p className="ideas-text">{t.oracle_text}</p>
              <p className="ideas-credit">{Text.TEXT_CREDIT}</p>
            </details>
          )}
          <div className="ideas-actions">
            <a className="btn" href={scryfall} target="_blank" rel="noopener noreferrer">Open on Scryfall ↗</a>
            {lacking && t.buy && <a className="btn" href={scryfall} target="_blank" rel="noopener noreferrer">{Text.buyLabel(t.buy)}<span className="ideas-sr"> on Scryfall</span></a>}
          </div>
        </div>
      )}

      {a && !noRole && counts.same_job > 0 && (
        <>
          <h3 className="ideas-h3">{Text.tierHeading('same_job', counts.same_job)}</h3>
          <ul className="ideas-alts" aria-label={Text.TIERS.same_job}>
            {same.map((alt) => <IdeasAlt key={alt.oracle_id} alt={alt} {...altProps} />)}
          </ul>
        </>
      )}

      {a && !noRole && noSameJob && (
        <div className="ideas-none" role="status">
          <p className="ideas-none-text">{a.message || 'You own nothing else that does this job in this deck\'s colours and format.'}</p>
          {a.filtered_note && <p className="ideas-meta">{a.filtered_note}</p>}
          <div className="ideas-actions">
            {lacking && t.buy && <a className="btn primary" href={scryfall} target="_blank" rel="noopener noreferrer">{Text.buyLabel(t.buy).replace(/^Buy /, 'Buy for ')}<span className="ideas-sr"> on Scryfall</span></a>}
            <a className="btn" href={scryfall} target="_blank" rel="noopener noreferrer">Open on Scryfall ↗</a>
            <a className="btn" href={`#/decks/${encodeURIComponent(deckId)}`} onClick={ideasLink(() => onGo(deckRoute()))}>See upgrades on the deck page</a>
          </div>
        </div>
      )}

      {a && !noRole && counts.similar > 0 && (
        <div className="ideas-similar">
          <button type="button" className="btn sm" aria-expanded={showSimilar} aria-controls="ideas-similar-list" onClick={() => setShowSimilar(!showSimilar)}>
            {Text.similarToggle(counts.similar, showSimilar)}
          </button>
          {showSimilar && (
            <ul id="ideas-similar-list" className="ideas-alts" aria-label={Text.TIERS.similar}>
              {similar.map((alt) => <IdeasAlt key={alt.oracle_id} alt={alt} {...altProps} />)}
            </ul>
          )}
        </div>
      )}

      {a && !noRole && items.length > 0 && (
        <>
          {moreWanted && (
            <div className="ideas-more">
              <button type="button" className="btn xs" disabled={rest.busy} onClick={more}>{rest.busy ? 'Loading…' : 'Show more alternatives'}</button>
              {rest.error && <span role="alert" className="ideas-inline-error">Couldn't load more: {rest.error}</span>}
            </div>
          )}
          {!noSameJob && a.filtered_note && <p className="ideas-meta">{a.filtered_note}</p>}
          {lacking && t.buy && !noSameJob && (
            <p className="ideas-or">Or buy {name}: {Text.buyPlain(t.buy)}. <a href={scryfall} target="_blank" rel="noopener noreferrer">Open on Scryfall ↗</a>{' '}
              <a href="#/lab" onClick={ideasLink(() => onGo({ view: 'lab' }))}>Your buy list in the Lab</a></p>
          )}
        </>
      )}

      {a && (
        <div className="ideas-fine-block">
          <p className="ideas-fine"><strong>{Text.ROLES_NOTE}</strong> Format: {a.format}{a.format_from === 'default' ? ' (the default; change it above)' : ''}; colours: {(a.color_identity || []).join('') || 'colourless'}; roles version {a.roles_version}.</p>
          <details className="ideas-fine">
            <summary>About these roles and prices</summary>
            <p>{a.roles_note}</p>
            <p>{Text.PRICE_NOTE(a.prices_date)} Card data is Scryfall's; the roles are the Vault's.</p>
          </details>
          {t && t.image && t.image.artist && <p className="ideas-credit">Art: {t.image.artist} · image via Scryfall · © Wizards of the Coast</p>}
        </div>
      )}
    </section>
  );
}

// Move: the lab proposes, the deck pages do the work. It names the two steps and opens the deck that gives the card up, then this one.
function IdeasMove({ move, cardName, toDeck, cut, add, onGo }) {
  const Text = window.VaultIdeas;
  const [open, setOpen] = useStateI(false);
  const id = 'ideas-move-' + cardName.replace(/[^A-Za-z0-9]+/g, '-');
  return (
    <>
      <button type="button" className="btn sm" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        Move<span className="ideas-sr"> {cardName} from {move.from_deck.name}</span>
      </button>
      {open && (
        <div id={id} className="ideas-move">
          <p>{Text.moveSteps(move, cardName, toDeck.name)}</p>
          <div className="ideas-actions">
            <a className="btn xs" href={Text.swapHash(move.from_deck.id, [cardName], [])}
               onClick={ideasLink(() => onGo({ view: 'decks', deckId: String(move.from_deck.id), swap: { cut: [cardName], add: [] } }))}>1. Open {move.from_deck.name}</a>
            <a className="btn xs" href={Text.swapHash(toDeck.id, cut, add)}
               onClick={ideasLink(() => onGo({ view: 'decks', deckId: String(toDeck.id), swap: { cut, add } }))}>2. Open {toDeck.name}</a>
          </div>
        </div>
      )}
    </>
  );
}

// The larger image opens only when the thumbnail is tapped; it carries the artist and Scryfall credit.
function IdeasZoom({ card, onClose }) {
  const box = useRefI(null);
  window.useDialogFocus(box);
  return (
    <div className="ideas-zoom-backdrop" onClick={onClose}>
      <div className="panel ideas-zoom" role="dialog" aria-modal="true" aria-label={card.name} ref={box} tabIndex={-1} onClick={(e) => e.stopPropagation()}>
        <button type="button" className="btn xs ghost close-x" data-autofocus onClick={onClose} aria-label="Close the image" title="Close (Esc)"><window.CloseIcon /></button>
        <img src={card.normal} alt={window.VaultIdeas.thumbAlt(card.name, card.artist)} width="320" height="446" decoding="async" />
        <p className="ideas-credit">{card.artist ? <>Illustrated by <strong>{card.artist}</strong> · </> : null}image via <a href="https://scryfall.com" target="_blank" rel="noopener noreferrer">Scryfall</a> · © Wizards of the Coast</p>
      </div>
    </div>
  );
}

window.Ideas = Ideas;
