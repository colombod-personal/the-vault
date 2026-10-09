// Browse view — searchable / filterable table of all printings. The server filters, sorts, pages
// and totals (GET /collection/cards); this view shows its pages.
const { useEffect: useEffectB, useState: useStateB } = React;

const BROWSE_PAGE = 60;
// The view's sort choices, as the server's `sort` parameter.
const BROWSE_SORTS = { value: '-value', qty: '-quantity', name: 'name', recent: '-acquired', oldest: 'acquired', mana: 'mana_value', manaDesc: '-mana_value' };
// The same types and mana value buckets as the Lab's breakdowns and the assistant's tools (the server defines them, vault/analytics.py):
// one main type per card (an Artifact Creature is a Creature), and mana value 0 to 7 or 8+ (a fraction counts down).
const BROWSE_TYPES = ['Creature', 'Land', 'Artifact', 'Enchantment', 'Instant', 'Sorcery', 'Planeswalker', 'Battle', 'Other'];
const BROWSE_MANA = ['0', '1', '2', '3', '4', '5', '6', '7', '8+'];

function Browse({ data, openCard, initialQuery, bucket, onBucket, tag, onTag, onChanged, readOnly }) {
  const api = data.api;
  // Buckets (#125): the places copies live in. Own collection only: a share shows no buckets.
  const { buckets, reload: reloadBuckets } = window.useBuckets(data.meta.version, !readOnly);
  const [managing, setManaging] = useStateB(false);
  // Tags (#128): labels on cards. Own collection only: a share shows none.
  const { tags, loaded: tagsLoaded, reload: reloadTags } = window.useTags(data.meta.version, !readOnly);
  const [managingTags, setManagingTags] = useStateB(false);
  const [picked, setPicked] = useStateB(() => new Set());  // printings ticked for a bulk move (inside one bucket) or a bulk tag
  const [movedNote, setMovedNote] = useStateB(null);
  const bucketId = readOnly ? null : (bucket || null);
  // A bucket that was deleted or is not yours: back to the whole inventory.
  useEffectB(() => {
    if (bucketId && buckets.length && !buckets.some((b) => b.id === bucketId)) onBucket(null);
  }, [bucketId, buckets]);
  const tagId = readOnly ? null : (tag || null);
  // A tag that was deleted, renamed or never existed: back to all tags.
  useEffectB(() => {
    if (tagId && tagsLoaded && !tags.some((t) => t.tag === tagId)) onTag(null);
  }, [tagId, tags, tagsLoaded]);
  const [q, setQ] = useStateB(initialQuery?.search || '');
  const [query, setQuery] = useStateB(q);  // the search sent to the server, a moment after typing stops
  const [setF, setSetF] = useStateB('');
  const [printingF, setPrintingF] = useStateB('');
  const [typeF, setTypeF] = useStateB('');
  const [manaF, setManaF] = useStateB('');  // a mana value ('' = any); the server matches it exactly
  const [sort, setSort] = useStateB(initialQuery?.sort || 'value');
  const [layout, setLayout] = useStateB('table');
  const [shown, setShown] = useStateB(null);   // { items, total, value_total, more }
  const [busy, setBusy] = useStateB(false);
  const [error, setError] = useStateB(null);

  useEffectB(() => {
    const t = setTimeout(() => setQuery(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const params = { q: query, set: setF, printing: printingF, type: typeF, mana_value: manaF, bucket: bucketId, tag: tagId, sort: BROWSE_SORTS[sort] || '-value' };
  const first = window.useVaultQuery(() => api.cards({ ...params, limit: BROWSE_PAGE }),
    [api.base, data.meta.version, query, setF, printingF, typeF, manaF, bucketId, tagId, sort]);
  useEffectB(() => { setPicked(new Set()); }, [bucketId, tagId, query, setF, printingF, typeF, manaF, sort, data.meta.version]);
  useEffectB(() => { if (first.data) { setShown(first.data); setError(null); } }, [first.data]);
  useEffectB(() => { if (first.error) setError(first.error.message); }, [first.error]);

  // Next pages follow the server's `next` links; "Show all" asks again in pages of 500.
  async function load(next) {
    setBusy(true);
    try {
      let page = await next(), items = [...shown.items, ...page.items];
      if (next === shown.more) {
        setShown({ ...page, items });
        return;
      }
      items = page.items;
      while (page.more) {
        page = await page.more();
        items = items.concat(page.items);
      }
      setShown({ ...page, items });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  const showAll = () => load(() => api.cards({ ...params, limit: 500 }));

  const sets = (window.useVaultQuery(() => api.sets({ sort: 'code' }), [api.base, data.meta.version]).data || { items: [] }).items;
  const items = shown ? shown.items : [];
  const total = shown ? shown.total : 0;
  const hidden = data.meta.costsHidden;

  return (
    <div data-screen-label="02 Browse">
      <div style={{ marginBottom: 24 }}>
        <p className="eyebrow">Library</p>
        <h1 className="h1" style={{ marginTop: 6 }}>Every printing you own.</h1>
      </div>

      {!readOnly && buckets.length > 0 && (
        <window.BucketBar buckets={buckets} value={bucketId} onChange={onBucket} onManage={() => setManaging(true)}
                          totalCopies={data.meta.totalQty} />
      )}
      {managing && <window.BucketManager buckets={buckets} cardsApi={api} onClose={() => setManaging(false)}
                                         onChanged={async () => { reloadBuckets(); if (onChanged) await onChanged(); }} />}
      {!readOnly && <window.TagBar tags={tags} loaded={tagsLoaded} value={tagId} onChange={onTag} onManage={() => setManagingTags(true)}
                                   version={data.meta.version} />}
      {managingTags && <window.TagManager tags={tags} onClose={() => setManagingTags(false)}
                                          onChanged={async () => { reloadTags(); if (onChanged) await onChanged(); }} />}

      <div className="panel" style={{ marginBottom: 16, padding: 16 }}>
        <div className="filter-grid" style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr 1fr', gap: 12 }}>
          <input
            className="input"
            placeholder="Search by card name or set…"
            aria-label="Search by card name or set"
            value={q}
            onChange={e => setQ(e.target.value)}
          />
          <select className="select" aria-label="Filter by set" value={setF} onChange={e => setSetF(e.target.value)}>
            <option value="">All sets ({data.meta.uniqueSets})</option>
            {sets.map(s => <option key={s.code} value={s.code}>{s.code} — {s.name}</option>)}
          </select>
          <select className="select" aria-label="Filter by printing" value={printingF} onChange={e => setPrintingF(e.target.value)}>
            <option value="">All printings</option>
            {Object.keys(data.meta.printings).filter(k => k).map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select className="select" aria-label="Sort by" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="value">Sort: total value ↓</option>
            <option value="qty">Sort: quantity ↓</option>
            <option value="name">Sort: name A→Z</option>
            <option value="recent">Sort: newest first</option>
            <option value="oldest">Sort: oldest first</option>
            <option value="mana">Sort: mana value ↑ (cheapest first)</option>
            <option value="manaDesc">Sort: mana value ↓</option>
          </select>
          <select className="select" aria-label="Filter by card type" value={typeF} onChange={e => setTypeF(e.target.value)}>
            <option value="">All card types</option>
            {BROWSE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <select className="select" aria-label="Filter by mana value" value={manaF} onChange={e => setManaF(e.target.value)}>
            <option value="">Any mana value</option>
            {BROWSE_MANA.map(n => <option key={n} value={n}>Mana value {n}</option>)}
          </select>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 14, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--text-2)' }}>
          <div aria-live="polite">
            <span style={{ color: 'var(--gold)' }}>{total.toLocaleString()}</span> entries match · showing <span style={{ color: 'var(--text)' }}>{items.length.toLocaleString()}</span> · combined value <span style={{ color: 'var(--gold)' }}>${(shown?.value_total || 0).toFixed(2)}</span>
            {first.loading && <span className="muted"> · loading…</span>}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button className={`chip ${layout === 'table' ? 'active' : ''}`} onClick={() => setLayout('table')}>Table</button>
            <button className={`chip ${layout === 'grid' ? 'active' : ''}`} onClick={() => setLayout('grid')}>Grid</button>
          </div>
        </div>
        {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>Couldn't load: {error}</p>}
      </div>

      {movedNote && <p role="status" className="muted" style={{ fontSize: 12, margin: '0 0 12px' }}>{movedNote}</p>}
      {bucketId && !readOnly && (
        <window.BulkMoveBar bucketId={bucketId} buckets={buckets} picked={picked} items={items}
                            onDone={async (note) => { setPicked(new Set()); setMovedNote(note); reloadBuckets(); if (onChanged) await onChanged(); }} />
      )}
      {!readOnly && (
        <window.BulkTagBar picked={picked} items={items} tags={tags}
                           onDone={async (note) => { setPicked(new Set()); setMovedNote(note); reloadTags(); if (onChanged) await onChanged(); }} />
      )}

      {layout === 'table' ? (
        <div className="panel panel-flush">
          <table className="tbl">
            <thead>
              <tr>
                {!readOnly && (
                  <th style={{ width: 28 }}>
                    <label className="tick"><input type="checkbox" aria-label="Tick every card on this page" checked={items.length > 0 && items.every((c) => picked.has(c.key))}
                           onChange={(e) => setPicked(e.target.checked ? new Set(items.map((c) => c.key)) : new Set())} /></label>
                  </th>
                )}
                <th>Card</th>
                <th>Set</th>
                <th>#</th>
                <th>Printing</th>
                <th className="num">Qty</th>
                <th className="num">Market</th>
                <th className="num">Total</th>
                <th className="num">Spent</th>
                <th className="num">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                // shared without prices paid, or no price paid recorded: no fake $0 / full-value P&L
                <tr key={c.key} onClick={() => openCard(c)} style={{ cursor: 'pointer' }}>
                  {!readOnly && (
                    <td onClick={(e) => e.stopPropagation()}>
                      <label className="tick"><input type="checkbox" aria-label={`Tick ${c.n}`} checked={picked.has(c.key)}
                             onChange={(e) => setPicked((p) => { const n = new Set(p); e.target.checked ? n.add(c.key) : n.delete(c.key); return n; })} /></label>
                    </td>
                  )}
                  <td style={{ fontWeight: 600 }}>
                    {/* the row is clickable with a mouse; this button makes it reachable by keyboard */}
                    <button type="button" className="row-link" onClick={(e) => { e.stopPropagation(); openCard(c); }}>{c.n}</button>
                    {!readOnly && <window.TagChips detail={c.tagsDetail} tags={c.tags} />}
                  </td>
                  <td>
                    <span className="chip" style={{ padding: '2px 8px', fontSize: 10, gap: 4 }}>
                      {window.SetIcon && <SetIcon code={c.s} size={12} fallback={false} />}
                      <span>{c.s}</span>
                    </span>
                  </td>
                  <td className="muted" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{c.cn}</td>
                  <td className="muted" style={{ fontSize: 11 }}>{c.p}{c.c !== 'Mint' ? ` · ${c.c}` : ''}</td>
                  <td className="num">{c.q}</td>
                  <td className="num">${c.mk.toFixed(2)}</td>
                  <td className="num" style={{ color: 'var(--gold)' }}>${c.v.toFixed(2)}</td>
                  <td className="num muted">{window.vaultSpentText(c, hidden)}</td>
                  {hidden || c.gain == null ? <td className="num muted">{window.vaultGainText(c, hidden)}</td> : (
                    <td className="num" style={{ color: c.gain >= 0 ? 'var(--good)' : 'var(--danger)' }}>{window.vaultGainText(c, hidden)}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <CardGrid cards={items} onClick={openCard} />
      )}

      {shown && shown.more && (
        <div style={{ textAlign: 'center', marginTop: 20 }}>
          <button className="btn" disabled={busy} onClick={() => load(shown.more)}>
            Load {Math.min(BROWSE_PAGE, total - items.length)} more →
          </button>
          <button className="btn ghost" style={{ marginLeft: 8 }} disabled={busy} onClick={showAll}>
            Show all {total.toLocaleString()}
          </button>
        </div>
      )}
    </div>
  );
}

window.Browse = Browse;
