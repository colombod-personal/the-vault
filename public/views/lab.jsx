// The Lab (#164, docs/lab-design.md): a page for decisions about buying and selling. Every number is the server's: what the decks
// still need (GET /decks/overlap and its purchases), the copies beyond the saved decks (GET /collection/spare), the winners and
// losers (GET /collection/pnl) and the value over time (GET /collection/history, with its market-only summary). The words and
// the formatting are public/lib/lab.js (window.VaultLab); this file lays them out. Own account only: a shared collection has no Lab.
const { useEffect: useEffectLab, useMemo: useMemoLab, useRef: useRefLab, useState: useStateLab } = React;

const LAB_PAGE = 20;     // rows asked for at a time in a list
const LAB_PNL_PAGE = 10;

// A cursor-paged list: `head` is its first page (as the caller read it), `more()` follows the server's `next` link. The pages after
// the first are dropped when a new first page arrives (a changed collection).
function useLabMore(head) {
  const [rest, setRest] = useStateLab({ items: [], next: undefined, busy: false, error: null });
  const live = useRefLab(head);
  live.current = head;
  useEffectLab(() => { setRest({ items: [], next: undefined, busy: false, error: null }); }, [head]);
  const next = rest.next !== undefined ? rest.next : head && head._links && head._links.next ? head._links.next.href : null;
  const more = async () => {
    if (!next || rest.busy) return;
    const started = head;
    setRest((r) => ({ ...r, busy: true, error: null }));
    try {
      const page = await window.VaultApi.follow(next);
      if (live.current !== started) return;
      const after = page._links && page._links.next && page.items.length ? page._links.next.href : null;
      setRest((r) => ({ items: r.items.concat(page.items), next: after === next ? null : after, busy: false, error: null }));
    } catch (e) {
      if (live.current === started) setRest((r) => ({ ...r, busy: false, error: labError(e) }));
    }
  };
  return { items: head ? head.items.concat(rest.items) : [], total: head ? head.total : 0, next, more, busy: rest.busy, error: rest.error };
}

// A query that can be asked again from its error message.
function useLabQuery(load, deps) {
  const [tick, setTick] = useStateLab(0);
  const q = window.useVaultQuery(load, [...deps, tick]);
  return { ...q, retry: () => setTick((t) => t + 1) };
}

const labError = (e) => (e && e.status === 0 ? "The Vault couldn't be reached. Check your connection." : (e && e.message) || 'Unknown error');

function labJump(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
  const heading = el.querySelector('h2, h3');
  if (heading) heading.focus({ preventScroll: true });
}

// Copy what `read()` resolves to. The list is fetched after the tap, so the clipboard is given the promise (Safari and Chrome keep
// the tap's permission for it); resolves to the text, or null when the browser refused and the person must copy it by hand.
async function labCopy(read) {
  if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
    try {
      let text = '';
      const blob = read().then((t) => { text = t; return new Blob([t], { type: 'text/plain' }); });
      await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })]);
      return text;
    } catch (e) { if (e && e.status !== undefined) throw e; /* refused: try the older way below */ }
  }
  const text = await read();
  try { await navigator.clipboard.writeText(text); return text; } catch { /* needs a secure page and permission */ }
  return null;
}

function LabFail({ what, error, retry }) {
  return (
    <div className="lab-fail" role="alert">
      <span>Couldn't load {what}: {labError(error)}</span>
      <button type="button" className="btn xs" onClick={retry}>Try again</button>
    </div>
  );
}

function LabLoading({ what }) {
  return <p className="lab-loading" role="status"><span className="spinner spinner-xs"></span> Reading {what}…</p>;
}

function LabMore({ list, noun }) {
  if (!list.next) return null;
  const left = Math.max(0, list.total - list.items.length);
  return (
    <div className="lab-more">
      <button type="button" className="btn sm" disabled={list.busy} onClick={list.more}>
        {list.busy ? 'Loading…' : left ? `Show ${Math.min(left, LAB_PAGE).toLocaleString()} more ${noun}` : `Show more ${noun}`}
      </button>
      {list.error && <span role="alert" className="lab-inline-error">Couldn't load more: {list.error}</span>}
    </div>
  );
}

// ---- The page -----------------------------------------------------------------------------------------------------------------

function Lab({ data, openCard, readOnly, onImported, onOpenDeck, onAddDeck, onRefresh, refreshing }) {
  if (readOnly) return <LabShared />;
  if (!data.meta.totalQty) return <LabEmpty onImported={onImported} />;
  if (data.meta.offline) return <LabOffline />;
  return <LabPage data={data} openCard={openCard} onOpenDeck={onOpenDeck} onAddDeck={onAddDeck} onRefresh={onRefresh} refreshing={refreshing} />;
}

function LabTitle({ children }) {
  return (
    <div className="lab-head-title">
      <p className="eyebrow">Buy, sell or keep</p>
      <h1 className="h1">Lab</h1>
      {children}
    </div>
  );
}

// Nothing imported: the page is one panel.
function LabEmpty({ onImported }) {
  return (
    <div data-screen-label="05 Lab" className="lab">
      <div className="lab-head"><LabTitle /></div>
      <div className="panel lab-empty">
        <p className="lab-empty-text">Import your collection to see what to buy or sell.</p>
        <window.ImportButton className="btn primary" label="Import a file" onImported={onImported} />
      </div>
    </div>
  );
}

// A shared collection has no Lab: it is built from your own saved decks and from what you paid.
function LabShared() {
  return (
    <div data-screen-label="05 Lab" className="lab">
      <div className="lab-head"><LabTitle /></div>
      <div className="panel lab-empty">
        <p className="lab-empty-text">A shared collection has no Lab.</p>
        <p className="muted">The Lab compares your own saved decks with your own copies and looks at what you paid, so it belongs to your own vault. Use “Back to my vault” above to return to it; the Vault overview and Browse show the shared collection.</p>
      </div>
    </div>
  );
}

function LabOffline() {
  return (
    <div data-screen-label="05 Lab" className="lab">
      <div className="lab-head"><LabTitle /></div>
      <div className="panel lab-empty" role="alert">
        <p className="lab-empty-text">The Lab needs a connection.</p>
        <p className="muted">It asks the Vault's server for your decks, spare copies and prices, and keeps no copy of them on this device.</p>
        <button type="button" className="btn" onClick={() => window.location.reload()}>Try again</button>
      </div>
    </div>
  );
}

function LabPage({ data, openCard, onOpenDeck, onAddDeck, onRefresh, refreshing }) {
  const api = data.api;
  const m = data.meta;
  const Text = window.VaultLab;
  const at = [api.base, m.version];
  const [side, setSide] = useStateLab('winners');

  const overlap = useLabQuery(() => window.VaultApi.overlap(), at);
  const spare = useLabQuery(() => api.spare(LAB_PAGE), at);
  const winners = useLabQuery(() => api.pnl('winners', LAB_PNL_PAGE), at);
  const losers = useLabQuery(() => api.pnl('losers', LAB_PNL_PAGE), at);
  const history = useLabQuery(() => api.historySince(new Date(Date.now() - 365 * 86400000).toISOString().slice(0, 10)), at);

  const asOf = m.pricesAsOf;
  const stale = Text.isStale(asOf);
  const dated = stale ? Text.shortDate(asOf) : null;  // every price and total carries its date once the prices are stale

  const counters = [
    { key: 'buy', section: 'Buy', target: 'lab-buy', query: overlap, counter: Text.buyCounter(overlap.data, dated) },
    { key: 'sell', section: 'Spare copies', target: 'lab-spare', query: spare, counter: Text.sellCounter(spare.data, dated) },
    { key: 'pnl', section: 'Profit and loss', target: 'lab-pnl', query: winners, counter: Text.pnlCounter(winners.data, dated) },
  ];
  const action = Text.chartAction(winners.data, spare.data && spare.data.status);
  const onAction = (kind) => {
    if (kind === 'losers') setSide('losers');
    labJump(kind === 'losers' ? 'lab-pnl' : 'lab-spare');
  };

  return (
    <div data-screen-label="05 Lab" className="lab">
      <div className="lab-head">
        <LabTitle><p className="lab-lede">What should I buy, sell or keep?</p></LabTitle>
        <div className="lab-head-side">
          <p className={`label-mono lab-prices ${stale ? 'stale' : ''}`}>{Text.pricesLine(asOf)}</p>
          {stale && onRefresh && <window.RefreshButton refreshing={refreshing} onRefresh={onRefresh} />}
          <a className="btn sm" href={api.base + '/export.csv'} download>Export collection</a>
        </div>
      </div>

      <div className="lab-counters" role="group" aria-label="What to decide, at a glance">
        {counters.map(({ key, section, target, query, counter }) => (
          <button key={key} type="button" className={`lab-counter ${counter && counter.quiet ? 'quiet' : ''}`} onClick={() => labJump(target)}>
            {counter ? (
              <>
                <span className="lab-counter-text">
                  <span className="lab-counter-head">{counter.headline}</span>
                  <span className="lab-counter-detail">{counter.detail}</span>
                </span>
                <span className="lab-counter-go" aria-hidden="true">›</span>
                <span className="lab-sr">. Go to {section}.</span>
              </>
            ) : (
              <>
                <span className="lab-counter-text">
                  <span className="lab-counter-head">{query.error ? 'Not available' : 'Reading…'}</span>
                  <span className="lab-counter-detail">{section}</span>
                </span>
                <span className="lab-counter-go" aria-hidden="true">›</span>
              </>
            )}
          </button>
        ))}
      </div>

      <div className="lab-cols">
        <LabBuy overlap={overlap} dated={dated} api={api} openCard={openCard} onOpenDeck={onOpenDeck} onAddDeck={onAddDeck} />
        <section id="lab-sell" className="lab-block" aria-labelledby="lab-sell-h">
          <h2 id="lab-sell-h" className="h2" tabIndex={-1}>Sell or hold</h2>
          <LabSpare spare={spare} dated={dated} api={api} openCard={openCard} onAddDeck={onAddDeck} />
          <LabPnl winners={winners} losers={losers} side={side} setSide={setSide} api={api} openCard={openCard} />
        </section>
      </div>

      <LabValue history={history} action={action} onAction={onAction} />
    </div>
  );
}

// ---- Buy: can every deck stand on its own? -----------------------------------------------------------------------------------

function LabBuy({ overlap, dated, api, openCard, onOpenDeck, onAddDeck }) {
  const Text = window.VaultLab;
  const o = overlap.data;
  const wantsList = !!o && o.summary.cards_to_buy > 0;
  const purchases = useLabQuery(() => (wantsList ? window.VaultApi.overlapPurchases(LAB_PAGE) : null), [o]);
  const buys = useLabMore(purchases.data);
  const deckHead = useMemoLab(() => (o ? { items: o.decks, total: o.decks_analysed + o.decks_skipped_count, _links: { next: o._links.next } } : null), [o]);
  const decks = useLabMore(deckHead);
  const [showDecks, setShowDecks] = useStateLab(null);  // null: open when something is to buy, folded when every deck stands alone
  const [copy, setCopy] = useStateLab({ state: 'idle' });

  const copyList = async () => {
    setCopy({ state: 'busy' });
    try {
      const copied = await labCopy(() => window.VaultApi.overlapText());
      if (copied === null) setCopy({ state: 'manual', text: await window.VaultApi.overlapText() });
      else setCopy({ state: 'done' });
    } catch (e) {
      setCopy({ state: 'error', message: labError(e) });
    }
  };
  const openOwned = (name) => api.topPrinting(name).then((c) => c && openCard(c)).catch(() => {});
  const head = Text.buyHeadline(o);
  const decksOpen = showDecks == null ? !!o && o.summary.cards_to_buy > 0 : showDecks;

  return (
    <section id="lab-buy" className="lab-block" aria-labelledby="lab-buy-h">
      <h2 id="lab-buy-h" className="h2" tabIndex={-1}>Buy</h2>
      <p className="lab-sub">Can every deck stand on its own?</p>
      {overlap.error && !o && <LabFail what="your decks" error={overlap.error} retry={overlap.retry} />}
      {!o && !overlap.error && <LabLoading what="your decks" />}
      {o && head.kind === 'no_decks' && (
        <div className="panel lab-note">
          <p>{head.text}</p>
          <button type="button" className="btn" onClick={onAddDeck}>Add a deck</button>
        </div>
      )}
      {o && head.kind !== 'no_decks' && (
        <>
          <p className="lab-line" role="status">{head.text}</p>
          {o.summary.cards_to_buy > 0 && (
            <>
              <p className="lab-line muted">
                Finishing every deck costs {Text.money(o.summary.finish_all_cost)}{dated ? ` (${dated})` : ''}, each card counted once
                {o.summary.unpriced ? `; ${Text.plural(o.summary.unpriced, 'card has', 'cards have')} no price` : ''}.
                {' '}Prices are Scryfall's cheapest{o.prices_date ? `, from ${Text.shortDate(o.prices_date)}` : ''}; the Vault contacts no shop.
              </p>
              <h3 className="lab-h3">Cards to buy, cheapest first</h3>
              {purchases.error && !purchases.data && <LabFail what="the cards to buy" error={purchases.error} retry={purchases.retry} />}
              {!purchases.data && !purchases.error && <LabLoading what="the cards to buy" />}
              {purchases.data && (
                <ul className="lab-list" aria-label="Cards to buy, cheapest first">
                  {buys.items.map((p) => (
                    <li key={p.card} className="lab-item">
                      <div className="lab-item-main">
                        {p.have > 0
                          ? <button type="button" className="row-link lab-name" onClick={() => openOwned(p.card)}>{p.card}</button>
                          : <a className="row-link lab-name" href={Text.scryfallSearch(p.card)} target="_blank" rel="noopener noreferrer">{p.card}</a>}
                        {p.contested && <span className="lab-badge">{p.move ? 'move possible' : 'contested'}</span>}
                      </div>
                      <p className="lab-item-sub">{Text.purchaseOwnership(p)}</p>
                      <div className="lab-opts">
                        {p.move && <span className="lab-opt">move: {p.move.from_deck.name} to {p.move.to_deck.name}</span>}
                        <span className="lab-opt buy">{Text.purchaseBuy(p, dated)}</span>
                      </div>
                      <BuyMenu card={p.card} />
                      {p.move && <p className="lab-item-note">{p.move.effect}</p>}
                    </li>
                  ))}
                </ul>
              )}
              {purchases.data && <LabMore list={buys} noun="cards" />}
              <div className="lab-copy">
                <button type="button" className="btn primary" disabled={copy.state === 'busy'} onClick={copyList}>
                  {copy.state === 'busy' ? 'Copying…' : 'Copy shopping list'}
                </button>
                <span className="lab-copy-note" role="status">
                  {copy.state === 'done' && `Copied ${Text.plural(o.summary.cards_to_buy, 'card', 'cards')} to the clipboard.`}
                </span>
                {copy.state === 'error' && <span role="alert" className="lab-inline-error">{copy.message}</span>}
              </div>
              {copy.state === 'manual' && (
                <div className="lab-manual">
                  <label htmlFor="lab-list-text">Your browser did not allow copying. Select this list and copy it yourself:</label>
                  <textarea id="lab-list-text" className="input" readOnly rows={Math.min(10, Math.max(3, copy.text.split('\n').length))} value={copy.text}
                            onFocus={(e) => e.target.select()} />
                </div>
              )}
            </>
          )}

          <h3 className="lab-h3">Your decks</h3>
          {o.summary.cards_to_buy === 0 && (
            <button type="button" className="btn sm" aria-expanded={decksOpen} aria-controls="lab-decks" onClick={() => setShowDecks(!decksOpen)}>
              {decksOpen ? 'Hide the decks' : `Show the ${Text.plural(decks.total, 'deck', 'decks')}`}
            </button>
          )}
          {decksOpen && (
            <>
              <ul id="lab-decks" className="lab-list" aria-label="Your decks">
                {decks.items.map((d) => <LabDeck key={d.id} deck={d} dated={dated} onOpenDeck={onOpenDeck} />)}
              </ul>
              <LabMore list={decks} noun="decks" />
            </>
          )}
        </>
      )}
    </section>
  );
}

// One deck: complete, or what it lacks and what finishing it costs; opens to the cards it holds and lacks.
function LabDeck({ deck, dated, onOpenDeck }) {
  const Text = window.VaultLab;
  const [open, setOpen] = useStateLab(false);
  const status = Text.deckStatus(deck);
  const cost = Text.deckCost(deck, dated);
  const detailed = deck.status !== 'skipped' && (deck.lacking.length > 0 || deck.holds.length > 0);
  const id = `lab-deck-${deck.id}`;
  const lackMore = deck.lacking_total != null ? deck.lacking_total - deck.lacking.length : 0;
  const holdMore = deck.holds_total != null ? deck.holds_total - deck.holds.length : 0;
  return (
    <li className="lab-item lab-deck">
      <div className="lab-deck-row">
        <a className="row-link lab-name" href={`#/decks/${encodeURIComponent(deck.id)}`}
           onClick={(e) => { e.preventDefault(); onOpenDeck({ id: deck.id }); }}>{deck.name}</a>
        <span className={`lab-status ${status.tone}`}>{status.tone === 'ok' && <span aria-hidden="true">✓ </span>}{status.text}</span>
        {cost && <span className="lab-fig gold">{cost}</span>}
        {detailed && (
          <button type="button" className="btn xs lab-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
            {open ? 'Hide cards' : 'Show cards'}<span className="lab-sr"> of {deck.name}</span>
          </button>
        )}
      </div>
      {deck.status === 'skipped' && <p className="lab-item-note">{deck.reason}</p>}
      {open && (
        <div id={id} className="lab-deck-detail">
          {deck.lacking.length > 0 && (
            <>
              <p className="lab-mini">Lacks</p>
              <ul>
                {deck.lacking.map((l) => (
                  <li key={'l' + l.card}>
                    {l.quantity} × {l.card}: {Text.lackingWords(l)}
                    {l.cost != null ? `, ${Text.money(l.cost)}${dated ? ` (${dated})` : ''}` : ', no price known'}
                  </li>
                ))}
                {lackMore > 0 && <li className="muted">and {lackMore.toLocaleString()} more</li>}
              </ul>
            </>
          )}
          {deck.holds.length > 0 && (
            <>
              <p className="lab-mini">Holds cards other decks want too</p>
              <ul>
                {deck.holds.map((h) => (
                  <li key={'h' + h.card}>
                    {h.quantity} × {h.card}, also wanted by {h.also_wanted_by.join(', ')}{h.also_wanted_by_total > h.also_wanted_by.length ? ` and ${h.also_wanted_by_total - h.also_wanted_by.length} more` : ''}
                  </li>
                ))}
                {holdMore > 0 && <li className="muted">and {holdMore.toLocaleString()} more</li>}
              </ul>
            </>
          )}
        </div>
      )}
    </li>
  );
}

// ---- Sell or hold, 2a: spare copies -------------------------------------------------------------------------------------------

function LabSpare({ spare, dated, api, openCard, onAddDeck }) {
  const Text = window.VaultLab;
  const d = spare.data;
  const list = useLabMore(d);
  const valueLine = d ? Text.spareValueLine(d.summary, dated) : null;
  return (
    <div id="lab-spare" className="lab-sub-block">
      <h3 id="lab-spare-h" className="lab-h3 big" tabIndex={-1}>Spare copies <span className="lab-h3-note">beyond your saved decks</span></h3>
      {spare.error && !d && <LabFail what="your spare copies" error={spare.error} retry={spare.retry} />}
      {!d && !spare.error && <LabLoading what="your spare copies" />}
      {d && d.status === 'no_decks' && (
        <div className="panel lab-note">
          <p>{d.note}</p>
          <button type="button" className="btn" onClick={onAddDeck}>Add a deck</button>
        </div>
      )}
      {d && d.status === 'ok' && (
        <>
          {d.summary.names === 0
            ? <p className="lab-line">No copy is spare: your saved decks need every copy you own (basic lands left out).</p>
            : <p className="lab-line" role="status">{Text.plural(d.summary.copies, 'copy', 'copies')} of {Text.plural(d.summary.names, 'card', 'cards')} are spare, worth {valueLine}.</p>}
          {d.decks_skipped > 0 && (
            <p className="lab-line warn">{Text.plural(d.decks_skipped, 'saved deck', 'saved decks')} could not be read, so {d.decks_skipped === 1 ? 'its cards are' : 'their cards are'} not counted as needed.</p>
          )}
          {d.summary.names > 0 && (
            <>
              <div className="lab-colhead" aria-hidden="true">
                <span>Card</span><span>Spare</span><span>Value</span><span>Used by</span>
              </div>
              <ul className="lab-list" aria-label="Spare copies, most valuable first">
                {list.items.map((item) => <LabSpareRow key={item.name} item={item} dated={dated} api={api} openCard={openCard} />)}
              </ul>
              <LabMore list={list} noun="cards" />
            </>
          )}
          <p className="lab-fine">{d.note}</p>
        </>
      )}
    </div>
  );
}

function LabSpareRow({ item, dated, api, openCard }) {
  const Text = window.VaultLab;
  const [open, setOpen] = useStateLab(false);
  const fig = Text.spareFigures(item, dated);
  const id = 'lab-copies-' + item.name.replace(/[^A-Za-z0-9]+/g, '-');
  const openName = () => {
    const first = item.printings[0];
    return (first && first._links && first._links.self ? api.card(first._links.self.href) : api.topPrinting(item.name))
      .then((c) => c && openCard(c)).catch(() => {});
  };
  return (
    <li className="lab-item lab-spare">
      <div className="lab-spare-row">
        <button type="button" className="row-link lab-name" onClick={openName}>{item.name}</button>
        <span className="lab-fig strong"><span className="lab-fig-l">spare </span>{item.spare}<span className="lab-sr"> spare</span></span>
        <span className="lab-fig gold">{fig.value}</span>
        <span className="lab-fig muted">{fig.decks}</span>
      </div>
      <p className="lab-item-sub">you own {item.have}, your decks need {item.needed}</p>
      <button type="button" className="btn xs lab-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        {open ? 'Hide the copies' : `Which copies (${Text.plural(item.printing_rows_total, 'printing', 'printings')})`}<span className="lab-sr"> of {item.name}</span>
      </button>
      {open && <LabPrintings id={id} item={item} dated={dated} api={api} openCard={openCard} />}
    </li>
  );
}

// The spare printings of one card: the server's preview of ten, and every one of them on request (its own cursor).
function LabPrintings({ id, item, dated, api, openCard }) {
  const Text = window.VaultLab;
  const [full, setFull] = useStateLab(null);
  const [state, setState] = useStateLab({ busy: false, error: null });
  const head = useMemoLab(() => full || { items: item.printings, total: item.printing_rows_total, _links: {} }, [full, item]);
  const list = useLabMore(head);
  const loadAll = async () => {
    setState({ busy: true, error: null });
    try {
      setFull(await api.follow(item._links.printings.href));
      setState({ busy: false, error: null });
    } catch (e) {
      setState({ busy: false, error: labError(e) });
    }
  };
  const hidden = item.printing_rows_total - list.items.length;
  return (
    <div id={id} className="lab-printings">
      <ul>
        {list.items.map((p) => (
          <li key={p.id}>
            <span className="lab-printing-name">
              <button type="button" className="row-link" onClick={() => api.card(p._links.self.href).then((c) => c && openCard(c)).catch(() => {})}>
                {Text.printingTitle(p)}
              </button>
              <span className="muted"> {Text.printingDetail(p)}</span>
            </span>
            <span>{p.spare_quantity} spare</span>
            <span className="gold">{p.unit_price == null ? 'no price' : `${Text.money(p.unit_price)} each${dated ? ` (${dated})` : ''}`}</span>
            <span className="muted">{p.trade_marked_quantity ? `${p.trade_marked_quantity} marked for trade` : ''}</span>
            <a className="lab-link" href={p.scryfall_link || p.scryfall_search} target="_blank" rel="noopener noreferrer">
              {p.scryfall_link ? 'Scryfall' : 'Search Scryfall'}<span className="lab-sr"> for {item.name} ({Text.printingTitle(p)})</span> ↗
            </a>
          </li>
        ))}
      </ul>
      {hidden > 0 && !full && (
        <div className="lab-more">
          <button type="button" className="btn xs" disabled={state.busy} onClick={loadAll}>{state.busy ? 'Loading…' : `Show all ${item.printing_rows_total.toLocaleString()} printings`}</button>
          {state.error && <span role="alert" className="lab-inline-error">{state.error}</span>}
        </div>
      )}
      {full && <LabMore list={list} noun="printings" />}
    </div>
  );
}

// ---- Sell or hold, 2b: profit and loss ------------------------------------------------------------------------------------------

function LabPnl({ winners, losers, side, setSide, api, openCard }) {
  const Text = window.VaultLab;
  const sides = ['winners', 'losers'];
  const queries = { winners, losers };
  const summary = winners.data ? winners.data.summary : null;
  const hidden = summary ? Text.pnlHidden(summary) : null;
  const onKey = (e) => {
    const at = sides.indexOf(side);
    const to = e.key === 'ArrowRight' || e.key === 'ArrowLeft' ? sides[1 - at] : e.key === 'Home' ? sides[0] : e.key === 'End' ? sides[1] : null;
    if (!to) return;
    e.preventDefault();
    setSide(to);
    const tab = document.getElementById('lab-tab-' + to);
    if (tab) tab.focus();
  };
  return (
    <div id="lab-pnl" className="lab-sub-block">
      <h3 id="lab-pnl-h" className="lab-h3 big" tabIndex={-1}>Profit and loss</h3>
      {winners.error && !winners.data && <LabFail what="your profit and loss" error={winners.error} retry={winners.retry} />}
      {!winners.data && !winners.error && <LabLoading what="your profit and loss" />}
      {summary && hidden && <p className="lab-line">{hidden}</p>}
      {summary && !hidden && (
        <>
          <p className="lab-line">{Text.coverageLine(summary)} {Text.netLine(summary.net_gain)}</p>
          <div className="lab-tabs" role="tablist" aria-label="Profit and loss" onKeyDown={onKey}>
            {sides.map((s) => (
              <button key={s} type="button" role="tab" id={'lab-tab-' + s} aria-selected={side === s} aria-controls={'lab-panel-' + s}
                      tabIndex={side === s ? 0 : -1} className={`chip ${side === s ? 'active' : ''}`} onClick={() => setSide(s)}>
                {s === 'winners' ? 'Winners' : 'Losers'}{queries[s].data ? ` (${queries[s].data.total.toLocaleString()})` : ''}
              </button>
            ))}
          </div>
          {sides.map((s) => (
            <div key={s} role="tabpanel" id={'lab-panel-' + s} aria-labelledby={'lab-tab-' + s} hidden={side !== s}>
              <LabPnlPanel side={s} query={queries[s]} api={api} openCard={openCard} />
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function LabPnlPanel({ side, query, api, openCard }) {
  const Text = window.VaultLab;
  const list = useLabMore(query.data);
  if (query.error && !query.data) return <LabFail what={`the ${side}`} error={query.error} retry={query.retry} />;
  if (!query.data) return <LabLoading what={`the ${side}`} />;
  if (query.data.total === 0) return <p className="lab-line">{Text.pnlEmpty(side)}</p>;
  return (
    <>
      <div className="lab-colhead pnl" aria-hidden="true"><span>Card</span><span>Paid</span><span>Now</span><span>Gain</span></div>
      <ul className="lab-list" aria-label={side === 'winners' ? 'Winners, most profitable first' : 'Losers, biggest loss first'}>
        {list.items.map((p) => (
          <li key={p.id} className="lab-item lab-pnl-row">
            <div className="lab-pnl-main">
              <button type="button" className="row-link lab-name" onClick={() => api.card(p._links.self.href).then((c) => c && openCard(c)).catch(() => {})}>{p.name}</button>
              <span className="lab-item-sub">{p.set.code.toUpperCase()} #{p.collector_number} · {p.printing} · {Text.plural(p.copies, 'copy', 'copies')}</span>
            </div>
            <span className="lab-fig"><span className="lab-fig-l">paid </span>{Text.money(p.paid)}</span>
            <span className="lab-fig gold"><span className="lab-fig-l">now </span>{Text.money(p.market_value)}</span>
            <span className={`lab-fig strong ${p.gain < 0 ? 'bad' : 'good'}`}>
              <span className="lab-fig-l">gain </span>{Text.signed(p.gain)}{p.gain_pct != null && <span className="lab-pct"> {Text.percent(p.gain_pct)}</span>}
            </span>
            <a className="lab-link" href={p.scryfall_link || p.scryfall_search} target="_blank" rel="noopener noreferrer">
              {p.scryfall_link ? 'Scryfall' : 'Search Scryfall'}<span className="lab-sr"> for {p.name}</span> ↗
            </a>
          </li>
        ))}
      </ul>
      <LabMore list={list} noun={side} />
    </>
  );
}

// ---- Sell or hold, 2c: value over time (the context for 2a and 2b) -------------------------------------------------------------

function LabValue({ history, action, onAction }) {
  const Text = window.VaultLab;
  const d = history.data;
  // The market line only: history's value covers every holding while the cost exists for the copies with a price paid, so
  // drawing them together would mix two groups (the profit and loss list above compares one).
  const days = useMemoLab(() => (d ? d.items.map((x) => ({ ...x, cost: null })) : []), [d]);
  const line = d ? Text.historyLine(d.summary) : null;
  return (
    <section id="lab-value" className="lab-block" aria-labelledby="lab-value-h">
      <h2 id="lab-value-h" className="h2" tabIndex={-1}>Value over time</h2>
      <p className="lab-sub">Sell or hold: is now a good moment?</p>
      {history.error && !d && <LabFail what="the value history" error={history.error} retry={history.retry} />}
      {!d && !history.error && <LabLoading what="the value history" />}
      {d && days.length === 0 && <p className="lab-line">No daily values yet. They start with your first import and grow by one a day.</p>}
      {d && days.length > 0 && (
        <>
          <div className="lab-value-head">
            <p className="lab-line" role="status">{line || 'Value history starts today.'}</p>
            {action && <button type="button" className="btn sm" onClick={() => onAction(action.kind)}>{action.label}</button>}
          </div>
          {days.length > 1
            ? <div className="panel lab-chart">{window.DailyChart ? <window.DailyChart days={days} /> : null}</div>
            : <p className="lab-line">The chart starts when a second day is recorded.</p>}
          <p className="lab-fine">Market value at each day's Scryfall prices, the last year at most. Marked days are imports, so a jump there can be cards added or removed, not the market.</p>
        </>
      )}
    </section>
  );
}

window.Lab = Lab;
