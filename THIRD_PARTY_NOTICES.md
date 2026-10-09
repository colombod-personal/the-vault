# Third-party notices

The Vault's own code (this repository) is licensed under the [MIT License](LICENSE). The
software it uses, and the Magic: The Gathering content it shows, keep their own licences and
terms. Nothing here is relicensed.

## Software the server uses

These are installed as dependencies; none of their code is copied into this repository.

| Package | Licence | Used for |
|---|---|---|
| mtg-toolkits | MIT | collection formats, Scryfall matching, deltas, decklists (our library) |
| FastAPI, Starlette | MIT, BSD-3-Clause | the web server |
| Pydantic (+ pydantic-core, annotated-types, typing-inspection) | MIT | request and response models |
| SQLAlchemy | MIT | database layer |
| Alembic (+ Mako, MarkupSafe) | MIT, MIT, BSD-3-Clause | database schema migrations |
| **Psycopg 3** (+ psycopg-binary) | **LGPL-3.0-only** | Postgres driver (see below) |
| Authlib, joserfc | BSD-3-Clause | OAuth / OpenID Connect sign-in, JWTs |
| py_webauthn | BSD-3-Clause | passkeys (WebAuthn) |
| cbor2 | MIT | CBOR decoding for WebAuthn |
| cryptography | Apache-2.0 OR BSD-3-Clause | signatures, keys |
| pyOpenSSL | Apache-2.0 | used by py_webauthn |
| pyasn1, pyasn1-modules | BSD-2-Clause | used by py_webauthn |
| cffi, pycparser | MIT-0, BSD-3-Clause | used by cryptography |
| HTTPX, httpcore, h11, anyio, idna | BSD-3-Clause, BSD-3-Clause, MIT, MIT, BSD-3-Clause | HTTP client |
| certifi | MPL-2.0 | CA certificates for HTTPS (unmodified) |
| ItsDangerous | BSD-3-Clause | signed session cookies |
| python-multipart | Apache-2.0 | file uploads |
| typing-extensions | PSF-2.0 | Python typing backports |

Development and test only (not deployed): pytest (MIT), uvicorn (BSD-3-Clause), Playwright
(Apache-2.0, not a project dependency).

### Psycopg and the LGPL

Psycopg is LGPL-3.0. The Vault uses it unmodified, as a separately installed library, imported
at run time. That keeps our code MIT. Things to know:
- **Running the site** (Vercel) doesn't "convey" Psycopg to anyone, so there is nothing extra to do.
- **Handing someone a bundle that includes Psycopg** (a Docker image, a packaged desktop app, a
  zip of the Vercel output) conveys it. Include the LGPL-3.0 and GPL-3.0 texts and a pointer to
  Psycopg's source (https://github.com/psycopg/psycopg). Make sure the recipient can replace
  Psycopg with their own build; a normal pip-installed Python library already allows that.
- **Modifying Psycopg itself** would mean publishing those modifications under the LGPL. We don't.
- `psycopg-binary` wheels also bundle libpq (PostgreSQL Licence) and OpenSSL (Apache-2.0).

The **iOS app** won't include Psycopg or any server code; it talks to the API.

### certifi (MPL-2.0)

MPL-2.0 is file-level copyleft: only changed certifi files would have to be shared. We don't
change it.

## Software the web page loads

Loaded by the browser from the unpkg CDN, not stored in this repository: React and ReactDOM
(MIT). The views are compiled ahead of time by esbuild (MIT, a build tool in
`web/`; none of its code ships). Fonts come from Google Fonts (Cormorant
Garamond, Manrope and JetBrains Mono, all SIL Open Font License 1.1).

## Content that isn't ours

The MIT licence covers our code only, not these:
- **Magic: The Gathering.** Card names, rules text, mana symbols, set symbols and card images
  are the property of Wizards of the Coast LLC. The Vault is unofficial Fan Content permitted
  under the [Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy); it is
  not approved or endorsed by Wizards. The test seed data (`twins/data/scryfall_cards.json`) and
  fixtures contain a few card names and short rules texts for testing, under the same policy.
- **Scryfall** card data, images and prices are used under
  [Scryfall's terms](https://scryfall.com/docs/api). Images are shown with artist credit and
  never cropped, and the Vault stays free. Card art belongs to the artists and Wizards of the
  Coast.
- **17Lands** per-card Limited statistics are worked out from 17Lands' public data sets, which are licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) ("Unless otherwise noted", read on 2026-10-09). The data is not
  stored: the server streams the game and draft files, keeps per-card counts and throws the files away. The credit, the
  statement that the Vault changed the data (counts reduced; percentages, ranges and warnings recomputed) and the "not produced or
  endorsed by 17Lands" line are in `public/credits.html` and in every answer that carries these figures. The licence provides the
  data without warranty (its section 5).
- **Archidekt, Dragon Shield and Moxfield** are named for interoperability (reading and writing
  file formats and public decks). They are trademarks of their owners, and the Vault is not
  affiliated with any of them.

`tests/test_licenses.py` fails if a GPL or AGPL package enters the server's dependencies,
or an LGPL one other than Psycopg.
