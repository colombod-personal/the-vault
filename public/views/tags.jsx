// Tags in the app (#128, docs/collections.md section 2): labels on cards, the person's own. The server lists them with counts
// (GET /collection/tags), tags and untags (POST /collection/tags/{tag}/cards and /cards/remove) and says who wrote each tag on a
// card (`tags_detail`); this file shows them: chips on the rows of Browse, the filter above it, the bulk bar for ticked cards, the
// manager, and in the card drawer the card's tags (with accept / remove for an assistant's) and its notes (vault_metadata, read only).
// A tag an assistant wrote is always marked with the app that wrote it, never shown as the person's own.
const { useEffect: useEffectTg, useRef: useRefTg, useState: useStateTg } = React;

const TAG_CONFIRM_ABOVE = 25;  // the server's: tagging more cards than this at once is shown first and applied only with confirm
const TAG_CHUNK = 200;         // the server's most ids in one request
const TAG_RULE = "A tag is 1 to 40 characters: lower case letters, digits, '-' and ':' (for example trade or deck:sliver).";
const tgPlural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const tgName = (text) => (text || '').trim().toLowerCase();
const tgValid = (name) => /^[a-z0-9:-]{1,40}$/.test(name);
const tgAppOf = (d) => d.source_detail || 'an assistant';
// Who wrote a tag on a card, in words (an assistant's is never "yours").
const tgByText = (d) => (d.source === 'assistant' ? `Added by ${tgAppOf(d)}` : d.source === 'system' ? 'Added by the Vault' : 'Your tag');

// The signed-in person's tags with counts, asked again when the collection's version changes (a tag change moves it) or reload() is called.
function useTags(version, enabled = true) {
  const [state, setState] = useStateTg({ items: null, error: null });
  const [tick, setTick] = useStateTg(0);
  useEffectTg(() => {
    if (!enabled) return undefined;
    let dead = false;
    window.VaultApi.tags().then(
      (items) => { if (!dead) setState({ items, error: null }); },
      (e) => { if (!dead) setState({ items: null, error: e.message }); });
    return () => { dead = true; };
  }, [version, tick, enabled]);
  return { tags: state.items || [], loaded: state.items !== null, error: state.error, reload: () => setTick((t) => t + 1) };
}

// A card's tags in a row of the table. An assistant's tag carries "AI" and its app's name (to a screen reader too).
function TagChips({ detail, tags }) {
  const rows = detail && detail.length ? detail : (tags || []).map((tag) => ({ tag, source: 'person' }));
  if (!rows.length) return null;
  return (
    <ul className="tag-row" aria-label="Tags">
      {rows.map((d) => (
        <li key={d.tag} className={`tag-chip${d.source === 'assistant' ? ' ai' : ''}`}
            title={d.source === 'assistant' ? `Added by ${tgAppOf(d)}: open the card to accept or remove it` : undefined}>
          {d.source === 'assistant' && <span className="tag-mark" aria-hidden="true">AI</span>}
          <span className="tag-text">{d.tag}</span>
          {d.source === 'assistant' && <span className="sr-only"> (added by {tgAppOf(d)})</span>}
        </li>
      ))}
    </ul>
  );
}

// Above Browse: the tag filter (kept in the address as #/browse?tag=trade, with the bucket if one is chosen).
// `quiet` (the analytics views' scope bar, #130): just the choice, without the tag's detail line or the "no tags yet" hint about ticking cards.
function TagBar({ tags, loaded, value, onChange, onManage, version, quiet }) {
  const [detail, setDetail] = useStateTg(null);
  useEffectTg(() => {
    setDetail(null);
    if (!value || quiet) return undefined;
    let dead = false;
    window.VaultApi.tagDetail(value).then((d) => { if (!dead) setDetail(d); }, () => {});
    return () => { dead = true; };
  }, [value, version]);
  if (loaded && tags.length === 0 && !value) {
    return quiet ? null : <p className="muted tag-note">No tags yet. Tick cards below, or open a card, to tag it (for example trade or deck:sliver).</p>;
  }
  const known = tags.some((t) => t.tag === value);
  const assistants = detail ? detail.assistants : [];
  return (
    <div className="bucket-bar tag-bar">
      <label className="label-mono" htmlFor="tag-select">Tag</label>
      <select id="tag-select" className="select" value={value || ''} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">All tags</option>
        {value && !known && <option value={value}>{value}</option>}
        {tags.map((t) => (
          <option key={t.tag} value={t.tag}>
            {t.tag} ({tgPlural(t.cards, 'card', 'cards')}{t.by_source.assistant > 0 ? `, ${t.by_source.assistant} by an assistant` : ''})
          </option>
        ))}
      </select>
      {onManage && <button type="button" className="btn xs" onClick={onManage}>Manage tags</button>}
      {detail && (
        <p className="muted tag-note" role="status">
          {tgPlural(detail.cards, 'card', 'cards')} tagged {detail.tag}, {detail.owned_cards} of them in your collection now.
          {assistants.length > 0 && <> Written by an assistant: <strong>{assistants.join(', ')}</strong>. Open a card to accept or remove its tag.</>}
        </p>
      )}
    </div>
  );
}

// Rename a tag on every card, or take it off every card (the cards stay). Nothing is deleted without an explicit button naming the count.
function TagManager({ tags, onClose, onChanged }) {
  const api = window.VaultApi;
  const [renaming, setRenaming] = useStateTg(null);  // { tag, name }
  const [deleting, setDeleting] = useStateTg(null);  // a tag item
  const [busy, setBusy] = useStateTg(false);
  const [error, setError] = useStateTg(null);
  const [message, setMessage] = useStateTg(null);
  const run = async (fn) => {
    setBusy(true); setError(null); setMessage(null);
    try { setMessage(await fn()); await onChanged(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const rename = (e) => {
    e.preventDefault();
    const name = tgName(renaming.name);
    if (!tgValid(name)) { setError(TAG_RULE); return; }
    run(async () => { await api.renameTag(renaming.tag, name); setRenaming(null); return `Renamed ${renaming.tag} to ${name}.`; });
  };
  const remove = () => run(async () => { const t = deleting; await api.deleteTag(t.tag); setDeleting(null); return `Took ${t.tag} off ${tgPlural(t.cards, 'card', 'cards')}.`; });
  return (
    <Modal title="Manage tags" onClose={onClose}>
      <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
        A tag is a label on a card, not on a copy: it never changes your quantities and it stays when you import again. Renaming
        changes it on every card; if a card already has the new name, the two merge. Removing a tag never removes a card.
      </p>
      <ul className="bucket-list" aria-label="Your tags">
        {tags.map((t) => (
          <li key={t.tag} className="bucket-row">
            {renaming && renaming.tag === t.tag ? (
              <form onSubmit={rename} className="bucket-edit">
                <input className="input" aria-label={`New name for ${t.tag}`} value={renaming.name} maxLength={40} data-autofocus
                       onChange={(e) => setRenaming({ ...renaming, name: e.target.value })} />
                <button className="btn xs" disabled={busy || !renaming.name.trim()}>Save</button>
                <button type="button" className="btn xs ghost" onClick={() => setRenaming(null)}>Cancel</button>
              </form>
            ) : (
              <>
                <span className="bucket-name">{t.tag}</span>
                <span className="muted bucket-count">
                  {tgPlural(t.cards, 'card', 'cards')}{t.by_source.assistant > 0 ? ` · ${t.by_source.assistant} by an assistant` : ''}
                </span>
                <span className="bucket-actions">
                  <button type="button" className="btn xs ghost" disabled={busy} aria-label={`Rename ${t.tag}`}
                          onClick={() => { setDeleting(null); setRenaming({ tag: t.tag, name: t.tag }); }}>Rename</button>
                  <button type="button" className="btn xs ghost" disabled={busy} aria-label={`Remove ${t.tag} from every card`}
                          onClick={() => { setRenaming(null); setDeleting(t); }}>Remove</button>
                </span>
              </>
            )}
          </li>
        ))}
        {tags.length === 0 && <li className="muted" style={{ fontSize: 13 }}>No tags yet: tick cards in Browse, or open a card, to tag it.</li>}
      </ul>
      {deleting && (
        <div className="panel bucket-delete" role="group" aria-label={`Remove ${deleting.tag} from every card`}>
          <p style={{ fontSize: 13, marginBottom: 8 }}>
            Take <strong>{deleting.tag}</strong> off {tgPlural(deleting.cards, 'card', 'cards')}? The cards stay in your collection.
          </p>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn xs" disabled={busy} onClick={remove} data-autofocus>
              Remove {deleting.tag} from {tgPlural(deleting.cards, 'card', 'cards')}
            </button>
            <button type="button" className="btn xs ghost" disabled={busy} onClick={() => setDeleting(null)}>Keep it</button>
          </div>
        </div>
      )}
      {message && <p role="status" className="muted" style={{ marginTop: 10, fontSize: 12 }}>{message}</p>}
      {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--danger)' }}>{error}</p>}
    </Modal>
  );
}

// Above the table when cards are ticked: tag them all, or take a tag off them all. More than 25 cards waits for an explicit button
// that says the count (the server says the same: it shows first and applies only with confirm).
function BulkTagBar({ picked, items, tags, onDone }) {
  const [name, setName] = useStateTg('');
  const [busy, setBusy] = useStateTg(false);
  const [error, setError] = useStateTg(null);
  const [pending, setPending] = useStateTg(null);  // { mode: 'add' | 'remove', tag, ids } waiting for the person's yes
  const confirmButton = useRefTg(null);
  const chosen = items.filter((c) => picked.has(c.key));
  useEffectTg(() => { setPending(null); setError(null); }, [chosen.length]);
  useEffectTg(() => { if (pending && confirmButton.current) confirmButton.current.focus(); }, [pending]);
  if (chosen.length === 0) return null;

  const tag = tgName(name);
  const send = async (mode, label, ids, confirm) => {
    const call = mode === 'add' ? window.VaultApi.tagCards : window.VaultApi.untagCards;
    let changed = 0, same = 0;
    for (let i = 0; i < ids.length; i += TAG_CHUNK) {
      const res = await call(label, ids.slice(i, i + TAG_CHUNK), confirm);
      if (res.applied === false) throw new Error(res.note || 'The server wants a yes first.');
      changed += (mode === 'add' ? res.added + (res.accepted || 0) : res.removed) || 0;
      same += (mode === 'add' ? res.already_tagged - (res.accepted || 0) : res.not_tagged) || 0;
    }
    return { changed, same };
  };
  const go = async (mode, confirmed) => {
    const ids = confirmed ? pending.ids : chosen.map((c) => c.key);
    const label = confirmed ? pending.tag : tag;
    if (!tgValid(label)) { setError(TAG_RULE); return; }
    if (!confirmed && ids.length > TAG_CONFIRM_ABOVE) { setError(null); setPending({ mode, tag: label, ids }); return; }
    setBusy(true); setError(null);
    try {
      const { changed, same } = await send(mode, label, ids, confirmed);
      setPending(null); setName('');
      await onDone(mode === 'add'
        ? `Tagged ${tgPlural(changed, 'card', 'cards')} ${label}${same ? ` (${same} already had it)` : ''}.`
        : `Took ${label} off ${tgPlural(changed, 'card', 'cards')}${same ? ` (${same} did not have it)` : ''}.`);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };

  const first = chosen.slice(0, 3).map((c) => c.n).join(', ');
  return (
    <div className="bulk-bar tag-bulk" role="group" aria-label="Tag the ticked cards">
      <span aria-live="polite">{tgPlural(chosen.length, 'card', 'cards')} ticked</span>
      <label className="label-mono" htmlFor="bulk-tag">Tag</label>
      <input id="bulk-tag" className="input" list="bulk-tag-names" placeholder="e.g. trade or deck:sliver" maxLength={40} value={name}
             autoComplete="off" disabled={busy} onChange={(e) => { setName(e.target.value); setPending(null); }}
             onKeyDown={(e) => { if (e.key === 'Enter' && tgValid(tag)) { e.preventDefault(); go('add', false); } }} />
      <datalist id="bulk-tag-names">{tags.map((t) => <option key={t.tag} value={t.tag} />)}</datalist>
      <button type="button" className="btn xs" disabled={busy || !tgValid(tag)} onClick={() => go('add', false)}>Add tag</button>
      <button type="button" className="btn xs ghost" disabled={busy || !tgValid(tag)} onClick={() => go('remove', false)}>Take it off</button>
      {pending && (
        <div className="tag-confirm" role="group" aria-label="Confirm">
          <span>
            {pending.mode === 'add' ? 'Tag' : 'Take'} {first}{chosen.length > 3 ? ` and ${chosen.length - 3} more` : ''}
            {pending.mode === 'add' ? ' with' : ' off'} <strong>{pending.tag}</strong>? That is {tgPlural(pending.ids.length, 'card', 'cards')}.
          </span>
          <button type="button" className="btn xs" ref={confirmButton} disabled={busy} onClick={() => go(pending.mode, true)}>
            {pending.mode === 'add' ? `Tag ${tgPlural(pending.ids.length, 'card', 'cards')}` : `Take it off ${tgPlural(pending.ids.length, 'card', 'cards')}`}
          </button>
          <button type="button" className="btn xs ghost" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
        </div>
      )}
      {error && <span role="alert" className="tag-error">{error}</span>}
    </div>
  );
}

// In the card drawer: this card's tags, who wrote each, and a way to add one. A tag an assistant wrote is marked with its app and
// can be accepted (the card is then tagged by you, the same tag) or removed.
function CardTags({ card, tags, onChanged }) {
  const [rows, setRows] = useStateTg(null);  // [{ tag, source, source_detail }]; null = not read yet
  const [taggable, setTaggable] = useStateTg(true);
  const [name, setName] = useStateTg('');
  const [busy, setBusy] = useStateTg(false);
  const [message, setMessage] = useStateTg(null);
  const [error, setError] = useStateTg(null);

  const read = () => window.VaultApi.cardDetail(card.href).then((d) => {
    setTaggable(d.tags_detail != null);  // a printing the Vault could not match to a card yet has no tags field
    setRows(d.tags_detail || []);
  }, (e) => setError(e.message));
  useEffectTg(() => { setRows(null); setMessage(null); read(); }, [card.href]);
  if (!card.href || rows === null && !error) return null;

  const change = async (fn, done) => {
    setBusy(true); setError(null); setMessage(null);
    try { await fn(); setMessage(done); await read(); if (onChanged) await onChanged(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  const add = (e) => {
    e.preventDefault();
    const tag = tgName(name);
    if (!tgValid(tag)) { setError(TAG_RULE); return; }
    change(async () => { await window.VaultApi.tagCards(tag, [card.key]); setName(''); }, `Added ${tag}.`);
  };
  const accept = (d) => change(() => window.VaultApi.tagCards(d.tag, [card.key]), `Accepted ${d.tag}: it is now your own tag.`);
  const remove = (d) => change(() => window.VaultApi.untagCards(d.tag, [card.key]), `Removed ${d.tag}.`);

  return (
    <div className="panel" style={{ marginBottom: 20 }} role="group" aria-label={`Tags on ${card.n}`}>
      <p className="eyebrow" style={{ marginBottom: 6 }}>Tags</p>
      <p className="muted" style={{ fontSize: 12, marginBottom: 10 }}>On the card, so every printing of {card.n} has them.</p>
      {rows && rows.length > 0 && (
        <ul className="tag-list">
          {rows.map((d) => (
            <li key={d.tag} className={`tag-item${d.source === 'assistant' ? ' ai' : ''}`}>
              <span className="tag-chip-lg">
                {d.source === 'assistant' && <span className="tag-mark" aria-hidden="true">AI</span>}
                {d.tag}
              </span>
              <span className="muted tag-by">{tgByText(d)}</span>
              {d.source === 'assistant' ? (
                <span className="tag-actions">
                  <button type="button" className="btn xs" disabled={busy} onClick={() => accept(d)}
                          aria-label={`Accept ${d.tag}, added by ${tgAppOf(d)}`}>Accept</button>
                  <button type="button" className="btn xs ghost" disabled={busy} onClick={() => remove(d)}
                          aria-label={`Remove ${d.tag}, added by ${tgAppOf(d)}`}>Remove</button>
                </span>
              ) : (
                <button type="button" className="btn xs ghost tag-x" disabled={busy} onClick={() => remove(d)}
                        aria-label={`Remove ${d.tag}`} title={`Remove ${d.tag}`}>×</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {rows && rows.length === 0 && taggable && <p className="muted" style={{ fontSize: 13, marginBottom: 10 }}>No tags on this card yet.</p>}
      {taggable ? (
        <form onSubmit={add} className="bucket-edit">
          <label className="sr-only" htmlFor="card-tag">Add a tag to {card.n}</label>
          <input id="card-tag" className="input" list="card-tag-names" placeholder="Add a tag, e.g. trade" maxLength={40} value={name}
                 autoComplete="off" disabled={busy} onChange={(e) => setName(e.target.value)} />
          <datalist id="card-tag-names">{tags.map((t) => <option key={t.tag} value={t.tag} />)}</datalist>
          <button className="btn xs" disabled={busy || !name.trim()}>Add tag</button>
        </form>
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>The Vault has not matched this printing to a card yet, so it cannot take a tag.</p>
      )}
      {message && <p role="status" className="muted" style={{ marginTop: 10, fontSize: 12 }}>{message}</p>}
      {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--danger)' }}>{error}</p>}
    </div>
  );
}

// A JSON value as nested key/values (text only: nothing here is ever read as markup).
function MetaValue({ value, depth = 0 }) {
  if (value === null || value === undefined) return <span className="muted">none</span>;
  if (Array.isArray(value)) {
    return value.length === 0 ? <span className="muted">empty list</span>
      : <ul className="meta-list">{value.map((v, i) => <li key={i}><MetaValue value={v} depth={depth + 1} /></li>)}</ul>;
  }
  if (typeof value === 'object') {
    const keys = Object.keys(value);
    if (depth > 7) return <span className="muted">…</span>;
    return keys.length === 0 ? <span className="muted">empty</span>
      : <dl className="meta-dl">{keys.map((k) => <div key={k}><dt>{k}</dt><dd><MetaValue value={value[k]} depth={depth + 1} /></dd></div>)}</dl>;
  }
  return <span className="meta-text">{String(value)}</span>;
}

const metaWhen = (at) => {
  const d = at ? new Date(at) : null;
  return d && !isNaN(d) ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : null;
};

// In the card drawer, read only: what the person and their assistants keep on the card (vault_metadata), one block per writer.
// An assistant's notes say so and name the app; they are never shown as the person's own.
function CardMetadata({ card }) {
  const [doc, setDoc] = useStateTg(null);
  const [error, setError] = useStateTg(null);
  useEffectTg(() => {
    let dead = false;
    setDoc(null); setError(null);
    window.VaultApi.cardMetadata(card.key).then((d) => { if (!dead) setDoc(d); }, (e) => { if (!dead) setError(e.status === 422 || e.status === 404 ? null : e.message); });
    return () => { dead = true; };
  }, [card.key]);
  if (!doc && !error) return null;
  const names = doc ? Object.keys(doc.namespaces).sort((a, b) => (a === 'user' ? -1 : b === 'user' ? 1 : a === 'system' ? 1 : b === 'system' ? -1 : a.localeCompare(b))) : [];
  return (
    <div className="panel" style={{ marginBottom: 20 }} role="group" aria-label="Notes on this card (read only)">
      <p className="eyebrow" style={{ marginBottom: 6 }}>Notes on this card</p>
      <p className="muted" style={{ fontSize: 12, marginBottom: 10 }}>Read only. Each writer keeps its own notes; an assistant never writes in yours.</p>
      {doc && names.length === 0 && <p className="muted" style={{ fontSize: 13 }}>Nothing is kept on this card yet.</p>}
      {doc && names.map((ns) => {
        const w = (doc.written || {})[ns] || {};
        const assistant = ns.startsWith('ai.');
        const when = metaWhen(w.at);
        return (
          <section key={ns} className={`meta-ns${assistant ? ' ai' : ''}`} aria-label={assistant ? `Notes from ${w.by || ns}` : ns === 'user' ? 'Your notes' : 'Notes from the Vault'}>
            <h3 className="meta-head">
              {assistant && <span className="tag-mark" aria-hidden="true">AI</span>}
              {assistant ? `Written by an assistant: ${w.by || ns}` : ns === 'user' ? 'Your notes' : 'From the Vault'}
            </h3>
            {(w.by || when) && <p className="muted meta-when">{ns === 'system' ? 'Kept' : 'Last written'}{ns === 'user' ? ' by you' : ''}{when ? ` on ${when}` : ''}</p>}
            <MetaValue value={doc.namespaces[ns]} />
          </section>
        );
      })}
      {error && <p role="alert" style={{ marginTop: 10, fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--danger)' }}>Couldn't load the notes: {error}</p>}
    </div>
  );
}

Object.assign(window, { useTags, TagChips, TagBar, TagManager, BulkTagBar, CardTags, CardMetadata });
