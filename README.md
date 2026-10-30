<p align="center"><img src="src/lucentapi/static/lucentLIMS-logo.svg" alt="lucentLIMS" width="420"></p>

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

> **Status.** The API follows lucentBASE's DDL: it checks the tables, columns and log
> triggers at startup, and its tests run against a database generated from lucentBASE's
> `DATABASE_GENERATION.SQL` on MariaDB. It needs lucentBASE's branch
> `harmonize-with-lucentapi` (see [The database](#the-database)). It has not run against a
> real identity provider or in production yet.

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

The interactive documentation is at <http://localhost:8000/docs> (and
<http://localhost:8000/redoc>). Both show the lucentLIMS logo, which the API serves from
`src/lucentapi/static/` under `/static/`. The demo users are
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
| `LUCENT_USERNAME_CLAIM` | Default `preferred_username`. Matched against `person.linux_user`. |
| `LUCENT_ROLES_CLAIM` | Default `roles`. Dots walk into nested claims: Keycloak needs `realm_access.roles`. |
| `LUCENT_ROLE_MAP` | JSON, e.g. `{"lims-reviewers": "reviewer"}`, if your role or group names differ. |
| `LUCENT_NUMBERING_OFFSET` | Collection identifiers continue at `id_collection + offset`. Default 0. |
| `LUCENT_SAMPLING_TIME_PARAMETER` | The parameter (by name) whose datetime raw value is a collection's sampling time, e.g. `Probenahmezeitpunkt`. Without it, a collection's time is when it was registered. |

Unknown `LUCENT_` variables stop the start, so a typo doesn't go unnoticed.

## Authentication and roles

The API handles no passwords. It accepts a token when its signature matches one of the
provider's keys and its issuer, audience and expiry are right. The token's username must
belong to a visible person in lucentBASE (`person.linux_user`). Otherwise the answer is 403
`unknown_person`. Every change is recorded under that person, so machine clients need a
person row as well.

| Role | May |
|---|---|
| (any person) | read everything; create tasks; send and read their own messages |
| `sampler` | register collections |
| `analyst` | record raw values |
| `reviewer` | accept and withdraw raw values, never ones they measured (four-eyes principle) |
| `admin` | everything, including switching devices on and off |

Tasks can be changed by their assigner, their assignee and admins.

### Keycloak

1. Create a realm, e.g. `lucent`, and connect your user directory (LDAP) or add users. The
   username has to equal `person.linux_user`.
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
| `GET /configuration` | the sampling-time parameter and lucentBASE's collection classes |
| `GET /people`, `/organizations` | |
| `GET /sites`, `/spots`, `/crs` | sites with address and border |
| `GET /matrices`, `/collection-classes`, `/dimensions`, `/units`, `/parameters` | |
| `GET /actions` | methods with their effects, documents, devices, people and dependencies |
| `GET /documents`, `/thresholds`, `/coefficients` | documents (`link`) and the limits they set |
| `GET /devices`, `PATCH /devices/{id}` | switching a device on or off (admin, `If-Match`) |
| `GET /collections` | newest first; filter by status, matrix, class, spot, site (with its spots) and sampling time; paged |
| `POST /collections` | registers a collection and plans the effects of the given actions as its values |
| `GET /collections/{id}` | with its values and their raw values |
| `POST /collections/{id}/plan` | plans further actions |
| `POST /values/{id}/raw-values` | records a reading; a further one is a repetition |
| `POST /raw-values/{id}/accept`, `/withdraw` | review a raw value |
| `POST /collections/{id}/accept` | accepts all open raw values, or none |
| `GET /values` | values of many collections at once (by collection or sampling time), for clients that keep a local copy |
| `GET /series` | one parameter over time at a spot or site |
| `GET /tasks`, `POST /tasks`, `PATCH /tasks/{id}` | `PATCH` needs `If-Match` |
| `GET /messages`, `POST /messages`, `POST /messages/{id}/read` | |

`GET /health` needs no token.

### Collections, values and raw values

The API uses lucentBASE's model as it is:

- An **action** is a method. Its **effects** are what it yields: one parameter each, with the
  method's uncertainty.
- Registering a collection plans actions: every effect of them becomes one of the
  collection's **values**, a slot.
- A reading is a **raw value** of a value. A second reading of the same value is a
  repetition. Raw values carry who measured them, when, with which device and in which unit.
  The unit defaults to the parameter's, the device to the action's first active one.
- The **sampling time** is the datetime raw value of the parameter named by
  `LUCENT_SAMPLING_TIME_PARAMETER`. `POST /collections` records it when it gets `sampled_at`;
  if none of the planned actions yields that parameter, the answer is 422
  `sampling_time_not_planned`. Without one, the registration time from `l_collection` stands
  in.
- **Limits** are `threshold` rows: a lower and/or upper limit of a parameter in the context of
  a document (`link`), e.g. the drinking water ordinance. The limits of a raw value are the
  thresholds of its parameter in the documents of its action (`j_action_link`). Each value
  lists them, and each raw value says whether it `exceeds` one.

A collection's status comes from its values:

| Status | Values |
|---|---|
| `registered` | none has a raw value |
| `in_analysis` | some have |
| `in_review` | all have, not all raw values accepted |
| `approved` | all raw values accepted |

A reading is exactly one of `{"decimal": …}`, `{"text": …}`, `{"boolean": …}` or
`{"datetime": …}`, and has to match the parameter's `value_class`.

Errors look like `{"error": {"code", "message", "request_id", "details"?}}`. Request bodies
with unknown fields are refused. Times are UTC: the database stores naive UTC, and the API
answers with `Z`.

## The log

lucentBASE keeps a log table `l_<table>` for every table, written by its triggers, not by the
API. Before each write the API sets two session variables, which the triggers read:

- `@current_user_id`: the person behind the token, as `id_editor`;
- `@change_note`: the request's `note`, if it has one (most write requests take one).
  Otherwise the triggers write `Inserted` or `Updated`.

Each log row has the time `t` and a `checksum`, the SHA-256 of the row's columns. The API
reads the log for a collection's registration time and a message's sending time.

The API never deletes. lucentBASE hides rows with `vis = 0`, and the API only shows visible
rows.

## Concurrent changes

Records that can be changed (devices, tasks, raw values) carry an `etag`, a hash of the record's
columns. Send it back as `If-Match`. If the record changed in the meantime, through the API
or not, the answer is 412 with the current ETag. `PATCH` requires `If-Match` (428 without);
accepting and withdrawing raw values check it when it is sent.

## Collection identifiers

Identifiers use the mixed-radix scheme of lucentAndShiny's `las.yaml` (first four segments),
e.g. `0·Cyan·1·Rho`. The API inserts the collection without a name and sets it to the
identifier of the auto-increment id plus `LUCENT_NUMBERING_OFFSET` in the same transaction. Two clients registering at the
same moment can't get the same identifier, which taking the largest existing name plus one
can't guarantee.

## The database

lucentBASE has the final say: [schema.py](src/lucentapi/schema.py) mirrors its DDL, and the
API refuses to start when a table, column or log trigger it needs is missing. A few things
the API needs weren't in lucentBASE yet; they are on lucentBASE's branch
`harmonize-with-lucentapi`:

- `threshold`: limits of a parameter in the context of a document;
- `parameter.value_class`: which kind of raw value a parameter takes;
- `task.done`;
- `@change_note` in the log triggers;
- fixes to `v_raw_value`, a district's uniqueness and the checksum's separator.

**[sql/lucentapi-account.sql](sql/lucentapi-account.sql)** creates the API's database
account. It gets `SELECT, INSERT, UPDATE`: no `DELETE`, no schema changes.

Things to know:

- **Coordinates:** `spot.x` is the latitude and `spot.y` the longitude, as in
  lucentAndShiny. Site borders are WKT polygons in the usual (longitude latitude) order, as
  lucentAndShiny writes them. The API answers with explicit `latitude` and `longitude`.
- **`value`** is a reserved word in MariaDB, so SQL that writes to it has to quote it.
- **Dependencies:** a row of `j_action_action` is read as "`id_action1` depends on
  `id_action2`".
- **SQLite:** for development and most tests, [devdb.py](src/lucentapi/devdb.py) creates the
  tables on SQLite and imitates the log triggers.

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
`--proxy-headers`, and with `--root-path /lucent` if the API lives under a path. Keep the
database password in the service's environment, not in git. None of this has been tried in
production yet.

## Development

```bash
uv run pytest
```

The tests run on SQLite with the demo data, with tokens signed by a throwaway key, and cover:

- authentication;
- the reference endpoints;
- the collection workflow: planning, raw values, repetitions, sampling time, limits and the
  four-eyes principle;
- ETags;
- that the schema check notices what's missing;
- that the generated files are current.

With a local MariaDB they also run against the real lucentBASE: a database generated from
`DATABASE_GENERATION.SQL`, its CHECK constraints and its log triggers, through an account
with the API's grants. The test database's name must end in `_test`; it is dropped and
recreated.

```bash
LUCENTBASE_DIR=../lucentBase MARIADB_TEST_URL='mysql+pymysql://lucentapi_test:<password>@127.0.0.1/lucent_test' uv run pytest
```

After changing an endpoint, regenerate the files. The tests fail until you do:

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
  main.py        create_app(), /health, /docs and /redoc
  static/        the lucentLIMS logo (SVG), as in lucentLIMS's Branding/
  config.py      settings (LUCENT_…)
  auth.py        token check, person lookup, roles
  schema.py      lucentBASE's tables, as its DDL defines them
  checks.py      the startup check against the database
  db.py          engine, session variables for the log triggers, UTC
  devdb.py       lucentBASE on SQLite, with imitated log triggers
  lims.py        status, sampling time, limits, four-eyes
  etag.py        If-Match
  numbering.py   collection identifiers
  routes/        reference, collections, management
  demo.py        demo data (development and tests)
  devtokens.py   local signing key for development tokens
dev/             seed.py, mint_token.py
scripts/         export_openapi.py, export_examples.py
examples/        responses/: real answers on the demo data
sql/             the API's database account
tests/
```
