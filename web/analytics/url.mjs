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
