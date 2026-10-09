// The deck page's "Opening turns" tab (#138, docs/deck-simulation-design.md): how the list plays. One call to POST /decks/simulate when the
// tab opens and again when a control changes (1,000 games, the first 5 shown turn by turn), and the mana curve from POST /decks/stats in
// parallel, so the curve does not wait for the games. Nothing is computed here: every figure is the server's, and the words are
// public/lib/deck_sim.js (tests/js/deck_sim.test.mjs, tests/test_deck_sim_page.py run them on the server's real answers).
// It reuses useDeckAnswer, DeckCurve and FORMAT_CHOICES from views/deck.jsx (the views are joined into one script by web/build.mjs).

function DeckOpening({ text, title, format, setFormat }) {
  const Sim = window.VaultDeckSim;
  const [onPlay, setOnPlay] = useStateD(true);
  const [turns, setTurns] = useStateD(Sim.DEFAULT_TURNS);
  const [seed, setSeed] = useStateD(null);          // null: the server takes it from the list, so reopening the tab gives the same figures
  const [retry, setRetry] = useStateD(0);
  const [wait, setWait] = useStateD(0);             // seconds until the Vault will answer again (HTTP 429), counted down here
  const [run, setRun] = useStateD({ loading: true, answer: null, error: null });
  const stats = useDeckAnswer(() => window.VaultApi.deckStats(text), text);

  useEffectD(() => {
    let stop = false;
    setRun((s) => ({ ...s, loading: true, error: null }));  // the figures already held stay on screen, dimmed
    window.VaultApi.deckSimulate(text, { format, on_the_play: onPlay, turns, games: Sim.GAMES, samples: Sim.SAMPLES, seed })
      .then((answer) => { if (!stop) setRun({ loading: false, answer, error: null }); })
      .catch((e) => {
        if (stop) return;
        const error = Sim.problem(e, turns);
        setRun((s) => ({ loading: false, answer: error.kind === 'toofew' ? null : s.answer, error }));  // too few cards: no figures at all
        if (error.kind === 'limit') setWait(error.seconds);
      });
    return () => { stop = true; };
  }, [text, format, onPlay, turns, seed, retry]);
  useEffectD(() => {
    if (wait <= 0) return undefined;
    const id = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(id);
  }, [wait]);

  const { answer, error, loading } = run;
  const r = answer && answer.result;
  const overview = answer && answer.deck && answer.deck.overview;
  const deckOverview = overview && r ? { ...overview, format: r.format } : overview;
  const locked = loading || wait > 0;
  const again = Sim.playAgain({ busy: loading && !!answer, wait });
  const intro = r && Sim.intro(r);
  const stale = !!answer && !!error && error.kind !== 'toofew';  // the figures shown are the last run that worked

  return (
    <div className="ds">
      <section className="panel" aria-labelledby="ds-h-intro">
        <h2 id="ds-h-intro" className="eyebrow ds-h">Opening turns</h2>
        <p className="ds-meta"><strong>{Sim.deckLine(title, deckOverview)}</strong></p>
        <p className="ds-lead"><strong>{intro ? intro.lead : 'A simulation, not a prediction.'}</strong>{' '}
          {intro ? <>{intro.body} {intro.margin}</> : 'The Vault plays this list many times with a simple player (no opponent) and counts what happened in the first turns.'}</p>
        {r && <p className="ds-asplayed"><strong>As played:</strong> {Sim.asPlayed(r)}</p>}
        <div className="ds-controls">
          <div className="ds-seg" role="group" aria-label="Who goes first">
            <button className={`chip ${onPlay ? 'active' : ''}`} aria-pressed={onPlay} disabled={locked} onClick={() => setOnPlay(true)}>On the play</button>
            <button className={`chip ${onPlay ? '' : 'active'}`} aria-pressed={!onPlay} disabled={locked} onClick={() => setOnPlay(false)}>On the draw</button>
          </div>
          <label className="label-mono ds-field">Turns
            <select className="select" value={turns} disabled={locked} onChange={(e) => setTurns(Number(e.target.value))}>
              {Sim.TURN_CHOICES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="label-mono ds-field">Format
            <select className="select" value={format} disabled={locked} onChange={(e) => setFormat(e.target.value)}>
              {FORMAT_CHOICES.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <button className="btn primary" disabled={again.disabled} onClick={() => setSeed(Sim.newSeed(Math.random))}>{again.label}</button>
        </div>
        <div className="ds-status" role="status">
          {loading && <><span className="spinner" aria-hidden="true"></span> <span className="muted">{answer ? 'Playing again…' : `Playing ${Sim.GAMES.toLocaleString('en-US')} games…`}</span></>}
        </div>
        {r && r.unmatched && r.unmatched.length > 0 && <p className="ds-warn">{Sim.unmatchedLine(r.unmatched)}. {Sim.UNMATCHED_NOTE}</p>}
      </section>

      {error && (
        <div className="panel ds-problem" role="alert">
          <strong className={error.kind === 'toofew' ? '' : 'ds-danger-text'}>{error.title}</strong>{' '}
          {error.kind === 'toofew' ? <>The Vault says: “{error.quote}” {error.advice}</> : error.detail}
          {stale && <> The figures below are from the last run that worked.</>}
          {error.kind === 'other' && <p><button className="btn" disabled={loading} onClick={() => setRetry((n) => n + 1)}>Try again</button></p>}
        </div>
      )}

      {r && r.colour_warning && <div className="ds-notice" role="note">{r.colour_warning}</div>}

      <div className={r && (loading || stale) ? 'ds-body ds-dim' : 'ds-body'} aria-busy={loading && !!answer}>
        {r && (
          <>
            <h2 className="eyebrow ds-h ds-gap">The headline figures</h2>
            <div className="ds-tiles">
              {Sim.tiles(r).map((t) => (
                <div className="ds-tile" key={t.key}><p className="ds-k">{t.label}</p><p className="ds-v">{t.value}</p><p className="ds-u">{t.unit}</p></div>
              ))}
            </div>
          </>
        )}

        <div className="ds-grid2">
          <section className="panel" aria-labelledby="ds-h-curve">
            <h3 id="ds-h-curve" className="eyebrow ds-h">Mana curve <span className="muted">(cards that are not lands, by mana value)</span></h3>
            {stats.result ? <DeckCurve stats={stats.result} full />
              : stats.error ? <p className="ds-sub"><strong className="ds-danger-text">Couldn’t work out the curve:</strong> {stats.error}</p>
              : <p className="ds-sub"><span className="spinner" aria-hidden="true"></span> <span className="muted">Working out the curve…</span></p>}
          </section>
          <section className="panel" aria-labelledby="ds-h-read">
            <h3 id="ds-h-read" className="eyebrow ds-h">How to read this</h3>
            <ul className="ds-assump">{Sim.HOW_TO_READ.map((s) => <li key={s}>{s}</li>)}</ul>
            <p className="ds-sub">These lines are fixed copy about the player; what the simulation does not model is listed at the bottom, from the Vault.</p>
          </section>
        </div>

        {r && (
          <>
            <h2 className="eyebrow ds-h ds-gap">The odds, turn by turn</h2>
            <div className="ds-grid2">
              {Sim.oddsPanels(r).map((p) => (
                <section className="panel" key={p.id} aria-labelledby={`ds-h-${p.id}`}>
                  <h3 id={`ds-h-${p.id}`} className="eyebrow ds-h">{p.title}</h3>
                  <p className="ds-sub">{p.blurb}</p>
                  {p.groups.map((g, i) => (
                    <React.Fragment key={i}>
                      {g.caption && <p className="ds-sub ds-tight">{g.caption}</p>}
                      <ul className={`ds-bars ds-${g.tone}`}>
                        {g.rows.map((row) => (
                          <li key={row.label}>
                            <span className="ds-bl">{row.label}</span>
                            <span className="ds-track" aria-hidden="true"><span className="ds-fill" style={{ width: `${row.width}%` }}></span></span>
                            <span className="ds-bv">{row.value}</span>
                          </li>
                        ))}
                      </ul>
                    </React.Fragment>
                  ))}
                  {p.plan && (
                    <div className="ds-plan" role="note">
                      <strong>{p.plan.title}</strong>
                      <ul>{p.plan.items.map((it) => <li key={`${it.card}-${it.why}`}><span className="ds-cn">{it.card}</span> <span className="ds-why">{it.why}</span></li>)}</ul>
                      <p className="ds-sub">{p.plan.note}</p>
                    </div>
                  )}
                </section>
              ))}
            </div>

            {r.samples.length > 0 && (
              <>
                <h2 className="eyebrow ds-h ds-gap">Sample games, turn by turn</h2>
                <p className="ds-sub">{Sim.samplesIntro(r)}</p>
                {r.samples.map((g, i) => <SampleGame key={`${r.seed}-${i}`} game={g} index={i} r={r} open={i === 0} />)}
              </>
            )}

            <section className="panel ds-end" aria-labelledby="ds-h-not">
              <h3 id="ds-h-not" className="eyebrow ds-h">What this simulation does not do</h3>
              <ul className="ds-assump">{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
              <SimProvenance p={answer.provenance && answer.provenance[0]} />
            </section>
          </>
        )}
      </div>
    </div>
  );
}

// One of the first games of the run, as it came up: its opening hand, then a table of the turns. On a phone each turn becomes a block
// (public/layout.css); the ARIA roles keep the table's meaning when the CSS changes how it is drawn.
function SampleGame({ game, index, r, open }) {
  const Sim = window.VaultDeckSim;
  const s = Sim.gameSummary(game, index);
  const rows = game.turns.map((t) => Sim.turnCells(t, r));
  return (
    <details className="ds-game" open={open}>
      <summary><span>{s.title} <span className="muted">· {s.facts}</span></span></summary>
      <p className="ds-sub"><strong>Opening hand:</strong> {s.hand}</p>
      <table className="ds-turns" role="table" aria-label={`${s.title}, turn by turn`}>
        <thead>
          <tr role="row">{['Turn', 'Drew', 'Land played', 'Mana', 'Cast', 'Hand', 'Notes'].map((h) => <th key={h} scope="col" role="columnheader">{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.turn} role="row">
              <th scope="row" role="rowheader" data-l="Turn">{c.turn}</th>
              <td role="cell" data-l="Drew" className={c.drewNone ? 'muted' : ''}>{c.drew}</td>
              <td role="cell" data-l="Land played" className={c.landNone ? 'muted' : ''}>{c.land}</td>
              <td role="cell" data-l="Mana">{c.mana}</td>
              <td role="cell" data-l="Cast" className={c.castNone ? 'muted' : ''}>{c.cast}</td>
              <td role="cell" data-l="Hand">{c.hand}</td>
              <td role="cell" data-l="Notes" className={c.notes.length ? '' : 'ds-none'}>
                {c.notes.length ? c.notes.map((n) => <span key={n.text} className={`ds-flag ds-f-${n.tone}`}>{n.text}</span>) : <span className="muted">-</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

// Where the figures come from: computed by the Vault, from which source and when; third-party material stays theirs (docs/compliance.md).
function SimProvenance({ p }) {
  const pv = window.VaultDeckSim.provenance(p);
  return (
    <p className="ds-prov">
      {pv.lead}{' '}
      {pv.sources.map((s, i) => (
        <React.Fragment key={i}>
          {i > 0 && '; '}
          {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{window.VaultDeckSim.sourceText(s)}</a> : window.VaultDeckSim.sourceText(s)}
        </React.Fragment>
      ))}
      . {pv.reading} {pv.notice}
    </p>
  );
}
