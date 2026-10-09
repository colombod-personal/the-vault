// Change this deck — the deck page's cut-and-add flow (docs/deck-ideas-lab-design.md, task 0 under #163). On a saved deck of the
// person's own: choose cards to cut (from the deck's list) and cards to add (each looked up in the Vault's card catalog, so only
// real cards), see the server's check of the proposal (POST /decks/validate-changes) WITHOUT applying anything, then press the one
// button that names the change to save it (PUT /decks/{id} with the list the check ran on; the Vault records the deck's version).
// The wording is public/lib/deck_change.js; every number and reason is the server's. A swap in the address
// (#/decks/7?swap={"cut":[...],"add":[...]}, from the Ideas view) arrives as `initial` and fills in the cards.
const { useState: useStateC, useEffect: useEffectC, useRef: useRefC, useMemo: useMemoC } = React;

function DeckChange({ deckId, deckName, rows, format, setFormat, archidekt, initial, onClose, onApplied, onShowHistory }) {
  const V = window.VaultDeckChange;
  const prefilled = initial && !initial.error ? initial : null;
  const [cuts, setCuts] = useStateC(prefilled ? prefilled.cut : []);
  const [adds, setAdds] = useStateC([]);  // a swap's adds are looked up in the card catalog first
  const [notice, setNotice] = useStateC(initial && initial.error ? initial.error : '');
  const [find, setFind] = useStateC('');
  const [addText, setAddText] = useStateC('');
  const [look, setLook] = useStateC(null);  // { busy } | { miss, suggestions } | { error }
  const [check, setCheck] = useStateC(null);  // { loading } | { error } | { key, result, coverage, coverageError }
  const [saving, setSaving] = useStateC(false);
  const [saveError, setSaveError] = useStateC('');
  const [saved, setSaved] = useStateC(null);  // { line, archidekt } once the change is saved
  const root = useRefC(null), head = useRefC(null), done = useRefC(null), findInput = useRefC(null);

  useEffectC(() => {
    if (root.current && root.current.scrollIntoView) root.current.scrollIntoView({ block: 'start' });
    if (head.current) head.current.focus({ preventScroll: true });
    if (!prefilled) return;
    let stop = false;
    (async () => {
      setLook({ busy: true });
      const found = [], missing = [];
      for (const name of prefilled.add) {
        try { const a = await window.VaultApi.catalogCard(name); if (a.card) found.push(a.card.name); else missing.push(name); } catch { missing.push(name); }
        if (stop) return;
      }
      setAdds(found);
      setLook(null);
      if (missing.length) setNotice(`Not found in the card catalog, so not added: ${missing.join(', ')}.`);
      runCheck(prefilled.cut, found);
    })();
    return () => { stop = true; };
  }, []);
  useEffectC(() => { if (saved && done.current) done.current.focus(); }, [saved]);

  const key = V.proposalKey(format, cuts, adds);
  const current = check && check.key === key ? check : null;
  const changed = !!check && !check.loading && !check.error && check.key !== key;

  async function runCheck(c = cuts, a = adds) {
    if (!c.length && !a.length) return;
    const forKey = V.proposalKey(format, c, a);
    setCheck({ loading: true });
    setSaveError('');
    try {
      const answer = await window.VaultApi.deckValidateChanges(deckId, format, c, a);
      let coverage = null, coverageError = '';
      try { coverage = await window.VaultApi.deckCoverage(answer.result.deck_text); } catch (e) { coverageError = e.message; }
      setCheck({ key: forKey, result: answer.result, coverage, coverageError });
    } catch (e) { setCheck({ error: e.message }); }
  }

  async function addByName(raw) {
    const name = raw.trim();
    if (!name) return;
    setLook({ busy: true });
    try {
      const a = await window.VaultApi.catalogCard(name);
      if (a.card) { setAdds((list) => (list.length >= V.MAX_ITEMS ? list : [...list, a.card.name])); setAddText(''); setLook(null); }
      else setLook({ miss: name, suggestions: a.suggestions || [] });
    } catch (e) {
      if (e.status === 404) setLook({ miss: name, suggestions: [] });
      else setLook({ error: e.message });
    }
  }

  const cutCount = (name) => cuts.filter((n) => V.nameKey(n) === V.nameKey(name)).length;
  const dropOne = (list, name) => { const at = list.map(V.nameKey).lastIndexOf(V.nameKey(name)); return at < 0 ? list : list.filter((_, i) => i !== at); };
  const matches = useMemoC(() => {
    const k = find.trim().toLowerCase();
    return k ? (rows || []).filter((r) => r.name.toLowerCase().includes(k)).slice(0, 8) : [];
  }, [find, rows]);

  async function save() {
    if (!current || !current.result || saving) return;
    setSaving(true);
    setSaveError('');
    try {
      const d = await window.VaultApi.updateDeck(deckId, deckName, current.result.deck_text);
      setSaved({ line: V.savedLine(deckName, cuts, adds), archidekt });
      setCuts([]); setAdds([]); setCheck(null); setNotice(''); setFind('');
      await onApplied(d, current.result.deck_text);
    } catch (e) { setSaveError('Saving failed: ' + e.message); }
    setSaving(false);
  }

  const blocked = current && current.result ? V.blocked(current.result, cuts, adds) : null;
  const view = current && current.result ? current.result : null;
  const owned = current ? V.coverageByName(current.coverage) : {};

  return (
    <section className="panel dc" ref={root} aria-labelledby="dc-title" onKeyDown={(e) => { if (e.key === 'Escape' && !saving) { e.stopPropagation(); onClose(); } }}>
      <div className="dc-head">
        <h2 id="dc-title" className="h-display" tabIndex={-1} ref={head}>Change this deck</h2>
        <button className="btn xs ghost close-x" aria-label="Close the change flow" onClick={onClose}>×</button>
      </div>
      <p className="muted dc-lede">Choose cards to cut and cards to add, then check the change. Nothing is saved until you press the button that names it.</p>
      {archidekt && <p className="dc-note">{V.fromArchidekt}</p>}
      {notice && <p className="dc-note" role="status">{notice}</p>}

      {saved ? (
        <div className="dc-saved" role="status" tabIndex={-1} ref={done}>
          <p className="dc-saved-line">{saved.line}</p>
          {saved.archidekt && <p className="muted dc-small">{V.savedArchidekt}</p>}
          <div className="dc-actions">
            <button className="btn primary" onClick={onShowHistory}>See the History tab</button>
            <button className="btn" onClick={() => { setSaved(null); if (findInput.current) findInput.current.focus(); }}>Change more</button>
            <button className="btn ghost" onClick={onClose}>Close</button>
          </div>
        </div>
      ) : (
        <>
          <div className="dc-step">
            <p className="eyebrow">Cards to cut</p>
            <label className="label-mono" htmlFor="dc-find">Find a card in this deck</label>
            <input id="dc-find" ref={findInput} className="input" value={find} autoComplete="off" spellCheck="false" placeholder="Part of a card name"
              onChange={(e) => setFind(e.target.value)} />
            {find.trim() && (matches.length === 0
              ? <p className="muted dc-small">No card of this deck has “{find.trim()}” in its name.</p>
              : <ul className="dc-pick" aria-label="Cards in this deck">
                {matches.map((r) => {
                  const n = cutCount(r.name);
                  return (
                    <li key={r.name}>
                      <span className="dc-pick-name">{r.qty > 1 && <span className="muted">{r.qty}× </span>}{r.name}</span>
                      <button className="btn sm" disabled={n >= r.qty} onClick={() => setCuts((list) => [...list, r.name])}
                        aria-label={`Cut ${r.name}${n ? ` (${n} of ${r.qty} already chosen)` : ''}`}>{n >= r.qty ? (r.qty > 1 ? `All ${r.qty} chosen` : 'Chosen') : n ? `Cut one more (${n}/${r.qty})` : 'Cut'}</button>
                    </li>
                  );
                })}
              </ul>)}
            {cuts.length > 0 && (
              <ul className="dc-chips" aria-label="Cards chosen to cut">
                {V.copies(cuts).map((c) => (
                  <li key={c.name} className="dc-chip cut">
                    <span>− {c.n > 1 ? `${c.n}× ` : ''}{c.name}</span>
                    <button className="dc-x" aria-label={`Do not cut ${c.name}`} onClick={() => setCuts((list) => dropOne(list, c.name))}>×</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="dc-step">
            <p className="eyebrow">Cards to add</p>
            <form onSubmit={(e) => { e.preventDefault(); addByName(addText); }}>
              <label className="label-mono" htmlFor="dc-add">Card to add (looked up in the card catalog)</label>
              <div className="dc-row">
                <input id="dc-add" className="input" value={addText} autoComplete="off" spellCheck="false" enterKeyHint="search" placeholder="A card name"
                  onChange={(e) => { setAddText(e.target.value); if (look && !look.busy) setLook(null); }} />
                <button type="submit" className="btn" disabled={!addText.trim() || (look && look.busy)}>Look up and add</button>
              </div>
            </form>
            {look && look.busy && <p className="muted dc-small" role="status"><span className="spinner"></span> Looking up the card…</p>}
            {look && look.error && <p className="dc-error" role="alert">Couldn’t look the card up: {look.error}</p>}
            {look && look.miss && (
              <div role="status">
                <p className="dc-small">The card catalog has no card called “{look.miss}”.{look.suggestions.length ? ' Did you mean:' : ' Check the spelling.'}</p>
                {look.suggestions.length > 0 && (
                  <div className="dc-suggest">
                    {look.suggestions.map((s) => <button key={s} className="btn sm" onClick={() => { setLook(null); addByName(s); }}>{s}</button>)}
                  </div>
                )}
              </div>
            )}
            {adds.length > 0 && (
              <ul className="dc-chips" aria-label="Cards chosen to add">
                {V.copies(adds).map((c) => (
                  <li key={c.name} className="dc-chip add">
                    <span>+ {c.n > 1 ? `${c.n}× ` : ''}{c.name}</span>
                    <button className="dc-x" aria-label={`Do not add ${c.name}`} onClick={() => setAdds((list) => dropOne(list, c.name))}>×</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="dc-step">
            <p className="eyebrow">Check</p>
            <div className="dc-row">
              <FormatPicker format={format} setFormat={setFormat} />
              <button className="btn primary" disabled={(!cuts.length && !adds.length) || (check && check.loading) || (look && look.busy)} onClick={() => runCheck()}>
                {current ? 'Check again' : 'Check this change'}
              </button>
            </div>
            {!cuts.length && !adds.length && <p className="muted dc-small">Choose at least one card to cut or add.</p>}
            {check && check.loading && <p className="muted dc-small" role="status"><span className="spinner"></span> Checking the change against the card catalog and your collection…</p>}
            {check && check.error && <p className="dc-error" role="alert"><strong>Couldn’t check the change:</strong> {check.error}</p>}
            {changed && <p className="dc-note" role="status">You changed the cards after the last check. Check again before saving.</p>}

            {view && (
              <div className="dc-result" role="status" aria-live="polite">
                <p className="h-display dc-verdict" style={{ color: V.verdict(view).good ? 'var(--good)' : 'var(--danger)' }}>{V.verdict(view).text}</p>
                {view.issues.length > 0 && (
                  <ul className="dc-problems">
                    {view.issues.map((i, n) => {
                      const l = V.issueLine(i);
                      return <li key={n}><strong>{l.label}</strong>{l.card && <> · {l.card}</>}<span className="muted dc-detail">{l.detail}</span></li>;
                    })}
                  </ul>
                )}
                {V.existingLine(view) && <p className="muted dc-small">{V.existingLine(view)}</p>}
                <p className="dc-line">{V.countsLine(view)}</p>
                {V.costLines(view).map((t, n) => <p key={n} className="dc-line">{t}</p>)}
                {current.coverageError
                  ? <p className="muted dc-small">Couldn’t check what you own: {current.coverageError}</p>
                  : <>
                    {V.copies(adds).map((c) => <p key={c.name} className="dc-line">{V.ownershipLine(c.name, owned[V.nameKey(c.name)])}</p>)}
                    {V.toBuyLine(current.coverage) && <p className="dc-line">{V.toBuyLine(current.coverage)}</p>}
                  </>}
                <p className="muted dc-small">Not checked: {view.not_checked.join('; ')}. Prices and legality are Scryfall’s, as the Vault last loaded them.</p>
              </div>
            )}
          </div>

          {view && (
            <div className="dc-step dc-confirm">
              <p className="eyebrow">Save</p>
              {blocked
                ? <p className="dc-error" role="alert">{blocked}</p>
                : <>
                  <p className="dc-small">{view.issues.length ? 'The problems above stay in the deck if you save.' : 'This changes the saved deck and adds a version to its History.'}</p>
                  <button className="btn primary dc-save" disabled={saving} onClick={save}>{saving ? 'Saving…' : V.confirmLabel(deckName, cuts, adds, view)}</button>
                </>}
              {saveError && <p className="dc-error" role="alert">{saveError}</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
