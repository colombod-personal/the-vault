// What a card does (#435, docs/functional-equivalents.md section 13): the Vault's own reading of a card's Oracle text as a short list of
// points ("Doubles tokens", "Counters a spell"), in three places: the card panel (CardRoles), Browse's filter (RoleFilter) and the deck
// page's "What it does" tab (DeckRoles). The server reads the text when the catalog loads and stores the roles with the card; this file
// only shows what GET /catalog/cards/roles, /collection/roles and POST /decks/roles answer. Every place carries the server's label,
// "The Vault's reading of the card text, not an official classification": the roles are not Scryfall's tags and not a classification
// by Wizards, and a rule can miss or overreach, so each role names the rule that found it (a "Why?" line, and on hover).
// Scryfall's Tagger tags are a different list and are labelled as a community's opinion wherever they appear.
const { useEffect: useEffectR, useMemo: useMemoR, useState: useStateR } = React;

const ROLES_LABEL = "The Vault's reading of the card text, not an official classification";
const ROLES_MAX = 22;  // the vocabulary's size (the server's): a request names at most this many roles

// One role of a card as a row: its point, whether it is the card's job or a side effect, and (pressed) the rule that found it.
function RoleRow({ role, onFind, cardsLabel }) {
  const [open, setOpen] = useStateR(false);
  return (
    <li className="role-item">
      <button type="button" className="role-row" aria-expanded={open} title={role.why} onClick={() => setOpen(!open)}>
        <span className="role-point">{role.point}</span>
        <span className={`role-strength ${role.strength}`}>{role.strength === 'core' ? 'main job' : 'on the side'}</span>
        <span className="role-more" aria-hidden="true">{open ? 'Hide' : 'Why?'}</span>
      </button>
      {open && (
        <div className="role-why">
          <p>{role.why}</p>
          <p className="muted">{role.name}: {role.means}.</p>
          {onFind && <button type="button" className="btn xs" onClick={() => onFind(role.role)}>{cardsLabel || 'Show my cards that do this'}</button>}
        </div>
      )}
    </li>
  );
}

// The card panel's "What this card does". `name` is the card's name; `onFind(role)` (optional) opens Browse filtered to that role.
function CardRoles({ name, onFind }) {
  const [state, setState] = useStateR({ loading: true });
  useEffectR(() => {
    let dead = false;
    setState({ loading: true });
    window.VaultApi.cardRoles(name).then((a) => { if (!dead) setState({ answer: a }); },
      (e) => { if (!dead) setState({ error: e }); });
    return () => { dead = true; };
  }, [name]);
  const a = state.answer;
  if (state.error && state.error.status === 404) return null;  // a name the catalog does not know (a token, a typo): nothing to say
  return (
    <div className="panel role-panel" style={{ marginBottom: 20 }} aria-label="What this card does">
      <p className="eyebrow" style={{ marginBottom: 8 }}>What this card does</p>
      {state.loading && <p className="muted" style={{ fontSize: 12 }}><span className="spinner spinner-xs"></span> Reading the card text…</p>}
      {state.error && !state.loading && <p className="muted" style={{ fontSize: 12 }}>Couldn't read what this card does: {state.error.message}</p>}
      {a && !a.card && <p className="muted" style={{ fontSize: 12 }}>The card catalog does not have this card.</p>}
      {a && a.card && a.roles.length > 0 && (
        <ul className="role-list">
          {a.roles.map((r) => <RoleRow key={r.role} role={r} onFind={onFind} />)}
        </ul>
      )}
      {a && a.card && a.roles.length === 0 && <p style={{ fontSize: 13, lineHeight: 1.5 }}>{a.message}</p>}
      {a && a.card && (
        <>
          <p className="role-label">{a.label}.</p>
          {a.community_tags.length > 0 && (
            <p className="role-label">
              {a.community_tags_label}: {a.community_tags.map((t) => t.label || t.tag).join(', ')}.
            </p>
          )}
        </>
      )}
    </div>
  );
}

// Browse's filter by what a card does: a button that opens the 22 roles with how many of your cards have each, the chosen roles as
// removable chips, and "All of them" / "Any of them" when more than one is chosen. The list it filters is the server's
// (GET /collection/cards?role=...), so the count Browse already shows ("N entries match") says how many entries match.
function RoleFilter({ api, version, value, onChange, match, onMatch }) {
  const [open, setOpen] = useStateR(false);
  const counts = window.useVaultQuery(() => api.roles(), [api.base, version]);
  const items = counts.data ? counts.data.items : [];
  const byRole = useMemoR(() => Object.fromEntries(items.map((r) => [r.role, r])), [items]);
  const toggle = (slug) => onChange(value.includes(slug) ? value.filter((s) => s !== slug) : [...value, slug].slice(0, ROLES_MAX));
  const label = (slug) => (byRole[slug] ? byRole[slug].point : slug);
  return (
    <div className="role-filter">
      <div className="role-filter-bar">
        <button type="button" className={`btn${value.length ? ' primary' : ''}`} aria-expanded={open} aria-controls="role-picker" onClick={() => setOpen(!open)}>
          What it does{value.length ? ` (${value.length})` : ''} {open ? '▴' : '▾'}
        </button>
        {value.map((slug) => (
          <button key={slug} type="button" className="chip active role-chosen" onClick={() => toggle(slug)} aria-label={`Remove the filter: ${label(slug)}`}>
            {label(slug)} <span aria-hidden="true">×</span>
          </button>
        ))}
        {value.length > 0 && <button type="button" className="btn xs ghost" onClick={() => onChange([])}>Clear</button>}
      </div>
      {open && (
        <div className="role-picker" id="role-picker" role="group" aria-label="Filter by what the card does">
          {counts.error && <p role="alert" className="muted" style={{ fontSize: 12 }}>Couldn't load the roles: {counts.error.message}</p>}
          <div className="role-grid">
            {items.map((r) => (
              <button key={r.role} type="button" className={`chip role-toggle${value.includes(r.role) ? ' active' : ''}`} aria-pressed={value.includes(r.role)}
                      title={r.means} onClick={() => toggle(r.role)}>
                <span className="role-toggle-point">{r.point}</span> <span className="role-toggle-count">{r.cards.toLocaleString()}</span>
              </button>
            ))}
          </div>
          {value.length > 1 && (
            <div className="role-match" role="group" aria-label="With several roles">
              <button type="button" className={`chip${match === 'all' ? ' active' : ''}`} aria-pressed={match === 'all'} onClick={() => onMatch('all')}>All of them</button>
              <button type="button" className={`chip${match === 'any' ? ' active' : ''}`} aria-pressed={match === 'any'} onClick={() => onMatch('any')}>Any of them</button>
            </div>
          )}
          {counts.data && counts.data.note && <p className="muted" style={{ fontSize: 12 }}>{counts.data.note}</p>}
          <p className="role-label">{counts.data ? counts.data.label : ROLES_LABEL}. The number is how many different cards you own with the role.</p>
        </div>
      )}
    </div>
  );
}

// The deck page's "What it does": every role with how many of the deck's cards have it; pressing a role lists those cards, each a
// button that opens the card's panel. A role with no card is shown with 0 so a gap is visible. `rows` are the page's deck rows and
// `cardFor(row)` builds the card the drawer opens (deck.jsx), matched to an answer's card by its front-face name.
function DeckRoles({ text, rows, cardFor, openCard }) {
  const [state, setState] = useStateR({ loading: true });
  const [openRole, setOpenRole] = useStateR(null);
  useEffectR(() => {
    let dead = false;
    setState({ loading: true });
    window.VaultApi.deckRoles(text).then((a) => { if (!dead) setState({ answer: a }); }, (e) => { if (!dead) setState({ error: e.message }); });
    return () => { dead = true; };
  }, [text]);
  const front = (n) => String(n || '').split(' // ')[0].trim().toLowerCase();
  const rowOf = useMemoR(() => Object.fromEntries((rows || []).map((r) => [front(r.name), r])), [rows]);
  if (state.loading) return <div className="panel"><span className="spinner"></span> <span className="muted">Reading what the deck does…</span></div>;
  if (state.error) return <div className="panel"><strong style={{ color: 'var(--danger)' }}>Couldn't work out what the deck does:</strong> {state.error}</div>;
  const r = state.answer.result;
  const present = r.roles.filter((x) => !x.empty).length;
  return (
    <div className="panel deck-roles">
      <p className="eyebrow">What this deck does</p>
      <p style={{ fontSize: 13, lineHeight: 1.5, margin: '6px 0 10px' }} aria-live="polite">
        {present} of {r.roles.length} jobs are covered by {r.distinct_cards} different cards. Press a job to see its cards; a job with 0 is a gap.
      </p>
      <ul className="role-list">
        {r.roles.map((x) => {
          const on = openRole === x.role;
          return (
            <li key={x.role} className={`role-item${x.empty ? ' empty' : ''}`}>
              <button type="button" className="role-row" aria-expanded={on} onClick={() => setOpenRole(on ? null : x.role)} title={x.means}>
                <span className="role-point">{x.point}</span>
                <span className="role-count" aria-label={`${x.cards} ${x.cards === 1 ? 'card' : 'cards'}`}>{x.cards}</span>
              </button>
              {on && (
                <div className="role-why">
                  {x.empty ? <p className="muted">No card in this deck does this. {x.means}. The Vault reads 22 common jobs and misses some wordings, so this means its rules found nothing.</p> : (
                    <ul className="role-cards">
                      {x.items.map((c) => {
                        const row = rowOf[front(c.card)];
                        return (
                          <li key={c.oracle_id}>
                            {row
                              ? <button type="button" className="row-link" onClick={() => openCard(cardFor(row))}>{c.card}</button>
                              : <span className="role-card-name">{c.card}</span>}
                            {c.copies > 1 && <span className="muted"> ×{c.copies}</span>}
                            <span className={`role-strength ${c.strength}`}>{c.strength === 'core' ? 'main job' : 'on the side'}</span>
                            <span className="role-rule muted" title={`Found by the Vault's rule ${c.rule}`}>rule {c.rule}</span>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {r.without_role.length > 0 && (
        <p className="muted" style={{ fontSize: 12, lineHeight: 1.5, marginTop: 10 }}>
          No role known for {r.without_role.length} {r.without_role.length === 1 ? 'card' : 'cards'} (not lands): {r.without_role.map((c) => c.card).join(', ')}. That is not the same as the card doing nothing.
        </p>
      )}
      {r.unmatched.length > 0 && <p style={{ fontSize: 12, color: 'var(--gold)', marginTop: 6 }}>Not in the card catalog: {r.unmatched.join(', ')}</p>}
      <p className="role-label">{r.label}. {r.note}</p>
    </div>
  );
}

Object.assign(window, { CardRoles, RoleFilter, DeckRoles });
