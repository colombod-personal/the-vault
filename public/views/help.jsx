// In-app help: a Help view (#/help, #/help/<section>), a "?" on every main view that opens its
// section, and a first-visit welcome. The text states what the app does today; when a view changes,
// change its section here (tests/test_help.py checks that every view has a section and every link
// lands on one).
const { useState: useStateH, useEffect: useEffectH, useRef: useRefH } = React;

// One section per topic. `views` lists the views whose "?" opens it.
// The names of buttons, tabs, modes and Account sections are written in curly quotes (“Save to your decks”) exactly
// as the app labels them; tests/test_help.py finds every one of them in the views, so a rename there fails there.
const HELP_SECTIONS = [
  {
    id: 'import', title: 'Import your collection', views: ['dashboard'],
    body: [
      'Export a CSV from the Dragon Shield Card Manager (Inventory, Export) or from Moxfield (Collection, More, Export CSV) and choose it with “Import CSV” (top right) or, in an empty vault, “Choose CSV file”. The format is detected for you, and only the file you choose is read: the Vault never connects to your accounts there.',
      'Import again any time. The Vault applies only what changed in your app since the last import and keeps edits you made through an assistant. If the same card changed in both places, your assistant shows it in the preview and asks; the edit made here is kept unless you say otherwise. Each import records what changed, so you can see copies added, removed and changed.',
      'If an assistant you allowed to make changes edits which cards you own, that change is listed under “Collection history” in Account, with the app that made it. The latest one can be reverted there with “Undo” (you see what it will change first), until your collection changes again.',
    ],
  },
  {
    id: 'collection', title: 'Your collection and prices', views: ['browse', 'valuation'],
    body: [
      'Browse lists every printing you own. Search by card name or set, filter by set or printing, and sort by total value, quantity, name, or newest or oldest first. Open a card to see your copies of it, every printing you own, its text and its Scryfall prices.',
      'Prices are Scryfall\'s market prices in US dollars, refreshed daily and dated. “Spent” is what you paid, and profit and loss (“P&L”) counts only the copies where a price paid is known. Open “Market value” on the Vault page to see how your collection\'s value moved, by month and by day.',
    ],
  },
  {
    id: 'sets', title: 'Sets', views: ['sets'],
    body: [
      'Sets lists every set you own cards from, with how many cards and unique printings you own there and what they are worth. Search the list, sort it (by value, card count, release date, code or name) and open a set to see the cards you own from it, most valuable first.',
    ],
  },
  {
    id: 'decks', title: 'Decks', views: ['decks'],
    body: [
      'Add a deck from a public Archidekt link (“From a link”) or by pasting a list (“Paste a list”), then press “Save to your decks” to keep it; a saved deck offers “Update saved copy” and “Remove” instead. The Vault reads an Archidekt deck only when you ask, keeps a copy for ten minutes, and “Refresh” asks Archidekt again. It never searches or crawls Archidekt, and it never changes anything there: changes to the deck are made on Archidekt.',
      'A deck shows which cards you own (“Have”), partly own (“Partial”) or are missing (“Need”), with copy counts. The tabs are “Cards”, “Stats” (counts, mana curve, roles), “Legality”, “Upgrades”, “Combos” and “Buy list”, and, for a saved deck, “History”: the deck\'s earlier lists, kept each time its cards change (the last 20), with what was added or cut, and a way to read an older list. When a saved deck has changed since you last opened it, a note says “Changed since you last looked” with the cards added and cut. The buy list is plain text (“Copy list”) you can paste into a shop\'s own list tool: the Vault does not contact shops and shows prices as Scryfall\'s. The Where to buy menu on a card you do not own opens a list of plain links: the shops for your country, the stores you typed in Account (under Where I buy), Wizards\' official store locator and Scryfall. Each link opens that site in a new tab; the Vault contacts none of them, shows no shop price and earns nothing from a click. Your country is only what you choose; until you do, your browser\'s language orders the shops on your device and nothing is saved.',
    ],
  },
  {
    id: 'lab', title: 'Lab', views: ['lab'],
    body: [
      'The Lab helps you decide what to buy, sell or keep. Three counters at the top say how many decks need a purchase, how many cards you could sell and your biggest known loss and gain; each one takes you to its section.',
      '“Buy” says whether your saved decks can all be built at the same time from the copies you own, which cards to buy (cheapest first), which of them could be moved from another deck instead, and what each deck lacks. “Copy shopping list” copies the cards to buy as text you can paste into a shop\'s own list tool: the Vault does not contact shops. “Spare copies” lists the copies beyond what your saved decks need, with what they are worth and which copies they are; it does not know decks you have not saved, so spare never means worthless to you. “Profit and loss” lists your “Winners” and “Losers” against what you paid (only copies with a price paid and a current price count), and “Value over time” shows what your collection was worth each day. A shared collection has no Lab. Every number is computed by the server, and prices are Scryfall\'s, dated.',
    ],
  },
  {
    id: 'ideas', title: 'Ideas', views: ['ideas'],
    body: [
      'Ideas shows, for one of your saved decks, what your collection covers, what is missing and what another deck is holding. Pick a deck on the start page (Ideas explores saved decks only, so save one on the Decks page first). The header gives the deck\'s format, commander and colours and says how many cards are covered, missing and borrowed. The cards sit in lanes by job (ramp, draw, removal, sweepers, counterspells, tutors, recursion, sacrifice outlets, other and lands), each card in one lane. On a phone, “Missing”, “Borrowed” and “All” choose which cards to list first, and a lane opens when you tap it.',
      'Pick a card to see which cards you own that do the same job in the deck\'s colours and format, with why they match. A card another deck holds is marked borrowed. “Swap into the deck” opens the deck page with that swap proposed, “Move” explains how to take a card from the deck that holds it, and Buy opens the card on Scryfall with its cheapest known price and the day of that price; your buy list across all decks is in the Lab. Ideas changes nothing: a swap is applied only when you confirm it on the deck page. The Where to buy menu on a card you do not own opens a list of plain links: the shops for your country, the stores you typed in Account (under Where I buy), Wizards\' official store locator and Scryfall. Each link opens that site in a new tab; the Vault contacts none of them, shows no shop price and earns nothing from a click. Your country is only what you choose; until you do, your browser\'s language orders the shops on your device and nothing is saved. “Show combos you already own” asks Commander Spellbook, with the deck\'s card names, only when you press it.',
      '“Clear” steps back out to the list of decks, and so do Esc and the browser\'s Back button, one step at a time: from a card to the deck, then to the list. The roles are the Vault\'s eight coarse ones, so a match is a hint, not proof that two cards play alike. Every number is computed by the server and prices are Scryfall\'s, dated. A shared collection has no Ideas.',
    ],
  },
  {
    id: 'sharing', title: 'Sharing', views: [],
    body: [
      'Your collection and decks are private. Open Account (your name, top right): under “Share your collection”, “Create invite link for my collection” makes a link that gives one person read-only access, works once, and can be revoked at any time. Tick “Include what I paid for cards” if they may see what you paid. You can also share a single deck from “Saved decks”.',
      'When someone shares with you, open their link while signed in and find it under “Shared with me” in Account.',
    ],
  },
  {
    id: 'assistant', title: 'Connect an AI assistant', views: [],
    body: [
      'Use the Vault from Claude or ChatGPT: ask about your collection and decks, check rules with cited sources, and have an expert council review a deck. In Claude or ChatGPT you add The Vault and sign in with your Vault account: no token needed. You choose whether it may edit your collection and decks, and you can disconnect it any time under “Connected apps” in Account.',
      'For Claude Code, Codex, Cursor or VS Code, create a personal access token under “Agents & API” in Account. The connect page has the steps for each.',
    ],
    links: [{ href: '/connect.html', label: 'Open the connect page' }],
  },
  {
    id: 'privacy', title: 'Privacy and your data', views: [],
    body: [
      'Your data is yours. Under “Your data” in Account, you can download everything the Vault holds about you as a zip, and delete your account and all its data.',
      'Card data and images come from Scryfall, and Archidekt decks are credited to their authors. The Vault is unofficial fan content, not endorsed by Wizards of the Coast.',
    ],
    links: [{ href: '/privacy.html', label: 'Privacy notice' }, { href: '/credits.html', label: 'Credits' }],
  },
];

// The section a view's "?" opens (a view with no section of its own maps to its nearest one).
const HELP_FOR_VIEW = {
  dashboard: 'import', browse: 'collection', setdetail: 'sets', sets: 'sets', decks: 'decks',
  lab: 'lab', ideas: 'ideas', valuation: 'collection',
};

const helpHashFor = (id) => '#/help' + (id ? '/' + encodeURIComponent(id) : '');

// "?" on a main view: opens that view's help section. A real link, so it works by keyboard and touch
// and the browser's Back button returns to the view.
function HelpHint({ view, shared }) {
  // Someone else's collection is read-only: its page explains Sharing, not Import.
  const id = shared && view === 'dashboard' ? 'sharing' : HELP_FOR_VIEW[view];
  if (!id) return null;
  return (
    <a className="help-hint" href={helpHashFor(id)} aria-label="Help for this page" title="Help for this page">?</a>
  );
}

function Help({ section }) {
  const headingRef = useRefH(null);
  useEffectH(() => {
    // Focus moves to the opened section's heading, and the page scrolls so the heading is at the top, clear of the top bar
    // (focus alone scrolls the least it can: on a phone the heading ended up under the bottom tab bar).
    const el = document.getElementById('help-' + section);
    if (el) { el.setAttribute('tabindex', '-1'); el.focus({ preventScroll: true }); el.scrollIntoView({ block: 'start' }); }
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
