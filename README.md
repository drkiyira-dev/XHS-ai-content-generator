# XHS AI Content Generator

**English** | [简体中文](README.zh.md)

A full-stack, single-image Xiaohongshu (XHS) content generator built with Vue 3 and
FastAPI. It accesses PaddleOCR-VL and Qwen3-VL through the SiliconFlow API. The
application accepts one image plus optional topic/name, target-reader, and tone hints,
then returns an image-understanding summary, title, body copy, topic tags, and
pre-publication risk hints.

## Features

- `POST /api/v1/generations` accepts one image through `multipart/form-data`.
- `GET /api/v1/generations` returns recent successful local history and restricted
  image-preview state.
- `GET /api/v1/generations/{generation_id}/image-preview` returns a metadata-free
  thumbnail scoped to the current account.
- `DELETE /api/v1/generations/{generation_id}` soft-deletes one successful record
  within the current account scope.
- A product landing page provides an overview, workflow, FAQ, and creation entry point.
- The landing page, generation workspace, and history are separate routes with direct
  navigation, reload, browser back/forward support, history thumbnails, workspace
  restore, copy, and confirmed deletion. Regeneration keeps the current draft visible,
  supports session-local edits, and offers previous-version comparison. Local edits are
  never written back to history automatically.
- Optional local-account mode supports email registration, login, session restoration,
  and logout. Generation and history are isolated by the current account.
- Image-understanding summaries are collapsed by default in both current results and
  history cards and can be expanded with native disclosure controls.
- Supports JPG, JPEG, PNG, WebP, and single-frame HEIC/HEIF. HEIC/HEIF is converted to
  JPEG on the server. Empty files, disguised formats, corrupted images, oversized files,
  and invalid dimensions are rejected.
- Handles EXIF orientation, alpha channels, color modes, and proportional downscaling.
- PaddleOCR-VL attempts to read packaging text. OCR failure or timeout safely falls back
  to direct Qwen3-VL image understanding.
- Qwen3-VL returns a fixed `image_summary`, `title`, `body`, and `tags` structure.
- Includes JSON parsing, bounded repair, title normalization to at most 20 characters,
  and validation of 3–5 tags.
- Conservatively rejects skincare efficacy, audience suitability, sensory claims, and
  obvious category conflicts that are not supported by the image.
- Provides deterministic Emoji styling at `off`, `light`, or `expressive` levels and can
  append related tags from a versioned local catalog. It does not claim to use a live
  popularity chart and does not make an additional model call.
- Uses versioned local rules to flag absolute claims, medical/health language, off-site
  redirection, engagement bait, false endorsement, competitor disparagement, and broad
  traffic-seeking tags. These hints are neither Xiaohongshu platform review nor a
  prediction of throttling or enforcement.
- Shows a result-shaped skeleton while generation is in progress. Unvalidated model
  fragments are never displayed early. The endpoint still returns one complete JSON
  response; the skeleton is not an SSE token stream.
- Maps model failures, timeouts, invalid output, and internal failures to stable errors
  without exposing API keys or stack traces.
- Removes uploaded and preprocessed temporary files after both success and failure.

Member C's database core is integrated into the application lifecycle through Member B's
asynchronous adapter. MySQL persistence is disabled by default and uses a No-op adapter
until explicitly enabled. If startup validation fails after enabling it, the application
fails closed instead of pretending that writes succeeded. The B+C persistence path has
also been exercised against a dedicated local MySQL 9.6 test database.

## Requirements

- Python 3.11 or newer
- Node.js 24 and npm (the frontend development and CI validation baseline)
- Network access to the SiliconFlow API
- Optional: a current Docker Desktop or Docker Engine with `docker compose`
- A SiliconFlow API key authorized for:
  - `Qwen/Qwen3-VL-8B-Instruct`
  - `PaddlePaddle/PaddleOCR-VL-1.5`

## Local Installation

### Backend

```bash
git clone https://github.com/drkiyira-dev/XHS-ai-content-generator.git
cd XHS-ai-content-generator
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Frontend

```bash
npm --prefix frontend ci
cp frontend/.env.example frontend/.env
```

The frontend template connects to the real FastAPI service at
`http://127.0.0.1:8000` by default. Browser-only Mock data is used only when
`VITE_USE_MOCK=true` is explicitly set. Never place a SiliconFlow API key or database
password in frontend configuration. Mock mode does not analyze images; it cycles through
three clearly labeled demo drafts per account to exercise regeneration and comparison.

## Environment Configuration

Copy the template and provide a real key:

```bash
cp .env.example .env
```

At minimum, set:

```dotenv
SILICONFLOW_API_KEY=your-real-key
```

The current `.env.example` provides:

| Variable | Purpose | Template value |
| --- | --- | --- |
| `SILICONFLOW_API_KEY` | SiliconFlow API key, stored only in local `.env` | Empty; required |
| `SILICONFLOW_BASE_URL` | SiliconFlow OpenAI-compatible endpoint | `https://api.siliconflow.cn/v1` |
| `VISION_MODEL_NAME` | Image-understanding and copy-generation model | `Qwen/Qwen3-VL-8B-Instruct` |
| `OCR_MODEL_NAME` | Packaging-text OCR model | `PaddlePaddle/PaddleOCR-VL-1.5` |
| `MODEL_TIMEOUT_SECONDS` | Shared total deadline for OCR and Qwen | `60` |
| `OCR_TIMEOUT_SECONDS` | Maximum time allocated to OCR | `15` |
| `CORS_ALLOW_ORIGINS` | Comma-separated frontend origins allowed by the backend | Local Vite origins |
| `UPLOAD_DIR` | Temporary upload directory | `uploads` |
| `MAX_IMAGE_SIZE_MB` | Maximum size of one image | `10` |
| `MAX_IMAGE_PIXELS` | Maximum decoded pixel count | `50000000` |
| `MODEL_MAX_IMAGE_EDGE` | Maximum image edge before model submission | `3584` |
| `DATABASE_ENABLED` | Explicitly enable MySQL persistence | `false` |
| `DATABASE_URL` | Protected `mysql+pymysql` connection URL | Empty |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | MySQL connection timeout | `5` |
| `DATABASE_READ_TIMEOUT_SECONDS` | MySQL read timeout | `30` |
| `DATABASE_WRITE_TIMEOUT_SECONDS` | MySQL write timeout | `30` |
| `DATABASE_POOL_SIZE` | Persistent connection-pool limit | `5` |
| `DATABASE_MAX_OVERFLOW` | Temporary overflow connection limit | `5` |
| `DATABASE_POOL_TIMEOUT_SECONDS` | Maximum wait for a pooled connection | `5` |
| `DATABASE_POOL_RECYCLE_SECONDS` | Connection recycle interval | `1800` |
| `DATABASE_TLS_CA` | CA certificate path for remote MySQL | Empty |
| `AUTH_ENABLED` | Explicitly mount local-demo account endpoints | `false` |
| `AUTH_COOKIE_SECURE` | Use HTTPS-only Secure session cookies | `false` |

Never commit a real `.env`, API key, uploaded image, log, or raw model response. The
project `.gitignore` excludes these files.

## MySQL Persistence (Disabled by Default)

When database support is disabled, the application does not create an Engine, connect to
MySQL, or execute SQL. Swagger behavior remains unchanged. Before enabling persistence,
prepare all of the following:

- a dedicated existing database;
- a non-`root` user with limited privileges;
- a `generation_records` table created with
  [migrations/001_generation_records.sql](migrations/001_generation_records.sql);
- before running owner-aware persistence, back up existing history and explicitly run,
  in order:
  1. [migrations/003_auth_tables.sql](migrations/003_auth_tables.sql) to create `users`
     and `auth_sessions`;
  2. [migrations/004_generation_ownership.sql](migrations/004_generation_ownership.sql)
     to add nullable `generation_records.user_id`, its foreign key, and query index;
  3. [migrations/005_generation_previews_and_deletion.sql](migrations/005_generation_previews_and_deletion.sql)
     to add restricted previews and soft deletion;
  4. [migrations/006_generation_risk_snapshot.sql](migrations/006_generation_risk_snapshot.sql)
     to add a nullable generation-time risk snapshot;
- enable backend `AUTH_ENABLED` and frontend `VITE_AUTH_ENABLED` together for account
  mode;
- for a remote database, provide a CA file through `DATABASE_TLS_CA`.

Migration files never create, select, or delete a database. They only create or change
project tables inside the database explicitly selected by the operator. The runtime
`xhs_app` account has only `SELECT`, `INSERT`, and `UPDATE` and must not run `ALTER`.
Migrations 003–006 must be applied during a write-free maintenance window after a
verified backup, through a separate migration session with explicit DDL privileges and
an explicitly selected target database. Start the application with `xhs_app` only after
the resulting schema has been verified. Application startup never creates a database,
creates tables, or modifies the schema automatically.

After preparation, set the untracked local `.env` without sharing the password in chat
or Git:

```dotenv
DATABASE_ENABLED=true
DATABASE_URL=mysql+pymysql://xhs_app:your-password@127.0.0.1:3306/xhs_ai_test
```

Only `mysql+pymysql` is accepted. Empty passwords, the `root` user, URL query parameters,
and remote hosts without a CA are rejected. Startup performs read-only connectivity and
table-contract checks. Failure aborts startup instead of silently falling back to No-op.
The Engine hides SQL parameters and applies connection, read/write, and pool timeouts.

The dedicated local test database has verified successful API writes, failed-model state,
duplicate-ID rollback, concurrent final-state races on a pending record, reconnection and
reads after Engine disposal, and exact cleanup of test records. The application user has
only `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on that test database.

## Account API (Disabled by Default)

The account core exposes these endpoints for local demonstrations:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`

Registration uses email and password, but the demo does not send activation mail or
pretend that the address was verified. `email_verified` is always `false`. The raw session
token is written only to an `HttpOnly` cookie and never appears in JSON, the database, or
logs; the database stores only its SHA-256 digest. Registration, login, and logout also
require the fixed `X-XHS-CSRF: 1` header and use local single-process rate limits.

Ownership is explicit. With `AUTH_ENABLED=false`, generation and history access only the
`user_id IS NULL` compatibility partition. With `AUTH_ENABLED=true`, new records belong to
the account represented by the current cookie session, and history returns only that
account's records. Requests cannot choose `user_id` through form fields, query parameters,
or headers. Legacy NULL records are never assigned to the first registered user and are
not visible in any account history.

The frontend already provides registration, login, session restoration, and logout, but
both templates keep account mode disabled. Enable backend `AUTH_ENABLED=true` and frontend
`VITE_AUTH_ENABLED=true` together only after a controlled backup and migrations
003/004/005/006. An existing, unmigrated MySQL database must not run owner-aware code.
Startup fails with a fixed error when nullable ownership, preview, soft-delete, or native
JSON risk-snapshot columns are missing, so old-schema problems surface before the first
request. Startup never patches or migrates old data.

After migration, enable the two untracked configuration files together:

```dotenv
# Repository-root .env
AUTH_ENABLED=true
AUTH_COOKIE_SECURE=false

# frontend/.env
VITE_AUTH_ENABLED=true
VITE_API_BASE_URL=http://127.0.0.1:8000
```

The local HTTP demo must continue to listen only on `127.0.0.1`. Restart both the backend
and Vite after changing these flags. Do not use this insecure-cookie configuration on a
LAN or public network.

### Back Up Legacy History Before Migration

The offline `scripts/backup_generation_history.py` tool creates a read-only backup of the
13 original `generation_records` fields before ownership migration. It does not back up
account tables, database URLs, or image files, and it never performs DDL, assigns
`user_id`, or modifies records. Output is written under Git-ignored `.local-backups/`.
Each result is a `0700` directory whose data and manifest files are `0600`. The tool
reopens the output, verifies its row count and SHA-256, and atomically publishes the
directory only after validation succeeds.

Stop the backend and all database writes first. A consistent snapshot cannot include
records created after the snapshot begins. During the confirmed maintenance window, run:

```bash
XHS_HISTORY_BACKUP_CONFIRM=YES_BACKUP_XHS_AI_HISTORY \
  .venv/bin/python scripts/backup_generation_history.py
```

The CLI additionally requires the current database name, snapshot row count, and exact
`BACKEND_STOPPED` confirmation in an interactive terminal. It refuses non-interactive
input/output, custom destinations, or overwriting existing backups. Failure messages do
not print database URLs, credentials, SQL parameters, or history content.

A successful backup proves only that a verifiable copy exists; it does not assign
ownership. This project uses an archive policy: after explicitly running 003, 004, 005,
and 006, legacy records remain `user_id=NULL`; they are not guessed, updated, or deleted.
Preview and soft-delete fields also remain `NULL`. Legacy risk snapshots remain `NULL`,
and the history API reports that the original generation-time assessment cannot be
reconstructed instead of rescanning with newer rules. Legacy records remain invisible to
all users in account mode. Real backup and migration require a separately confirmed,
write-free maintenance window; this document does not connect to or modify the current
MySQL instance by itself.

## Run the Backend

Activate the virtual environment, then run:

```bash
python -m uvicorn backend.main:app --reload
```

Default endpoints:

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Health check: `GET http://127.0.0.1:8000/api/health`
- Generation: `POST http://127.0.0.1:8000/api/v1/generations`
- History: `GET http://127.0.0.1:8000/api/v1/generations?limit=20`

The health check only confirms that FastAPI can respond. It does not call OCR or Qwen,
query the database, or expose model, database, or environment details. When MySQL is
explicitly enabled, its independent startup checks still run before the application is
declared ready and abort startup on failure.

## Run the Frontend

Keep the backend running. In another terminal, from the repository root, run:

```bash
npm --prefix frontend run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5173>. The frontend calls the backend through
`VITE_API_BASE_URL`, whose default matches the FastAPI command above. Account mode requires
the page and API to use the same hostname family. Do not mix `localhost` and `127.0.0.1`,
or the SameSite cookie may not be sent.

- Landing page: <http://127.0.0.1:5173/>
- Generation workspace: <http://127.0.0.1:5173/app/generate>
- History: <http://127.0.0.1:5173/app/history>

Vite's development server and `vite preview` support direct reloads of these URLs. If a
different static server hosts `frontend/dist/`, configure SPA fallback to `index.html`
while keeping `/api/` routed to the backend.

Run the production build check before delivery:

```bash
npm --prefix frontend run build
```

## Backend Docker Image

The repository-root multi-stage `Dockerfile` builds only the backend. It uses Python 3.12
on Debian slim, runs as the fixed non-root UID/GID `10001:10001`, and copies only backend
code and runtime dependencies. The allowlisted build context excludes `.env`, Git history,
frontend dependencies, test data, uploaded images, and logs.

Build the image:

```bash
docker build --tag xhs-ai-backend:local .
```

This does not start or modify MySQL. Create an untracked container environment file,
provide at least a real `SILICONFLOW_API_KEY`, and keep `DATABASE_ENABLED=false`:

```bash
cp .env.example .env.docker
```

Run with a read-only root filesystem and dedicated tmpfs mounts. Original uploads,
preprocessed images, and Python temporary files remain in memory and disappear when the
container stops:

```bash
docker run --rm \
  --name xhs-ai-backend \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777 \
  --tmpfs /run/xhs/uploads:rw,noexec,nosuid,nodev,size=256m,uid=10001,gid=10001,mode=0700 \
  --cap-drop ALL \
  --security-opt no-new-privileges=true \
  --pids-limit 128 \
  --publish 127.0.0.1:8000:8000 \
  --env-file .env.docker \
  --env UPLOAD_DIR=/run/xhs/uploads \
  --env DATABASE_ENABLED=false \
  xhs-ai-backend:local
```

Open <http://127.0.0.1:8000/docs>. Check the container health status with:

```bash
docker inspect --format '{{.State.Health.Status}}' xhs-ai-backend
```

Never pass the real API key through Docker `ARG`, Dockerfile `ENV`, or image layers. The
example injects it at runtime from untracked `.env.docker`. The image declares no upload
`VOLUME`, avoiding anonymous volumes that retain images. The container listens on
`0.0.0.0` internally for port mapping, while the host publishes only to `127.0.0.1`.
Because `AUTH_ENABLED=false` is NULL-owner compatibility mode, account mode must be enabled
only after migration and synchronized frontend/backend configuration.

Inside a container, `127.0.0.1` refers to that container rather than host MySQL. This
standalone configuration therefore keeps the database disabled. Use the Compose setup in
the next section for backend plus database; do not bypass existing MySQL/TLS checks with
ad-hoc host mappings.

## Docker Compose: Backend and Database

The repository-root `compose.yaml` starts:

- the official `mysql:8.4.11` image with data in the named `mysql_data` volume;
- the repository's non-root FastAPI backend image.

It orchestrates **only the backend and MySQL; it does not contain a frontend container**.
After startup, run the Vue development server on the host as described above. This is
"one-command backend and database orchestration," not a one-command full-site deployment.

Initialize local secrets first. The script hides API-key input, generates random database
passwords, and creates four mutually consistent files without printing any secret:

```bash
python scripts/initialize_compose_secrets.py
```

Files live in Git-ignored `.compose-secrets/`. Do not copy, screenshot, commit, or share
their contents. The host directory is `0700`; files are `0444` read-only bind mounts for
non-root containers. The script refuses to overwrite a non-empty directory, preventing
accidental rotation of active database credentials.

Validate and start:

```bash
docker compose config --quiet
docker compose up --build --detach --wait
```

After startup, open <http://127.0.0.1:8000/docs>. The host-run frontend continues to use
`http://127.0.0.1:8000`.

Security boundaries:

- The API key, database URL, and both database passwords use read-only Compose file
  secrets. They are not stored in images, Compose environment variables, or Git. Each
  service receives only the secret it needs.
- The backend and MySQL use `network_mode: service:mysql` to share one network namespace
  and communicate over that namespace's real `127.0.0.1`. MySQL listens only there. Host
  and unrelated containers do not receive port `3306`, and MySQL X Protocol is disabled.
  This does not disguise a service name as loopback or weaken the rule requiring a CA for
  non-loopback MySQL.
- Host port `8000` is published only on `127.0.0.1`. A dedicated `runtime` bridge lets the
  backend reach SiliconFlow without publishing MySQL.
- Both services drop added Linux capabilities, enable `no-new-privileges`, and use
  read-only root filesystems. Temporary images are written only to backend tmpfs.

For a new, empty `mysql_data` volume, the official MySQL entrypoint creates only `xhs_ai`
and then runs, in order: `001_generation_records.sql`, `002_create_app_user.sh`,
`003_auth_tables.sql`, `004_generation_ownership.sql`,
`005_generation_previews_and_deletion.sql`, and `006_generation_risk_snapshot.sql`.
The second script creates `xhs_app` with only the runtime `SELECT`, `INSERT`, and `UPDATE`
privileges from its first grant; there is no intermediate `ALL`-privilege window. Migration
003 prepares `users` and `auth_sessions` without fabricating verification. Migration 004
adds nullable account ownership and the history index without backfilling, deleting, or
guessing ownership. Migration 005 stores metadata-free bounded previews in MySQL and adds
a nullable soft-delete timestamp; legacy rows remain `NULL`. Migration 006 adds a nullable
JSON risk snapshot; legacy rows are not backfilled. FastAPI still performs checks only and
never executes DDL.

Initialization scripts run **only once for an empty volume**. Existing volumes never
replay them automatically. Apply schema changes through a separate, explicitly approved
migration workflow. Existing volumes that predate migrations 005 or 006 must explicitly
apply both before restarting the newer ORM; Compose does not replay them. If first-time
initialization fails, inspect the fixed error logs and delete only that never-used failed
volume before retrying; do not keep a partially initialized volume.

Normal shutdown preserves history:

```bash
docker compose down
```

The following permanently deletes the Compose database and all generation history. Run
it only after explicitly deciding the data is no longer needed:

```bash
docker compose down --volumes
```

Do not regenerate `.compose-secrets/` while retaining `mysql_data`, or the new passwords
will no longer match accounts stored in the volume. Restore the original secret files if
they are lost. Only after intentionally deleting all Compose data should you run
`down --volumes` and initialize new secrets.

## API Request

Content type: `multipart/form-data`

| Field | Required | Description |
| --- | --- | --- |
| `image` | Yes | One JPG, JPEG, PNG, WebP, or single-frame HEIC/HEIF image. The template limit is 10 MB; HEIC/HEIF is converted to JPEG before model submission. |
| `product_name` | No | Topic or name hint. It adjusts direction only; the image wins on conflict. |
| `target_audience` | No | Target-reader hint. It is not evidence of audience suitability. |
| `tone` | No | Tone hint. It is not evidence of image facts or product attributes. |
| `emoji_level` | No | `off`, `light`, or `expressive`. The API default is `off`; it deterministically adjusts title/body without changing the image summary. |
| `related_tags` | No | `true` or `false`. The API default is `false`; tags come only from a versioned local catalog, not a live chart. |

Example:

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/generations' \
  -H 'accept: application/json' \
  -F 'image=@/absolute/path/example.jpg;type=image/jpeg' \
  -F 'product_name=Rosehip cleansing oil' \
  -F 'target_audience=University students' \
  -F 'tone=Relaxed and natural' \
  -F 'emoji_level=light' \
  -F 'related_tags=true'
```

All optional fields may be omitted. To preserve legacy-client behavior, the API adds no
Emoji or related tags unless those enhancement fields are submitted. The current frontend
workspace defaults to light Emoji plus related tags, and users may disable either.

## Successful Response

Status: `200 OK`

```json
{
  "generation_id": "550e8400-e29b-41d4-a716-446655440000",
  "image_summary": "A pale-pink bottle with a light label showing brand, category, and volume text.",
  "title": "Rosehip oil unboxed",
  "body": "The pale-pink bottle has a minimal label showing CLEANSING OIL, ROSE HIP, and 100 ML.",
  "tags": ["#cleansingoil", "#rosehip", "#pinkpackaging", "#skincare"],
  "created_at": "2026-08-07T07:39:32.921950Z",
  "risk_assessment": {
    "rule_version": "content-risk-hints-2026-08-14.2",
    "findings": []
  }
}
```

`generation_id` and `created_at` change for every request. `risk_assessment` contains
local, non-authoritative pre-publication hints. No findings does not mean platform
approval; a finding does not mean the content will necessarily be penalized or throttled.
The API returns once, after the complete model result passes structural and factual checks
and is saved. The page animation is not an unreviewed model-token stream.

Backend limits are consistent across parsing, persistence, and history reads:

- `image_summary`: at most 2,000 characters
- `title`: at most 20 characters
- `body`: at most 10,000 characters
- each normalized tag: at most 100 characters, including the leading `#`
- tags: 3–5 total

## History API: Compatibility and Account Isolation

The frontend loads recent successful results only when the user enters History. It never
uploads an image or calls OCR/Qwen during that read. The page displays restricted
thumbnails, image summaries, title, body, tags, and locally formatted time and allows
restore, copy, or deletion. It never displays local image paths, user input, failure
reasons, or database-internal fields.

```http
GET /api/v1/generations?limit=20
```

- Returns only `success` records, newest first.
- `limit` defaults to `20` and accepts `1` through `50`.
- Each item reuses `generation_id`, `image_summary`, `title`, `body`, `tags`,
  `created_at`, and `risk_assessment`, then adds `has_image_preview` and controlled
  `image_preview_url`. The top-level response also includes `count`.
- `risk_assessment` is the version and findings stored when generation succeeded. Rule
  upgrades never silently rewrite old records. Pre-006 NULL rows return an explicit
  "generation-time snapshot unavailable" warning instead of being rescanned under newer
  rules.
- Responses include `Cache-Control: no-store`.
- With `DATABASE_ENABLED=false`, No-op persistence stores nothing, so history is empty.
- With `AUTH_ENABLED=false`, only the `user_id IS NULL` compatibility partition is read.
- With `AUTH_ENABLED=true`, login is required and only the current account is returned;
  archived NULL-owner rows are invisible.

Example:

```json
{
  "items": [
    {
      "generation_id": "550e8400-e29b-41d4-a716-446655440000",
      "image_summary": "A pale-pink bottle with a light label.",
      "title": "Rosehip oil unboxed",
      "body": "The packaging shows CLEANSING OIL, ROSE HIP, and 100 ML.",
      "tags": ["#cleansingoil", "#rosehip", "#skincare"],
      "created_at": "2026-08-07T07:39:32.921950Z",
      "risk_assessment": {
        "rule_version": "content-risk-hints-2026-08-14.2",
        "findings": []
      },
      "has_image_preview": true,
      "image_preview_url": "/api/v1/generations/550e8400-e29b-41d4-a716-446655440000/image-preview"
    }
  ],
  "count": 1
}
```

New records store a metadata-free WebP preview with a maximum edge of 480 px and encoded
size no greater than 256 KiB. Preview reads and soft deletes are always owner-scoped. A
soft delete clears copy and preview content; repeating the delete is idempotent. Legacy
rows without previews can still be restored for their copy and display a placeholder.

Templates still default to `AUTH_ENABLED=false` NULL-owner compatibility. Enable backend
`AUTH_ENABLED` and frontend `VITE_AUTH_ENABLED` together only after controlled database
migration. Even with account isolation in place, the cookie, in-memory rate limiting, and
operational boundaries are for local development and demonstrations only. Do not bind the
authenticated application to `0.0.0.0` or expose it on a LAN or the public internet.

## Error Responses

All application errors use one structure:

```json
{
  "error": {
    "code": "MODEL_OUTPUT_INVALID",
    "message": "模型返回的内容格式无效，请重试。",
    "retryable": true
  }
}
```

Error messages are currently returned in Chinese; clients should branch on the stable
`code` and `retryable` fields rather than matching localized message text.

| HTTP | Code | Meaning | Retryable |
| --- | --- | --- | --- |
| 400 | `IMAGE_REQUIRED` | Missing or empty image | No |
| 400 | `FORM_FIELD_TOO_LARGE` | Optional text exceeds the form-parser boundary | No |
| 413 | `IMAGE_TOO_LARGE` | Image exceeds the configured byte limit | No |
| 415 | `UNSUPPORTED_IMAGE_TYPE` | Unsupported type or MIME/magic/decoded-format mismatch | No |
| 422 | `IMAGE_DECODE_FAILED` | Corrupted or undecodable image | No |
| 422 | `INVALID_IMAGE_DIMENSIONS` | Invalid dimensions or decoded pixel count too large | No |
| 502 | `MODEL_FAILED` | Upstream service, permission, rate-limit, or response failure | Depends on upstream |
| 502 | `MODEL_OUTPUT_INVALID` | Output cannot be normalized or fails factual-safety validation | Yes |
| 504 | `MODEL_TIMEOUT` | Total OCR + Qwen deadline exceeded | Yes |
| 500 | `DATABASE_ERROR` | Result could not be persisted | Yes |
| 500 | `INTERNAL_ERROR` | Unclassified internal failure | No |

Member B's `DATABASE_ERROR` mapping is connected to Member C's adapter. Live MySQL tests
confirm that successful and failed records use the same `generation_id`, while a duplicate
ID safely returns `DATABASE_ERROR` without overwriting the original record.

## Processing Pipeline

```text
Upload image
  → validate type, magic bytes, byte size, decoding, and dimensions
  → normalize EXIF, alpha, RGB, and scaling (convert HEIC/HEIF to JPEG)
  → create_pending (No-op unless MySQL is explicitly enabled)
  → PaddleOCR-VL (safe fallback on failure)
  → Qwen3-VL image understanding and copy generation
  → JSON parsing, field normalization, and bounded repair
  → factual-safety validation
  → optional deterministic Emoji / related-tag enhancement
  → structural and factual-rule revalidation
  → create and store a versioned publication-risk snapshot
  → mark_success / mark_failed (No-op unless MySQL is explicitly enabled)
  → return the fixed API response
```

The normal path calls OCR once and Qwen once. A single additional Qwen call is allowed
only when the first Qwen result needs repair.

## Tests

```bash
python -m pytest
```

Coverage includes:

- API form fields, binary uploads, and CORS;
- history ordering, limits, time zones, corrupted-record rejection, and database-error
  redaction;
- configuration validation and secret redaction;
- image formats, magic bytes, byte limits, decoding, dimensions, and multi-file rejection;
- EXIF orientation, transparency, large-image resizing, UUID temporary files, and cleanup;
- OCR fallback, Qwen requests, total timeout, cancellation, bounded repair, and error
  mapping;
- JSON/schema normalization, long-title handling, and factual-safety validation;
- image-summary, body, and per-tag length boundaries; risk snapshots; legacy compatibility;
  and corrupted-snapshot rejection;
- frontend routes, account isolation, Mock enhancements, loopback API boundaries,
  on-demand component loading, and the real Chrome flow.

Tests that call the real API incur model charges. Automated tests use doubles and never
call SiliconFlow by default.

Live MySQL tests are opt-in to prevent CI or routine local tests from writing a database.
They accept only the documented `xhs_app@127.0.0.1/xhs_ai_test` target and require:

```bash
XHS_MYSQL_LIVE_TEST_CONFIRM=YES_USE_XHS_AI_TEST \
  python -m pytest tests/test_mysql_live.py
```

The test uses three random UUIDs and deletes only those exact IDs afterward. It never
deletes a database, table, or unrelated row.

## GitHub Actions

The `CI` workflow runs when:

- a pull request targets `develop` or `main`;
- `develop` or `main` receives a commit;
- it is started manually from GitHub Actions.

It contains five independent jobs:

- **Python 3.12:** install backend development dependencies and run all `pytest` tests.
- **Node.js 24:** install frontend dependencies with `npm ci`, then build both compatibility
  mode and account mode.
- **Chrome:** run the real browser flow with a fixed model double and temporary SQLite,
  covering HttpOnly cookies, upload skeleton, risk hints, history preview, restore,
  deletion, and logout. It does not read the root `.env`, connect to developer MySQL, or
  call a third-party model.
- **Docker:** build the backend and run `/api/health` as non-root with a read-only
  filesystem, no network, and no Linux capabilities. It uses only an invalid placeholder
  key and does not connect to the model or MySQL.
- **Docker Compose:** use a temporary placeholder key, random database passwords, an
  internal no-egress bridge, and a new named volume to start MySQL and the backend. It
  validates non-root/read-only/capability boundaries, absence of secrets from container
  environment variables, no published `3306`, least-privilege application grants,
  persistence across container recreation, and then removes only that CI project's
  containers, network, test rows, and volume.

The workflow has repository-content read permission only. It uses no real SiliconFlow key,
connects to no external MySQL instance, logs into no image registry, pushes no image,
deploys nothing, and never edits or merges a pull request. Older runs are cancelled when
the same branch is pushed again.

## Team Integration Status

The current `main` branch integrates work from Members A, B, and C through pull requests:

- Member A's Vue application calls the real FastAPI service by default and includes upload,
  loading, result, copy, and retry states.
- Member B's multimodal generation, factual-safety validation, history, and HEIC/HEIF
  conversion are integrated.
- Member C's database responsibilities participate in the same generation lifecycle
  through the asynchronous SQLAlchemy adapter.
- The frontend accepts HEIC/HEIF. When the browser cannot preview it, the UI displays a
  file placeholder; the backend accepts single-frame images only and performs final
  validation and JPEG conversion.
- Local registration, login, session restoration, and logout are integrated; generation
  and history are user-isolated when account mode is enabled.
- The landing page, generation workspace, and history are routed Vue pages. Shared
  workspace state remains in page memory and is cleared whenever the account boundary
  changes.

Future enhancements must continue through dedicated feature branches and pull requests.
Do not push directly to `main`.
