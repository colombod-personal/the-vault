// Buckets in the app (#125, docs/collections.md): the places a person's copies live in (a binder, a deck box, a trade box).
// The server groups and counts them (GET /collection/buckets) and moves copies (POST /collection/buckets/{id}/move);
// this file shows them: the switcher above Browse, the manager, the move controls of the card drawer and of a bucket's list.
const { useEffect: useEffectBk, useRef: useRefBk, useState: useStateBk } = React;

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

// "Import file..." on a bucket (#122, #124): the file is read by the server's preview first, which names the bucket and totals what
// the import leaves alone; nothing is written until the person confirms. The browser adds nothing up: every figure is the answer's.
function importedLine(res, name) {
  const merge = res.merge || {};
  const kept = (merge.kept_vault_edits && merge.kept_vault_edits.count) || 0;
  const both = (merge.conflicts && merge.conflicts.count) || 0;
  const edits = kept + both ? ` Kept ${bkPlural(kept + both, 'card', 'cards')} you changed here` + (both ? ` (${both.toLocaleString()} also changed in the file: your change was kept)` : '') + '.' : '';
  return `Imported into ${name}: ${window.describeChanges(res.changes)}. ${name} now holds ${bkPlural(res.copies, 'copy', 'copies')}.${edits}`;
}

function confirmLabel(p) {
  const left = (p.untouched && p.untouched.copies) || 0;
  const rest = left ? ` ${bkPlural(left, 'copy', 'copies')} elsewhere ${left === 1 ? 'stays as it is' : 'stay as they are'}.` : '';
  return `Import into ${p.bucket.name}: ${window.describeChanges(p.changes)}.${rest}`;
}

const UNMATCHED_SHOWN = 5;

function ImportPreview({ file, preview: p, busy, onConfirm, onCancel }) {
  const box = useRefBk(null);
  useEffectBk(() => { if (box.current) box.current.focus(); }, [!!p]);
  const c = p ? p.changes || {} : {};
  const merge = p ? p.merge || {} : {};
  const kept = (merge.kept_vault_edits && merge.kept_vault_edits.count) || 0;
  const both = (merge.conflicts && merge.conflicts.count) || 0;
  const left = p && p.untouched;
  return (
    <div className="panel bucket-import" role="group" tabIndex={-1} ref={box}
         aria-label={p ? `Import ${file.name} into ${p.bucket.name}` : `Reading ${file.name}`}>
      {!p ? (
        <p role="status" style={{ fontSize: 13 }}>Reading {file.name}…</p>
      ) : (
        <>
          <p className="eyebrow" style={{ marginBottom: 8 }}>Nothing is changed until you confirm</p>
          <p style={{ fontSize: 13, marginBottom: 8 }}>
            <strong>{file.name}</strong> has {bkPlural(p.rows, 'row', 'rows')} ({bkPlural(p.copies, 'copy', 'copies')}) and goes into{' '}
            <strong>{p.bucket.name}</strong> only, which holds {bkPlural(p.bucket.rows, 'row', 'rows')} ({bkPlural(p.bucket.copies, 'copy', 'copies')}) now.
          </p>
          {merge.how && <p style={{ fontSize: 13, marginBottom: 10 }}>{merge.how}</p>}
          <ul className="bucket-holds" aria-label={`What the file changes in ${p.bucket.name}`}>
            <li><span>New cards</span><span className="muted">{(c.added || 0).toLocaleString()}</span></li>
            <li><span>Cards gone</span><span className="muted">{(c.removed || 0).toLocaleString()}</span></li>
            <li><span>Cards with more copies</span><span className="muted">{(c.increased || 0).toLocaleString()}</span></li>
            <li><span>Cards with fewer copies</span><span className="muted">{(c.decreased || 0).toLocaleString()}</span></li>
            <li><span>Cards unchanged</span><span className="muted">{(c.unchanged || 0).toLocaleString()}</span></li>
            <li><span>Copies in / out</span><span className="muted">+{(c.copies_in || 0).toLocaleString()} / −{(c.copies_out || 0).toLocaleString()}</span></li>
          </ul>
          {kept > 0 && <p style={{ fontSize: 13, marginTop: 10 }}>{bkPlural(kept, 'card', 'cards')} you changed here since the last import {kept === 1 ? 'is' : 'are'} kept.</p>}
          {both > 0 && <p style={{ fontSize: 13, marginTop: 10 }}>{bkPlural(both, 'card', 'cards')} changed both in the file and here: your change here is kept.</p>}
          {p.unmatched_rows > 0 && (
            <div style={{ fontSize: 13, marginTop: 10 }}>
              <p>{bkPlural(p.unmatched_rows, 'row has', 'rows have')} no printing the Vault can identify (set code or number missing or unknown). They are kept and matched later by name.</p>
              <ul className="bucket-holds" aria-label="Rows without a known printing">
                {(p.unmatched || []).slice(0, UNMATCHED_SHOWN).map((u) => (
                  <li key={u.row}><span>Row {u.row}: {u.name}</span><span className="muted">{(u.set || '?').toUpperCase()} {u.number || '?'}</span></li>
                ))}
              </ul>
              {p.unmatched_rows > UNMATCHED_SHOWN && <p className="muted" style={{ fontSize: 12 }}>The first {UNMATCHED_SHOWN} are listed.</p>}
            </div>
          )}
          {left && (
            <p style={{ fontSize: 13, marginTop: 10 }}>
              <strong>Left as they are:</strong>{' '}
              {left.copies > 0
                ? `${bkPlural(left.copies, 'copy', 'copies')} (${bkPlural(left.rows, 'row', 'rows')}) in ${bkPlural(left.buckets, 'other bucket', 'other buckets')}.`
                : 'no other bucket holds copies.'}
              {left.note && <span className="muted" style={{ display: 'block', fontSize: 12, marginTop: 4 }}>{left.note}</span>}
            </p>
          )}
          <div className="bucket-import-actions">
            <button type="button" className="btn xs confirm" disabled={busy} onClick={onConfirm}>{busy ? 'Importing…' : confirmLabel(p)}</button>
            <button type="button" className="btn xs ghost" disabled={busy} onClick={onCancel}>Cancel</button>
          </div>
        </>
      )}
    </div>
  );
}

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
  const fileInput = useRefBk(null);
  const importFor = useRefBk(null);               // the bucket the next picked file goes into
  const [importing, setImporting] = useStateBk(null);  // { bucket, file, preview }: preview is null while the server reads the file
  const [imported, setImported] = useStateBk(null);    // the result line of the last import
  const importedBox = useRefBk(null);
  useEffectBk(() => { if (imported && importedBox.current) importedBox.current.focus(); }, [imported]);  // the panel is gone: keep the focus in the dialog

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

  const pickFile = (b) => {
    setError(null); setImported(null); setDeleting(null); setRenaming(null); setImporting(null);
    importFor.current = b;
    fileInput.current.click();
  };
  const onFile = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';  // the same file can be picked again
    const bucket = importFor.current;
    if (!file || !bucket) return;
    setBusy(true); setError(null); setImported(null);
    setImporting({ bucket, file, preview: null });
    try {
      const preview = await api.importPreview(file, bucket.id);
      setImporting({ bucket, file, preview });
    } catch (err) { setImporting(null); setError(err.message); } finally { setBusy(false); }
  };
  const confirmImport = async () => {
    const { bucket, file, preview } = importing;
    setBusy(true); setError(null);
    let res;
    try { res = await api.importInto(file, bucket.id); } catch (err) { setError(err.message); setBusy(false); return; }
    setImporting(null);
    setImported(importedLine(res, preview.bucket.name));
    try { await onChanged(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

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
                  <button type="button" className="btn xs ghost" disabled={busy} onClick={() => pickFile(b)}
                          aria-label={`Import a file into ${b.name}`}>Import file…</button>
                  <button type="button" className="btn xs ghost" disabled={busy} onClick={() => { setDeleting(null); setImporting(null); setRenaming({ id: b.id, name: b.name }); }}
                          aria-label={`Rename ${b.name}`}>Rename</button>
                  <button type="button" className="btn xs ghost" disabled={busy || buckets.length < 2 && b.copies > 0}
                          onClick={() => { setRenaming(null); setImporting(null); setDeleting({ bucket: b, to: (buckets.find((o) => o.id !== b.id) || {}).id }); }}
                          aria-label={`Delete ${b.name}`}>Delete</button>
                </span>
              </>
            )}
          </li>
        ))}
        {buckets.length === 0 && <li className="muted" style={{ fontSize: 13 }}>No buckets yet: import your collection, or make one below.</li>}
      </ul>

      <input ref={fileInput} type="file" accept=".csv,text/csv" style={{ display: 'none' }} tabIndex={-1} aria-hidden="true" onChange={onFile} />
      {importing && <ImportPreview file={importing.file} preview={importing.preview} busy={busy} onConfirm={confirmImport} onCancel={() => setImporting(null)} />}
      {imported && <p role="status" className="bucket-imported" tabIndex={-1} ref={importedBox}>{imported}</p>}

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
