# lucentAPI

An HTTPS API for lucentBASE that reads and writes. Clients (the SwiftUI app
[lucentLIMS](../lucentLIMS), R scripts, instrument imports) get JSON over HTTPS and never touch
the database. Users log in at an OpenID Connect provider and send the access token they get.

```
lucentLIMS, R, scripts ──HTTPS + Bearer token──▶ lucentAPI ──one service account──▶ MariaDB (lucentBASE)
        └── login ──▶ OpenID Connect provider (Keycloak, Authentik, Entra ID, …) ──▶ signing keys ──┘
```

The parts of the lucent family can be used separately:

| | |
|---|---|
| lucentBASE | the data model (MariaDB) |
| lucentAndShiny | the R/Shiny front end, which works on the database directly |
| **lucentAPI** | this service: FastAPI, SQLAlchemy Core, PyJWT |
| lucentLIMS | the SwiftUI app for macOS, iPadOS and iOS |

> **Status.** The API runs and is tested on SQLite with demo data. It has not run against
> MariaDB or a real identity provider yet. The lucentBASE schema in
> [schema.py](src/lucentapi/schema.py) is reconstructed from lucentAndShiny's demo data:
> column names are exact, types are guesses. Compare it with your lucentBASE before you
> connect.

## Quick start (SQLite, no identity provider)

```bash
uv sync
```

```bash
cp .env.example .env
```

```bash
uv run python dev/seed.py
```

```bash
uv run uvicorn --factory lucentapi.main:create_app --reload
```

In a second terminal, get a development token (the first run creates a signing key in
`dev/.keys/` and `dev/jwks.json`) and call the API:

```bash
TOKEN=$(uv run python dev/mint_token.py grindel admin)
```

```bash
curl --noproxy '*' -H "Authorization: Bearer $TOKEN" 'localhost:8000/v1/collections?status=in_review'
```

The interactive documentation is at <http://localhost:8000/docs>. The demo users are
`grindel`, `jd`, `worf`, `s7e23`, `p47at` and `xx121`, the people of lucentAndShiny's demo data.

## Configuration

Environment variables, or a `.env` file in the working directory:

| Variable | |
|---|---|
| `LUCENT_DATABASE_URL` | SQLAlchemy URL, e.g. `mysql+pymysql://lucentapi:…@db.example.org/lucent?charset=utf8mb4` |
| `LUCENT_OIDC_ISSUER` | The provider's issuer, e.g. `https://id.example.org/realms/lucent`. Tokens must name it in `iss`. |
| `LUCENT_OIDC_AUDIENCE` | What tokens must name in `aud`, e.g. `lucentapi`. |
| `LUCENT_OIDC_JWKS_URL` | Optional. Without it the keys are found through `<issuer>/.well-known/openid-configuration`. |
| `LUCENT_OIDC_JWKS_FILE` | Keys from a file instead. Only for development (`dev/jwks.json`). |
| `LUCENT_USERNAME_CLAIM` | Default `preferred_username`. Matched against `person.unix_account`. |
| `LUCENT_ROLES_CLAIM` | Default `roles`. Dots walk into nested claims: Keycloak needs `realm_access.roles`. |
| `LUCENT_ROLE_MAP` | JSON, e.g. `{"lims-reviewers": "reviewer"}`, if your role or group names differ. |
| `LUCENT_NUMBERING_OFFSET` | Collection identifiers continue at `id_collection + offset`. Default 0. |
| `LUCENT_ROOT_PATH` | Path prefix behind a reverse proxy, e.g. `/lucent`. |

Unknown `LUCENT_` variables stop the start, so a typo doesn't go unnoticed.

## Authentication and roles

The API handles no passwords. It accepts a token when its signature matches one of the
provider's keys and its issuer, audience and expiry are right. The token's username must
belong to a visible person in lucentBASE (`person.unix_account`). Otherwise the answer is 403
`unknown_person`. Every change is recorded under that person, so machine clients need a
person row as well.

| Role | May |
|---|---|
| (any person) | read everything; create tasks; send and read their own messages |
| `sampler` | register collections |
| `analyst` | record values |
| `reviewer` | accept and withdraw values, never ones they measured (four-eyes principle) |
| `admin` | everything, including switching devices on and off |

Tasks can be changed by their assigner, their assignee and admins.

### Keycloak

1. Create a realm, e.g. `lucent`, and connect your user directory (LDAP) or add users. The
   username has to equal `person.unix_account`.
2. Create the realm roles `sampler`, `analyst`, `reviewer` and `admin` and assign them to
   users or groups.
3. Create a client `lucentapi` for the API. It needs no login flows; it only names the
   audience.
4. Create a client `lucentlims` for the app:
   - client authentication off (a public client);
   - standard flow on;
   - PKCE method `S256`;
   - redirect URI `lucentlims://oauth/callback`.

   Give it a client scope with an *Audience* mapper that adds `lucentapi` to access tokens.
5. Configure the API with `LUCENT_OIDC_ISSUER=https://<host>/realms/lucent`,
   `LUCENT_OIDC_AUDIENCE=lucentapi` and `LUCENT_ROLES_CLAIM=realm_access.roles`.

For an instrument or import script, create a confidential client with service accounts
enabled. It gets tokens through the client-credentials flow, and its username is
`service-account-<client>`, which needs a person row.

Other providers work the same way: an issuer, an audience, a username claim and a roles
claim. With Entra ID the app roles arrive in `roles`.

## Endpoints

All under `/v1`, all need a token. The full description is in [openapi.json](openapi.json)
and at `/docs`.

| | |
|---|---|
| `GET /me` | the caller and their roles |
| `GET /people`, `/organizations` | |
| `GET /sites`, `/spots`, `/crs` | sites with address and border |
| `GET /matrices`, `/collection-classes`, `/dimensions`, `/units`, `/variables` | |
| `GET /actions` | methods with their variables, limits, devices, people and dependencies |
| `GET /devices`, `PATCH /devices/{id}` | switching a device on or off (admin, `If-Match`) |
| `GET /sops` | with documents and people |
| `GET /collections` | newest first; filter by status, matrix, class, spot, site (with its spots) and sampling time; paged |
| `POST /collections` | registers a collection; the identifier comes from the numbering scheme |
| `GET /collections/{id}` | with all values |
| `POST /collections/{id}/values` | records a reading; a further one becomes the next repetition |
| `POST /values/{id}/accept`, `/withdraw` | review a value |
| `POST /collections/{id}/accept` | accepts all open values, or none |
| `GET /values` | values of many collections at once (by collection or sampling time), for clients that keep a local copy |
| `GET /series` | one variable over time at a spot or site |
| `GET /tasks`, `POST /tasks`, `PATCH /tasks/{id}` | `PATCH` needs `If-Match` |
| `GET /messages`, `POST /messages`, `POST /messages/{id}/read` | |

`GET /health` needs no token.

A collection's status comes from its values:

| Status | Values |
|---|---|
| `registered` | none |
| `in_analysis` | some of the planned variables |
| `in_review` | all planned variables, not all accepted |
| `approved` | all accepted |

A reading is exactly one of `{"decimal": …}`, `{"text": …}`, `{"boolean": …}` or
`{"datetime": …}`, and has to match the variable's value class.

Errors look like `{"error": {"code", "message", "request_id", "details"?}}`. Request bodies
with unknown fields are refused. Times are UTC: the database stores naive UTC, and the API
answers with `Z`.

## Audit trail

lucentBASE has a log table `l_<table>` for every table. Every write of the API adds a row to it:

- `change`: JSON of the inserted row or the changed fields, old and new;
- `t`: the time;
- `id_editor`: the person behind the token;
- `note`: an optional reason (most write requests take a `note`);
- `checksum`.

The `checksum` is the SHA-256 of the previous checksum for the same record plus the row's
content. Changing or deleting a log row breaks the chain from that row on. To check every
record of some tables:

```bash
uv run lucentapi-verify-log collection value raw_value
```

This is how lucentAPI fills the `checksum` column. If lucentBASE meant something else by
it, [audit.py](src/lucentapi/audit.py) is the place to change.

The API never deletes. lucentBASE hides rows with `vis = 0`, and the API only shows visible
rows.

## Concurrent changes

Records that can be changed (devices, tasks, values) carry an `etag`, a hash of the record's
columns. Send it back as `If-Match`. If the record changed in the meantime, through the API
or not, the answer is 412 with the current ETag. `PATCH` requires `If-Match` (428 without);
accepting and withdrawing values check it when it is sent.

## Collection identifiers

Identifiers use the mixed-radix scheme of lucentAndShiny's `las.yaml` (first four segments),
e.g. `0·Cyan·1·Rho`. The API derives them from the auto-increment id plus
`LUCENT_NUMBERING_OFFSET` inside the insert's transaction. Two clients registering at the
same moment can't get the same identifier, which taking the largest existing name plus one
can't guarantee.

## The database

- **[sql/lucentbase-additions.sql](sql/lucentbase-additions.sql)** adds the few columns the
  API needs that lucentBASE doesn't have:
  - when and by whom a collection was sampled;
  - a method's standard;
  - the value unit, display precision and limits of a measured variable;
  - `task.done`;
  - `message.sent_at`.

  It is safe to run more than once. Test it on a copy first.
- **[sql/lucentapi-account.sql](sql/lucentapi-account.sql)** creates the API's database
  account. It gets `SELECT, INSERT, UPDATE`: no `DELETE`, no schema changes.
- **[sql/lucentbase-schema.sql](sql/lucentbase-schema.sql)** is the reconstructed schema as
  MariaDB DDL, generated from `schema.py`. Use it for a fresh test database, not on top of an
  existing lucentBASE.

Two things differ from what you might expect:

- **Coordinates:** `spot.x` is the latitude and `spot.y` the longitude, as in
  lucentAndShiny. Site borders are WKT polygons in the usual (longitude latitude) order, as
  lucentAndShiny writes them. The API answers with explicit `latitude` and `longitude`.
- **SOP roles:** `j_sop_person` has no log table in the demo data, so the API doesn't write
  to it.

## Clients

[examples/responses/](examples/responses) holds real answers of the API on the demo data,
one file per endpoint, generated by `scripts/export_examples.py`. They're meant for client
tests.

- **Swift:** lucentLIMS's `Packages/LucentAPIClient` is a client without dependencies, tested
  against this repository's `openapi.json` and example responses. You can also generate one
  with [swift-openapi-generator](https://github.com/apple/swift-openapi-generator). Log in with
  `ASWebAuthenticationSession` and keep the tokens in the Keychain.
- **R:** use httr2 with `req_auth_bearer_token()`. For tokens, `oauth_flow_auth_code()`
  covers the login.
- **Python:** use `httpx` or `requests` with an `Authorization: Bearer` header.

## Running it in production

The API runs as `uvicorn --factory lucentapi.main:create_app` behind a reverse proxy that
terminates TLS, e.g. Caddy with `reverse_proxy 127.0.0.1:8000`. Start uvicorn with
`--proxy-headers`, and set `LUCENT_ROOT_PATH` if the API lives under a path. Keep the
database password in the service's environment, not in git. None of this has been tried in
production yet.

## Development

```bash
uv run pytest
```

The tests run on SQLite with the demo data, with tokens signed by a throwaway key, and cover:

- authentication;
- the reference endpoints;
- the collection workflow, including the four-eyes principle and repetitions;
- ETags;
- the audit chain and tamper detection;
- that the generated files are current.

After changing `schema.py` or an endpoint, regenerate the files. The tests fail until you do:

```bash
uv run python scripts/dump_schema.py
```

```bash
uv run python scripts/export_openapi.py
```

```bash
uv run python scripts/export_examples.py
```

Clients that copy `openapi.json` and the examples (like lucentLIMS's client package) need the
new copies too.

### Layout

```
src/lucentapi/
  main.py        create_app(), /health
  config.py      settings (LUCENT_…)
  auth.py        token check, person lookup, roles
  schema.py      the lucentBASE tables
  lims.py        planned variables, status, limits, four-eyes
  audit.py       log rows and their checksum chain
  etag.py        If-Match
  numbering.py   collection identifiers
  routes/        reference, collections, management
  demo.py        demo data (development and tests)
  devtokens.py   local signing key for development tokens
dev/             seed.py, mint_token.py
scripts/         dump_schema.py, export_openapi.py, export_examples.py
examples/        responses/: real answers on the demo data
sql/             additions, API account, reconstructed schema
tests/
```
