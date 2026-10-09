// Buckets in the app (#125, docs/collections.md): the places a person's copies live in (a binder, a deck box, a trade box).
// The server groups and counts them (GET /collection/buckets) and moves copies (POST /collection/buckets/{id}/move);
// this file shows them: the switcher above Browse, the manager, the move controls of the card drawer and of a bucket's list.
const { useEffect: useEffectBk, useState: useStateBk } = React;

// The signed-in person's buckets, asked again when the collection's version changes or reload() is called.
function useBuckets(version, enabled = true) {
  const [state, setState] = useStateBk({ items: null, error: null });
  const [tick, setTick] = useStateBk(0);
  useEffectBk(() => {
    if (!enabled) return undefined;
    let dead = false;
    window.VaultApi.buckets().then(
      (items) => { if (!dead) setState({ items, error: null }); },
      (e) => { if (!dead) setState({ items: null, error: e.message }); });
    return () => { dead = true; };
  }, [version, tick, enabled]);
  return { buckets: state.items || [], loaded: state.items !== null, error: state.error, reload: () => setTick((t) => t + 1) };
}

// Move every copy of these printings (lines: { card_id, quantity }) from one bucket to another, 50 lines to a request. Each
// request is recorded as a change in the history, so a move is as visible in the app as one an assistant makes.
async function moveBucketLines(from, to, lines, onProgress) {
  let moved = 0;
  for (let i = 0; i < lines.length; i += 50) {
    const res = await window.VaultApi.moveCards(from, to, lines.slice(i, i + 50), true);
    moved += res.copies || 0;
    if (onProgress) onProgress(moved);
  }
  return moved;
}

const bkPlural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

function BucketBar({ buckets, value, onChange, onManage, totalCopies, disabled }) {
  return (
    <div className="bucket-bar">
      <label className="label-mono" htmlFor="bucket-select">Bucket</label>
      <select id="bucket-select" className="select" value={value || ''} disabled={disabled}
              onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        <option value="">All inventory ({bkPlural(totalCopies, 'copy', 'copies')})</option>
        {buckets.map((b) => <option key={b.id} value={b.id}>{b.name} ({bkPlural(b.copies, 'copy', 'copies')})</option>)}
      </select>
      <button type="button" className="btn xs" onClick={onManage}>Manage buckets</button>
    </div>
  );
}

// Make, rename and delete buckets. A bucket that still holds copies is deleted by moving them to another one first (the server
// refuses to delete a bucket with copies in it, so nothing is ever moved behind the person's back).
function BucketManager({ buckets, cardsApi, onClose, onChanged }) {
  const api = window.VaultApi;
  const [name, setName] = useStateBk('');
  const [renaming, setRenaming] = useStateBk(null);  // { id, name }
  const [deleting, setDeleting] = useStateBk(null);  // { bucket, to }
  const [busy, setBusy] = useStateBk(false);
  const [progress, setProgress] = useStateBk(null);
  const [error, setError] = useStateBk(null);

  const run = async (fn) => {
    setBusy(true); setError(null);
    try { await fn(); await onChanged(); } catch (e) { setError(e.message); } finally { setBusy(false); setProgress(null); }
  };
  const add = (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    run(async () => { await api.createBucket(name.trim()); setName(''); });
  };
  const rename = (e) => {
    e.preventDefault();
    run(async () => { await api.renameBucket(renaming.id, renaming.name.trim()); setRenaming(null); });
  };
  const remove = () => run(async () => {
    const { bucket, to } = deleting;
    if (bucket.copies > 0) {
      const lines = [];
      let page = await cardsApi.cards({ bucket: bucket.id, limit: 500 });
      for (;;) {
        lines.push(...page.items.map((c) => ({ card_id: c.key, quantity: c.q })));
        if (!page.more) break;
        page = await page.more();
      }
      await moveBucketLines(bucket.id, to, lines, (n) => setProgress(n));
    }
    await api.deleteBucket(bucket.id);
    setDeleting(null);
  });

  const others = deleting ? buckets.filter((b) => b.id !== deleting.bucket.id) : [];
  return (
    <Modal title="Manage buckets" onClose={onClose}>
      <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
        A bucket is a place your copies live in. Every copy is in exactly one, and together they add up to your inventory. Renaming
        changes the Vault's label only: the folder in your exported file stays as you imported it.
      </p>
      <ul className="bucket-list" aria-label="Your buckets">
        {buckets.map((b) => (
          <li key={b.id} className="bucket-row">
            {renaming && renaming.id === b.id ? (
              <form onSubmit={rename} className="bucket-edit">
                <input className="input" aria-label={`New name for ${b.name}`} value={renaming.name} maxLength={200} data-autofocus
                       onChange={(e) => setRenaming({ ...renaming, name: e.target.value })} />
                <button className="btn xs" disabled={busy || !renaming.name.trim()}>Save</button>
                <button type="button" className="btn xs ghost" onClick={() => setRenaming(null)}>Cancel</button>
              </form>
            ) : (
              <>
                <span className="bucket-name">{b.name}</span>
                <span className="muted bucket-count">{bkPlural(b.copies, 'copy', 'copies')}</span>
                <span className="bucket-actions">
                  <button type="button" className="btn xs ghost" disabled={busy} onClick={() => { setDeleting(null); setRenaming({ id: b.id, name: b.name }); }}
                          aria-label={`Rename ${b.name}`}>Rename</button>
                  <button type="button" className="btn xs ghost" disabled={busy || buckets.length < 2 && b.copies > 0}
                          onClick={() => { setRenaming(null); setDeleting({ bucket: b, to: (buckets.find((o) => o.id !== b.id) || {}).id }); }}
                          aria-label={`Delete ${b.name}`}>Delete</button>
                </span>
              </>
            )}
          </li>
        ))}
        {buckets.length === 0 && <li className="muted" style={{ fontSize: 13 }}>No buckets yet: import your collection, or make one below.</li>}
      </ul>

      {deleting && (
        <div className="panel bucket-delete" role="group" aria-label={`Delete ${deleting.bucket.name}`}>
          {deleting.bucket.copies > 0 ? (
            <>
              <p style={{ fontSize: 13, marginBottom: 8 }}>
                <strong>{deleting.bucket.name}</strong> holds {bkPlural(deleting.bucket.copies, 'copy', 'copies')}. They are moved to another bucket first
                (and the move is recorded in your history), then the bucket is deleted.
              </p>
              <label className="label-mono" htmlFor="bucket-move-to">Move them to</label>
              <select id="bucket-move-to" className="select" value={deleting.to || ''} disabled={busy}
                      onChange={(e) => setDeleting({ ...deleting, to: Number(e.target.value) })}>
                {others.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
              </select>
            </>
          ) : (
            <p style={{ fontSize: 13, marginBottom: 8 }}><strong>{deleting.bucket.name}</strong> is empty. Delete it?</p>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button type="button" className="btn xs" disabled={busy || (deleting.bucket.copies > 0 && !deleting.to)} onClick={remove}>
              {deleting.bucket.copies > 0 ? `Move ${bkPlural(deleting.bucket.copies, 'copy', 'copies')} and delete` : 'Delete bucket'}
            </button>
            <button type="button" className="btn xs ghost" disabled={busy} onClick={() => setDeleting(null)}>Keep it</button>
            {progress != null && <span className="muted" role="status" style={{ fontSize: 12, alignSelf: 'center' }}>{bkPlural(progress, 'copy', 'copies')} moved…</span>}
          </div>
        </div>
      )}

      <form onSubmit={add} className="bucket-edit" style={{ marginTop: 16 }}>
        <input className="input" aria-label="Name of a new bucket" placeholder="New bucket, e.g. Trade binder" value={name} maxLength={200}
               onChange={(e) => setName(e.target.value)} />
        <button className="btn xs" disabled={busy || !name.trim()}>Add bucket</button>
      </form>
      {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>{error}</p>}
    </Modal>
  );
}

// In the card drawer: where this printing's copies are, and a way to move some of them to another bucket.
function BucketMover({ card, buckets, onMoved }) {
  const [holds, setHolds] = useStateBk(null);  // [{ id, quantity }]
  const [from, setFrom] = useStateBk(null);
  const [to, setTo] = useStateBk(null);
  const [qty, setQty] = useStateBk(1);
  const [busy, setBusy] = useStateBk(false);
  const [message, setMessage] = useStateBk(null);
  const [error, setError] = useStateBk(null);

  const read = () => window.VaultApi.cardDetail(card.href).then((d) => {
    const by = new Map();
    for (const c of d.copies || []) if (c.bucket_id != null) by.set(c.bucket_id, (by.get(c.bucket_id) || 0) + c.quantity);
    const list = [...by].map(([id, quantity]) => ({ id, quantity }));
    setHolds(list);
    setFrom((f) => (list.some((h) => h.id === f) ? f : (list[0] || {}).id ?? null));
  }, (e) => setError(e.message));
  useEffectBk(() => { read(); }, [card.href]);

  const name = (id) => (buckets.find((b) => b.id === id) || { name: 'Unknown bucket' }).name;
  const held = holds && from != null ? (holds.find((h) => h.id === from) || { quantity: 0 }).quantity : 0;
  const targets = buckets.filter((b) => b.id !== from);
  useEffectBk(() => { if (!targets.some((b) => b.id === to)) setTo((targets[0] || {}).id ?? null); }, [from, buckets]);
  useEffectBk(() => { setQty((q) => Math.min(Math.max(1, q), Math.max(1, held))); }, [held]);
  if (!card.href || holds === null && !error) return null;
  if (!holds || holds.length === 0) return null;

  const move = async () => {
    setBusy(true); setError(null); setMessage(null);
    try {
      await window.VaultApi.moveCards(from, to, [{ card_id: card.key, quantity: qty }], true);
      setMessage(`Moved ${bkPlural(qty, 'copy', 'copies')} to ${name(to)}.`);
      await read();
      if (onMoved) await onMoved();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="panel" style={{ marginBottom: 20 }}>
      <p className="eyebrow" style={{ marginBottom: 12 }}>Where your copies are</p>
      <ul className="bucket-holds">
        {holds.map((h) => <li key={h.id}><span>{name(h.id)}</span><span className="muted">{bkPlural(h.quantity, 'copy', 'copies')}</span></li>)}
      </ul>
      {targets.length > 0 && (
        <div className="bucket-move" role="group" aria-label="Move copies to another bucket">
          <label className="label-mono">From
            <select className="select" value={from ?? ''} disabled={busy} onChange={(e) => setFrom(Number(e.target.value))}>
              {holds.map((h) => <option key={h.id} value={h.id}>{name(h.id)} ({h.quantity})</option>)}
            </select>
          </label>
          <label className="label-mono">Copies
            <input className="input" type="number" min={1} max={held} value={qty} disabled={busy}
                   onChange={(e) => setQty(Math.min(held, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
          <label className="label-mono">To
            <select className="select" value={to ?? ''} disabled={busy} onChange={(e) => setTo(Number(e.target.value))}>
              {targets.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </label>
          <button type="button" className="btn xs" disabled={busy || !to || held < 1} onClick={move}>
            Move {bkPlural(qty, 'copy', 'copies')}
          </button>
        </div>
      )}
      {message && <p role="status" className="muted" style={{ marginTop: 10, fontSize: 12 }}>{message}</p>}
      {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>{error}</p>}
    </div>
  );
}

// Above a bucket's list: move every copy of the cards ticked in the table to another bucket.
function BulkMoveBar({ bucketId, buckets, picked, items, onDone }) {
  const targets = buckets.filter((b) => b.id !== bucketId);
  const [to, setTo] = useStateBk(null);
  const [busy, setBusy] = useStateBk(false);
  const [error, setError] = useStateBk(null);
  useEffectBk(() => { if (!targets.some((b) => b.id === to)) setTo((targets[0] || {}).id ?? null); }, [bucketId, buckets]);
  const chosen = items.filter((c) => picked.has(c.key));
  const copies = chosen.reduce((n, c) => n + c.q, 0);
  if (targets.length === 0 || chosen.length === 0) return null;
  const go = async () => {
    setBusy(true); setError(null);
    try {
      await moveBucketLines(bucketId, to, chosen.map((c) => ({ card_id: c.key, quantity: c.q })));
      await onDone(`Moved ${bkPlural(copies, 'copy', 'copies')} of ${bkPlural(chosen.length, 'card', 'cards')} to ${(targets.find((b) => b.id === to) || {}).name}.`);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="bulk-bar" role="group" aria-label="Move the ticked cards">
      <span aria-live="polite">{bkPlural(chosen.length, 'card', 'cards')} ticked ({bkPlural(copies, 'copy', 'copies')})</span>
      <label className="label-mono" htmlFor="bulk-to">Move to</label>
      <select id="bulk-to" className="select" value={to ?? ''} disabled={busy} onChange={(e) => setTo(Number(e.target.value))}>
        {targets.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
      </select>
      <button type="button" className="btn xs" disabled={busy || !to} onClick={go}>Move {bkPlural(copies, 'copy', 'copies')}</button>
      {error && <span role="alert" style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--danger)' }}>{error}</span>}
    </div>
  );
}

Object.assign(window, { useBuckets, BucketBar, BucketManager, BucketMover, BulkMoveBar });
