// What Vercel is told about a page: its address without the query or the hash. Invitation links to a shared collection, sign-in
// returns and deck links can carry tokens or ids there, so nothing after the path is ever sent.
export function cleanUrl(url) {
  try {
    const u = new URL(url);
    return u.origin + u.pathname;
  } catch {
    return '';
  }
}

// A visitor who sent Do Not Track or Global Privacy Control is not measured at all.
export function measured(nav) {
  return !(nav && (nav.globalPrivacyControl === true || nav.doNotTrack === '1' || nav.doNotTrack === 'yes'));
}

// The route Speed Insights files a page under. The app keeps its views in the hash (#/browse), which is never sent; this sends only
// the NAME of the view, and only when it is one of the app's own views: never a set code, deck id, token or anything typed.
export const VIEWS = ['dashboard', 'browse', 'sets', 'decks', 'lab', 'ideas', 'valuation', 'help'];
export function routeOf(loc) {
  const path = (loc && loc.pathname) || '/';
  if (path !== '/') return path;
  const first = String((loc && loc.hash) || '').replace(/^#[/]?/, '').split('/')[0].split('?')[0];
  return VIEWS.includes(first) ? '/' + first : '/';
}
