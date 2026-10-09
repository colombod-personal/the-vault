// "Where to buy" (#212, docs/where-to-buy-design.md): a text-only menu of plain links for a card the person does not own. The server builds
// every link and their order for the country the person saved (GET /buy/menu); the browser contacts no shop, shows no price or image,
// and, while no country is saved, picks the order from its own language without telling the server (public/lib/buy.js). Each link opens
// the shop's own search in a new tab. The country and up to three typed stores are kept in Account, "Where I buy" (BuySettingsSection).
const { useEffect: useEffectBuy, useId: useIdBuy, useRef: useRefBuy, useState: useStateBuy } = React;

let buyCountryList = null;  // the countries to choose from, asked once per page load
function loadBuyCountries() {
  if (!buyCountryList) buyCountryList = window.VaultApi.buyCountries().catch((e) => { buyCountryList = null; throw e; });
  return buyCountryList;
}

function BuyLink({ row, label, note }) {
  return (
    <li>
      <a className="buy-link" href={row.url} target="_blank" rel="noopener noreferrer">
        <span className="buy-link-name">{label || row.name}</span>
        <span className="buy-link-note">{note || row.host}</span>
        <span className="buy-sr"> (opens in a new tab)</span>
      </a>
    </li>
  );
}

function BuyCountryPicker({ initial, onSaved, onCancel }) {
  const Text = window.VaultBuy;
  const id = useIdBuy();
  const [countries, setCountries] = useStateBuy(null);
  const [value, setValue] = useStateBuy(initial || '');
  const [busy, setBusy] = useStateBuy(false);
  const [error, setError] = useStateBuy(null);
  useEffectBuy(() => { let dead = false; loadBuyCountries().then((c) => { if (!dead) setCountries(c); }, (e) => { if (!dead) setError(e.message); }); return () => { dead = true; }; }, []);
  const save = async () => {
    setBusy(true); setError(null);
    try { await window.VaultApi.saveBuySettings({ country: value || null }); onSaved(); } catch (e) { setError(e.message); setBusy(false); }
  };
  return (
    <div className="buy-picker">
      <label className="buy-label" htmlFor={id}>Where do you buy?</label>
      <select id={id} className="select" value={value} disabled={!countries} onChange={(e) => setValue(e.target.value)}>
        {(countries ? Text.countryChoices(countries) : [{ value: '', label: 'Loading countries…' }]).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
      <div className="buy-actions">
        <button type="button" className="btn sm primary" disabled={busy || !countries} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
        <button type="button" className="btn sm" onClick={onCancel}>Not now</button>
      </div>
      {error && <p role="alert" className="buy-error">{error}</p>}
    </div>
  );
}

// The button and its menu. `card` is the card's name; the menu opens in place (no pop-over), so nothing is clipped on a phone.
function BuyMenu({ card, className = '' }) {
  const Text = window.VaultBuy;
  const id = useIdBuy();
  const button = useRefBuy(null);
  const [open, setOpen] = useStateBuy(false);
  const [state, setState] = useStateBuy({ menu: null, error: null });
  const [more, setMore] = useStateBuy(false);
  const [picking, setPicking] = useStateBuy(false);
  const [tick, setTick] = useStateBuy(0);
  useEffectBuy(() => {
    if (!open) return undefined;
    let dead = false;
    setState((s) => ({ menu: s.menu, error: null }));
    window.VaultApi.buyMenu(card).then((menu) => { if (!dead) setState({ menu, error: null }); }, (e) => { if (!dead) setState({ menu: null, error: e.message }); });
    return () => { dead = true; };
  }, [open, card, tick]);
  const close = () => { setOpen(false); setPicking(false); setMore(false); if (button.current) button.current.focus(); };
  const menu = state.menu;
  const region = Text.regionOfLanguage(navigator.language);
  const arranged = menu ? Text.arrange(menu, region) : null;
  return (
    <div className={`buy ${className}`} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (e.key === 'Escape' && open) { e.stopPropagation(); close(); } }}>
      <button type="button" ref={button} className="btn sm buy-button" aria-expanded={open} aria-controls={id} onClick={() => (open ? close() : setOpen(true))}>
        Where to buy<span className="buy-sr">: {card}</span><span aria-hidden="true" className="buy-chev">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div id={id} className="buy-menu" role="group" aria-label={`Where to buy ${card}`}>
          {!menu && !state.error && <p className="buy-line" role="status">Loading the shops…</p>}
          {state.error && (
            <p className="buy-error" role="alert">Couldn't load the shops: {state.error}{' '}
              <button type="button" className="btn xs" onClick={() => setTick(tick + 1)}>Try again</button></p>
          )}
          {menu && (
            <>
              <p className="buy-line">
                {Text.shopsLine(menu, arranged)}{' '}
                {!picking && <button type="button" className="btn xs buy-change" onClick={() => setPicking(true)}>{Text.changeLabel(arranged)}</button>}
              </p>
              {picking && <BuyCountryPicker initial={menu.country || (arranged.source === 'browser' ? arranged.region : '')}
                                            onSaved={() => { setPicking(false); setTick(tick + 1); }} onCancel={() => setPicking(false)} />}
              <ul className="buy-list" aria-label={`Where to buy ${card}`}>
                {menu.my_stores.map((s) => <BuyLink key={s.id} row={s} label={Text.storeLabel(s)} note={`${Text.storeNote(s)} · ${s.host} · typed by you`} />)}
                {arranged.shops.map((s) => <BuyLink key={s.id} row={s} />)}
                <BuyLink row={{ url: menu.locator.url }} label={Text.LOCATOR_LABEL} note={Text.LOCATOR_NOTE} />
              </ul>
              {arranged.more.length > 0 && (
                <>
                  <button type="button" className="btn xs buy-more" aria-expanded={more} onClick={() => setMore(!more)}>{more ? 'Fewer shops' : 'More shops'}</button>
                  {more && <ul className="buy-list">{arranged.more.map((s) => <BuyLink key={s.id} row={s} />)}</ul>}
                </>
              )}
              <ul className="buy-list buy-scry"><BuyLink row={menu.scryfall} label="Scryfall card page" note="its own buy links" /></ul>
              <p className="buy-fine">{menu.notice}</p>
              <details className="buy-fine">
                <summary>About these links</summary>
                <p>{menu.prices} Shop names belong to those shops. {menu.region_note}</p>
                <ul className="buy-about">
                  {[...menu.shops, ...menu.more_shops].map((s) => (
                    <li key={s.id}>{s.name}: the search link was checked on {s.link_format_checked}; its <a href={s.terms} target="_blank" rel="noopener noreferrer">terms</a>.</li>
                  ))}
                  <li>The Wizards Store &amp; Event Locator is Wizards of the Coast's; its link was checked on {menu.locator.link_format_checked}; its <a href={menu.locator.terms} target="_blank" rel="noopener noreferrer">terms</a>.</li>
                </ul>
              </details>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Account, "Where I buy": the country and up to three stores the person typed. Saved with their account, exported and erased with it.
function BuySettingsSection() {
  const Text = window.VaultBuy;
  const api = window.VaultApi;
  const [saved, setSaved] = useStateBuy(null);
  const [countries, setCountries] = useStateBuy([]);
  const [country, setCountry] = useStateBuy('');
  const [stores, setStores] = useStateBuy([]);
  const [error, setError] = useStateBuy(null);
  const [note, setNote] = useStateBuy(null);
  const [busy, setBusy] = useStateBuy(false);
  const apply = (s) => { setSaved(s); setCountry(s.country || ''); setStores(s.stores.map((x) => ({ name: x.name, url: x.url, search_url: x.search_url || '' }))); };
  useEffectBuy(() => {
    api.buySettings().then(apply, (e) => setError(e.message));
    loadBuyCountries().then(setCountries, () => {});
  }, []);
  const limits = saved ? saved.limits : { stores: 3 };
  const edit = (i, field, value) => setStores(stores.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
  const save = async () => {
    setBusy(true); setError(null); setNote(null);
    try { apply(await api.saveBuySettings({ country: country || null, stores: Text.storesForSave(stores) })); setNote('Saved.'); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const removeAll = async () => {
    setBusy(true); setError(null); setNote(null);
    try { await api.removeBuySettings(); apply(await api.buySettings()); setNote('Removed. Nothing about where you buy is kept.'); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <Section title="Where I buy">
      <p className="label-mono" style={{ marginBottom: 8 }}>
        Orders the shops in the “Where to buy” menu for your country and puts your own stores first. The Vault never guesses your country from
        your connection, never contacts a shop, and keeps only what you enter here. It is saved with your account, included in “Download my data”
        and deleted with the account.
      </p>
      {!saved && !error && <p className="label-mono" role="status">Loading…</p>}
      {saved && (
        <div className="buy-form">
          <label className="label-mono" htmlFor="buy-country">Country</label>
          <select id="buy-country" className="select" value={country} onChange={(e) => setCountry(e.target.value)}>
            {Text.countryChoices(countries).map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          <p className="label-mono">My stores (up to {limits.stores}, typed by you; shown as links, never opened by the Vault)</p>
          {stores.map((s, i) => (
            <fieldset key={i} className="buy-store">
              <legend className="label-mono">Store {i + 1}</legend>
              <label className="label-mono" htmlFor={`buy-name-${i}`}>Name</label>
              <input id={`buy-name-${i}`} className="input" value={s.name} maxLength={80} onChange={(e) => edit(i, 'name', e.target.value)} />
              <label className="label-mono" htmlFor={`buy-url-${i}`}>Web address (https only)</label>
              <input id={`buy-url-${i}`} className="input" value={s.url} maxLength={300} inputMode="url" autoCapitalize="none" placeholder="https://" onChange={(e) => edit(i, 'url', e.target.value)} />
              <label className="label-mono" htmlFor={`buy-search-${i}`}>Search address (optional)</label>
              <input id={`buy-search-${i}`} className="input" value={s.search_url} maxLength={300} inputMode="url" autoCapitalize="none" placeholder="https://…?q={card}" onChange={(e) => edit(i, 'search_url', e.target.value)} />
              <p className="label-mono buy-hint">Paste it from the store's own search page and put {'{card}'} where the card's name goes. Without it the menu opens the store's page.</p>
              <button type="button" className="btn xs ghost" onClick={() => setStores(stores.filter((_, j) => j !== i))}>Remove this store</button>
            </fieldset>
          ))}
          <div className="buy-actions">
            {stores.length < limits.stores && <button type="button" className="btn sm" onClick={() => setStores([...stores, Text.emptyStore()])}>Add a store</button>}
            <button type="button" className="btn sm primary" disabled={busy} onClick={save}>Save</button>
            <button type="button" className="btn sm ghost" disabled={busy || (!saved.country && saved.stores.length === 0)} onClick={removeAll}>Remove</button>
          </div>
          {note && <p role="status" className="label-mono" style={{ color: 'var(--good)' }}>{note}</p>}
        </div>
      )}
      {error && <p role="alert" style={{ color: 'var(--danger)' }}>{error}</p>}
    </Section>
  );
}
