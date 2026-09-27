// Thin client for the Vault server (same origin, session cookie).
window.VaultApi = (() => {
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function call(path, opts = {}) {
    if (opts.json !== undefined) {
      opts = { ...opts, body: JSON.stringify(opts.json), headers: { 'Content-Type': 'application/json' } };
      delete opts.json;
    }
    const resp = await fetch(path, { credentials: 'same-origin', ...opts });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try { msg = (await resp.json()).detail || msg; } catch {}
      throw new ApiError(resp.status, msg);
    }
    const type = resp.headers.get('content-type') || '';
    return type.includes('application/json') ? resp.json() : resp.text();
  }

  return {
    ApiError,
    providers: () => call('/api/auth/providers'),
    me: () => call('/api/me'),
    collection: () => call('/api/collection'),
    imports: () => call('/api/imports'),
    importCsv: (file) => {
      const body = new FormData();
      body.append('file', file);
      return call('/api/imports', { method: 'POST', body });
    },
    archidektDeck: (id) => call('/api/archidekt/decks/' + encodeURIComponent(id)),
    logout: () => call('/api/auth/logout', { method: 'POST' }),
    devLogin: () => call('/api/auth/dev-login', { method: 'POST' }),

    // account & GDPR
    updateName: (name) => call('/api/me', { method: 'PATCH', json: { name } }),
    exportUrl: '/api/me/export',
    deleteAccount: () => call('/api/me', { method: 'DELETE', json: { confirm: 'DELETE' } }),

    // saved decks
    decks: () => call('/api/decks'),
    deck: (id) => call('/api/decks/' + id),
    saveDeck: (name, text, source_url) => call('/api/decks', { method: 'POST', json: { name, text, source_url } }),
    deleteDeck: (id) => call('/api/decks/' + id, { method: 'DELETE' }),

    // sharing
    shares: () => call('/api/shares'),
    createShare: (kind, deck_id, show_costs) => call('/api/shares', { method: 'POST', json: { kind, deck_id, show_costs } }),
    removeShare: (id) => call('/api/shares/' + id, { method: 'DELETE' }),
    acceptInvite: (token) => call('/api/shares/accept', { method: 'POST', json: { token } }),
    sharedWithMe: () => call('/api/shared'),
    sharedCollection: (id) => call('/api/shared/' + id + '/collection'),
    sharedDeck: (id) => call('/api/shared/' + id + '/deck'),
  };
})();
