// Thin client for the Vault server (same origin, session cookie).
window.VaultApi = (() => {
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function call(path, opts = {}) {
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
    deleteAccount: () => call('/api/me', { method: 'DELETE' }),
  };
})();
