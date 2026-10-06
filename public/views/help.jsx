// In-app help: a Help view (#/help, #/help/<section>), a "?" on every main view that opens its
// section, and a first-visit welcome. The text states what the app does today; when a view changes,
// change its section here (tests/test_help.py checks that every view has a section and every link
// lands on one).
const { useState: useStateH, useEffect: useEffectH, useRef: useRefH } = React;

// One section per topic. `views` lists the views whose "?" opens it.
const HELP_SECTIONS = [
  {
    id: 'import', title: 'Import your collection', views: ['dashboard'],
    body: [
      'Export a CSV from the Dragon Shield Card Manager (Inventory, Export) or from Moxfield (Collection, More, Export CSV) and choose it with Import. The format is detected for you, and only the file you choose is read: the Vault never connects to your accounts there.',
      'Import again any time. Each import replaces the collection with the new file and records what changed, so you can see copies added, removed and changed. Cards the Vault could not match to a printing are kept and marked, so nothing is silently dropped.',
    ],
  },
  {
    id: 'collection', title: 'Your collection and prices', views: ['browse', 'valuation'],
    body: [
      'Browse lists every printing you own. Search by name or set and sort by value. Open a card to see its copies, its price history and its card text.',
      'Prices are Scryfall\'s market prices in US dollars, refreshed daily and dated. "Paid" is what you paid, and profit and loss counts only the copies where a price paid is known. The Value view shows how your collection\'s value has moved over time.',
    ],
  },
  {
    id: 'sets', title: 'Sets', views: ['sets'],
    body: [
      'Sets shows how much of each set you own and what it is worth. Open a set to see its cards, owned and not owned.',
    ],
  },
  {
    id: 'decks', title: 'Decks', views: ['decks'],
    body: [
      'Add a deck from an Archidekt link or by pasting a list, then press Save deck to keep it. The Vault reads a public Archidekt deck only when you ask; it never searches or crawls Archidekt, and it never changes anything there.',
      'A deck shows which cards you own, partly own or are missing, with copy counts. The tabs cover stats and roles, legality, upgrade ideas, combos and a buy list. The buy list is plain text you can paste into a shop\'s own list tool: the Vault does not contact shops and shows prices as Scryfall\'s, with their date.',
    ],
  },
  {
    id: 'lab', title: 'Lab', views: ['lab'],
    body: [
      'The Lab shows what your numbers say: biggest gains and losses against what you paid, the cards you hold the most copies of, and your collection by colour, type and mana value. All numbers are computed by the server.',
    ],
  },
  {
    id: 'graph', title: 'Graph', views: ['graph'],
    body: [
      'The Graph draws your collection as a map of cards. It is being replaced by a deck ideas view that shows what a deck is missing and what you own that could stand in.',
    ],
  },
  {
    id: 'sharing', title: 'Sharing', views: [],
    body: [
      'Your collection and decks are private. In Account, Share your collection creates an invite link that gives one person read-only access, works once, and can be revoked at any time. You choose whether it includes what you paid. You can also share a single deck from Saved decks.',
      'When someone shares with you, open their link while signed in and find it under Account, Shared with me.',
    ],
  },
  {
    id: 'assistant', title: 'Connect an AI assistant', views: [],
    body: [
      'You can let your own AI assistant read your collection and decks, with sources shown. Sign-in is either OAuth in your assistant or a personal access token you create under Account, Agents & API, and either can be read-only and revoked.',
      'Setup steps for each assistant are on the connect page.',
    ],
    links: [{ href: '/connect.html', label: 'Open the connect page' }],
  },
  {
    id: 'privacy', title: 'Privacy and your data', views: [],
    body: [
      'Your data is yours. Under Account, Your data, you can download everything the Vault holds about you as a zip, and delete your account and all its data.',
      'Card data and images come from Scryfall, and Archidekt decks are credited to their authors. The Vault is unofficial fan content, not endorsed by Wizards of the Coast.',
    ],
    links: [{ href: '/privacy.html', label: 'Privacy notice' }, { href: '/credits.html', label: 'Credits' }],
  },
];

// The section a view's "?" opens (a view with no section of its own maps to its nearest one).
const HELP_FOR_VIEW = {
  dashboard: 'import', browse: 'collection', setdetail: 'sets', sets: 'sets', decks: 'decks',
  lab: 'lab', graph: 'graph', valuation: 'collection',
};

const helpHashFor = (id) => '#/help' + (id ? '/' + encodeURIComponent(id) : '');

// "?" on a main view: opens that view's help section. A real link, so it works by keyboard and touch
// and the browser's Back button returns to the view.
function HelpHint({ view }) {
  const id = HELP_FOR_VIEW[view];
  if (!id) return null;
  return (
    <a className="help-hint" href={helpHashFor(id)} aria-label="Help for this page" title="Help for this page">?</a>
  );
}

function Help({ section }) {
  const headingRef = useRefH(null);
  useEffectH(() => {
    // Focus moves to the opened section's heading; the page scrolls to it.
    const el = document.getElementById('help-' + section);
    if (el) { el.setAttribute('tabindex', '-1'); el.focus({ preventScroll: false }); }
    else if (headingRef.current) headingRef.current.focus();
  }, [section]);
  return (
    <div className="help">
      <p className="eyebrow">Help</p>
      <h1 className="h1" tabIndex={-1} ref={headingRef} style={{ marginTop: 6 }}>How to use the Vault</h1>
      <nav className="help-toc" aria-label="Help topics">
        {HELP_SECTIONS.map((s) => (
          <a key={s.id} href={helpHashFor(s.id)} aria-current={s.id === section ? 'true' : undefined}>{s.title}</a>
        ))}
      </nav>
      {HELP_SECTIONS.map((s) => (
        <section key={s.id} className="help-section" aria-labelledby={'help-' + s.id}>
          <h2 className="h2" id={'help-' + s.id}>{s.title}</h2>
          {s.body.map((p, i) => <p key={i}>{p}</p>)}
          {(s.links || []).map((l) => <p key={l.href}><a href={l.href}>{l.label}</a></p>)}
        </section>
      ))}
    </div>
  );
}

const VAULT_WELCOME_KEY = 'vault_welcome_done';
const welcomeSeen = () => { try { return localStorage.getItem(VAULT_WELCOME_KEY) === '1'; } catch { return false; } };
const welcomeDone = () => { try { localStorage.setItem(VAULT_WELCOME_KEY, '1'); } catch {} };

// A first-visit banner after sign-in: three steps, dismissible, remembered. It sits above the page
// and never covers it.
function Welcome({ hasCollection, onDismiss }) {
  return (
    <aside className="welcome" role="region" aria-label="Welcome to the Vault">
      <p className="eyebrow">Welcome</p>
      <ol>
        <li>{hasCollection ? 'Import: done.' : 'Import your collection (the Import button, top right).'}</li>
        <li>Explore: browse your cards, sets and decks.</li>
        <li>Connect your AI assistant: <a href="#/help/assistant">how</a>.</li>
      </ol>
      <p><a href="#/help">Open the help</a>
        <button className="btn xs ghost" onClick={onDismiss} aria-label="Dismiss the welcome">Got it</button></p>
    </aside>
  );
}

Object.assign(window, { Help, HelpHint, Welcome, HELP_SECTIONS, HELP_FOR_VIEW, helpHashFor, welcomeSeen, welcomeDone });
