// What a "Link Google" (etc.) round trip came back with, in words. The server redirects to
//   /?linked=PROVIDER[&empty_account=removed|kept]      linked (moved from an empty account)
//   /?link_error=CODE&provider=PROVIDER                  not linked
// noticeFromSearch(search) returns { text, tone: 'ok' | 'danger', params } or null; `params`
// is the query without these keys, so the caller can clean the URL. Wording only: the server
// decides what was linked.
(function () {
  const NAMES = { google: 'Google', microsoft: 'Microsoft', apple: 'Apple', facebook: 'Facebook' };
  const KEYS = ['linked', 'empty_account', 'link_error', 'provider'];

  function noticeFromSearch(search) {
    const params = new URLSearchParams(search || '');
    const linked = params.get('linked');
    const error = params.get('link_error');
    if (!linked && !error) return null;
    const provider = linked || params.get('provider');
    const name = NAMES[provider];
    const who = name || 'That sign-in';
    let text, tone = 'ok';
    if (error === 'identity_in_use') {
      tone = 'danger';
      text = `${who} already has its own Vault account with data in it, so it was not linked: accounts with data ` +
        `are never merged. To link it, sign out, sign in with ${name || 'it'}, download that account's data if ` +
        'you want it (Account → Download my data), delete it (Account → Delete my account), then sign in here ' +
        `again and link ${name || 'it'}.`;
    } else if (error) {
      tone = 'danger';
      text = `Linking failed (${error}). Please try again.`;
    } else if (params.get('empty_account') === 'removed') {
      text = `${who} is now linked to this vault (its empty test account was removed).`;
    } else if (params.get('empty_account') === 'kept') {
      text = `${who} is now linked to this vault. It came from an empty account that keeps its other sign-in ` +
        'methods; that account was signed out.';
    } else {
      text = `${who} is now linked to this vault.`;
    }
    KEYS.forEach((k) => params.delete(k));
    return { text, tone, params };
  }

  window.VaultLinkNotice = { noticeFromSearch };
})();
