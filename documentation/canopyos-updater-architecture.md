# CanopyOS Updater Architecture and Backend API

This document specifies a production-safe in-app update mechanism for CanopyOS deployments running via Docker Compose. It is inspired by Home Assistant Supervisor’s approach, not Watchtower, and is appropriate for a commercial product.

## Goals
- Provide a secure “Update CanopyOS” action from the web app.
- Pull new images, apply DB migrations, recreate services, and verify health.
- Offer progress reporting, logging, and a safe rollback strategy.
- Keep data safe with pre-update backups.

## Non-Goals
- Moving the platform to Kubernetes right now.
- Fully-automated background updates without user approval.

## Current Context (Compose Stack)
- `python_backend` and `app` images are pulled from the registry.
- A `migrations` service exists for Alembic migrations (runs and exits).
- A `docker-proxy` sidecar exposes the Docker Engine as `DOCKER_HOST=tcp://docker-proxy:2375` (already consumed by `python_backend`).

## High-Level Architecture
Two viable patterns are supported; pick one per deployment. Pattern A is recommended for production security.

### Pattern A (Recommended): Dedicated Updater Service
- A small, dedicated container with access to Docker Engine (via the existing `docker-proxy`).
- Exposes a local-only, authenticated HTTP API for update operations.
- The web app calls the backend, the backend calls the Updater API.
- The backend itself does not need Docker permissions.

Pros: Clear separation of duties, smaller attack surface, easier to harden and audit.  
Cons: One extra service to ship.

### Pattern B (Acceptable with Care): Backend Orchestrates Updates Directly
- The `python_backend` keeps `DOCKER_HOST` and uses the Docker Engine API (Python SDK) to orchestrate updates.
- No extra service, but tighter security discipline is required.

Pros: Fewer components.  
Cons: Backend is overly privileged; must enforce strict auth and rate limiting.

The API contract below is the same from the web app’s perspective. Only the internal hop differs (backend → updater service vs backend → Docker Engine).

## Decisions
- Use Pattern A (dedicated updater service with Docker access) to reduce the main backend’s privileges.
- Adopt two-generation backup retention (keep current and previous backups).
- For supply-chain security: start with digest pinning; plan to add signature verification in a later milestone.
- Use shared named volumes (`backups`, `update_logs`) for backups and updater logs.

## Security Model
- Strict auth: Only an admin user (supervisor role) can trigger updates.
- Local-only network exposure for the updater API (listen on `127.0.0.1` or internal bridge only).
- Single-flight lock: only one update can run at a time.
- No Docker TCP exposed externally; only via the internal `docker-proxy` service.
- Optionally verify release manifests and signed images (future-ready).

## Update Lifecycle (State Machine)
States: `idle` → `preflight` → `backup` → `pull` → `migrate` → `recreate` → `healthcheck` → `completed` (or `failed` with optional `rollback`).

Steps:
1) Preflight
   - Check disk space, network reachability (registry, GitHub), Docker Engine health, Compose files present.
   - Determine current versions (image tags/digests) for `app` and `python_backend`.
   - Fetch target version (from request or release channel manifest).
2) Backup (data safety)
   - PostgreSQL dump to a timestamped directory.
   - InfluxDB 2 backup to the same directory.
   - Config volumes tarball as needed.
3) Pull Images
   - Pull target images for `app`, `python_backend`, and any other changed services.
4) Migrate
   - Run Alembic migrations using the existing `migrations` image/command.
   - Abort on non-zero exit.
5) Recreate Services
   - Recreate and start services with the new images (detached).
6) Health Check
   - Verify backend health endpoint and UI responsiveness.
   - Timeouts and retries; emit detailed status events.
7) Completion or Rollback
   - On failure: stop updated services, restore DB from backup, optionally pull prior image digests and recreate.

## Shared Volumes for Backups and Logs
To standardize across services and the updater, use named volumes:

- `backups`: shared by `postgres`, `influxdb`, and the Updater for read/write of backup artifacts
- `update_logs`: written by the Updater (structured logs); read by `promtail`

Conceptual compose snippet (illustrative):
```yaml
services:
  postgres:
    volumes:
      - backups:/backups
  influxdb:
    volumes:
      - backups:/backups
  updater:
    volumes:
      - backups:/backups
      - update_logs:/update_logs
      - /var/run/docker.sock:/var/run/docker.sock  # or use docker-proxy

  promtail:
    volumes:
      - update_logs:/update_logs:ro

volumes:
  backups:
  update_logs:
```

## Backup/Restore Details (using named volumes)
- PostgreSQL (container name: `postgres`)
  - Dump:
    - Path: `/backups/postgres/backup_<ts>.dump` (named volume)
    - Command: `docker exec postgres pg_dump -U <user> -d <db> -F c -f /backups/postgres/backup_<ts>.dump`
  - Restore (rollback):
    - Stop dependent services, recreate DB if needed, then `pg_restore` from `/backups/postgres/backup_<ts>.dump`.
- InfluxDB 2 (container name: `influxdb`)
  - Backup:
    - Path: `/backups/influx/backup_<ts>` (directory inside named volume)
    - Command: `docker exec influxdb influx backup /backups/influx/backup_<ts>`
  - Restore:
    - `docker exec influxdb influx restore /backups/influx/backup_<ts>` into a clean instance or per-bucket as required.
- Config Volumes
  - Tar to `/backups/config/config_<ts>.tar.gz` for critical app configuration.

Note: For off-host archival, a scheduled job can sync `backups` volume contents to host filesystem or remote storage.

## Backup Retention Policy
- Strategy: Keep at most two generations per service (previous known-good and current candidate).
- Flow:
  1. Create a timestamped directory under `/backups/<ts>/` and write Postgres, Influx, and config backups there.
  2. On successful update, set `/backups/latest` symlink to `<ts>` and remove any older generations beyond the most recent previous one.
  3. On failed update, keep the previous known-good generation and optionally retain the failed attempt for diagnostics with a short TTL (e.g., 24–72 hours).
- Structure example:
  - `/backups/2025-01-20T12-34-56/postgres/backup.dump`
  - `/backups/2025-01-20T12-34-56/influx/backup/`
  - `/backups/2025-01-20T12-34-56/config/config.tar.gz`
  - `/backups/latest -> 2025-01-20T12-34-56`

## Health Checks
- Backend: `GET /health` (already present) should return 200 when operational.
- Optionally check the app container responds on `/` with HTTP 200.
- Compose healthchecks will also surface with `docker compose ps`.

## Release Management
- Channels: `stable` (default), `beta`.
- Target selection: explicit `target_version` (e.g., `1.4.2`) or latest in channel.
- Manifest (hosted by your release backend):
  - Defines version → image tags (and optionally OCI digests) per service.
  - Example:
    ```json
    {
      "version": "1.4.2",
      "services": {
        "app": "phyrron/canopyos-app:1.4.2",
        "python_backend": "phyrron/canopyos-backend:1.4.2"
      }
    }
    ```

### Version Discovery and Caching
- Preferred approach: query your release manifest API and compare to running containers.
- Fallback: query container registry for tag manifests (OCI Distribution API) and compare RepoDigests.
- Cache results server-side for 1–6 hours to reduce registry load; allow force refresh.

Implementation notes:
- Determine current version:
  - Read running containers’ image references and RepoDigests via Docker Engine API.
  - Optionally expose a readable `VERSION` file inside images for UI display.
- Determine latest version:
  - Fetch manifest for selected channel (`stable`, `beta`).
  - Validate semver and compatibility if needed.
- Caching:
  - Store the last fetched `latest` payload with a timestamp.
  - `GET /admin/version?refresh=true` bypasses cache for admins.

## Supply Chain: Signed Manifests and Digest Pinning
- Digest pinning: Resolve and use image digests (e.g., `phyrron/canopyos-app@sha256:<digest>`) instead of floating tags when recreating containers. Store expected digests in the release manifest and verify before deployment.
- Signed manifests/images: Use technologies like Sigstore Cosign or Notary v2 to sign image digests. The updater verifies signatures against a trusted public key before accepting an update.
  - Publisher signs `sha256:<digest>` of each image.
  - Updater fetches signature and verifies with a bundled/managed public key.
  - Reject updates if signature verification fails.
- Recommended phased approach:
  1) Phase 1: Implement digest pinning and record/verify digests from the release manifest.
  2) Phase 2: Add Cosign verification (Verifier in updater, public key provisioned securely).

## Backend API (consumed by Web App)

All endpoints require admin authentication and CSRF protection (if cookie-based). Responses use JSON. Long-running operations stream progress via Server-Sent Events (SSE) or polling.

### POST /admin/update/start
Starts an update.

Request body:
```json
{
  "target_version": "1.4.2",   
  "channel": "stable",         
  "force": false                
}
```

Response (202 Accepted):
```json
{
  "update_id": "a1b2c3d4",
  "state": "preflight"
}
```

Errors: 409 if an update is already running; 400 for invalid version.

### GET /admin/update/status?update_id=a1b2c3d4
Returns current state and progress.

Response (200):
```json
{
  "update_id": "a1b2c3d4",
  "state": "migrate",
  "progress": 62,
  "phase": "Running Alembic migrations",
  "log_tail": [
    "Pulling app:1.4.2...",
    "Pulled app:1.4.2",
    "Starting migrations..."
  ],
  "started_at": "2025-01-20T12:34:56Z"
}
```

### GET /admin/update/stream?update_id=a1b2c3d4
SSE stream of progress events.

Event payload example:
```json
{"event":"phase","state":"pull","message":"Pulling images"}
```

### POST /admin/update/cancel
Signals a best-effort cancellation if supported (preflight, pull, or before recreate).

Request body:
```json
{ "update_id": "a1b2c3d4" }
```

### GET /admin/version
Returns current and latest available versions.

Response:
```json
{
  "current": {
    "app": { "tag": "1.4.1", "digest": "sha256:..." },
    "backend": { "tag": "1.4.1", "digest": "sha256:..." }
  },
  "latest": {
    "version": "1.4.2",
    "services": {
      "app": "phyrron/canopyos-app:1.4.2",
      "python_backend": "phyrron/canopyos-backend:1.4.2"
    }
  }
}
```

## Updater Internals (implementation notes)

### Orchestration Flow (pseudocode)
```
acquire_lock()
emit(state=preflight)
ensure_disk_network_docker()
resolve_target_version()

emit(state=backup)
backup_postgres()
backup_influx()
backup_configs()

emit(state=pull)
pull_image(app)
pull_image(python_backend)

emit(state=migrate)
run_migrations()  # wait for exit code 0

emit(state=recreate)
recreate_services([app, python_backend])

emit(state=healthcheck)
wait_healthy()

emit(state=completed)
release_lock()
```

### Running migrations
- Prefer reusing the existing `migrations` definition.
- If using Docker Engine API directly, run a container from `phyrron/canopyos-backend:<target>` with:
  - Command: `alembic upgrade head`
  - Env: the same `POSTGRES_*` secrets as in compose
  - Network: `grow-net`
  - Volumes: `log_volume` for logs, `/etc/localtime:ro` as needed
- Treat non-zero exit as a hard failure.

### Recreating services
- With Docker Engine API: stop and remove existing containers for `app` and `backend`, then create and start new ones with the new image references and the same mounts/env/networks as defined in Compose.
- Or, if the updater container includes docker CLI+compose, shell out to `docker compose pull && docker compose up -d`.

## Error Handling and Rollback
- On any failure after `pull` and before `recreate`: abort and leave system unchanged.
- On failure during/after `recreate`:
  - Stop updated containers.
  - Restore DB from backup.
  - Optionally pin back to previous image digests (recorded during preflight).
  - Recreate services and verify health.

## Observability
- Emit structured events per phase; store a rolling log under a mounted logs volume.
- Surface last update result in `/admin/version` for UI badge.

### Updater Logs and Promtail
- Updater writes structured logs to `update_logs` volume, files like `/update_logs/updater_YYYYMMDDTHHMMSS.log`.
- Add a promtail scrape job for updater logs.

Promtail scrape job (illustrative):
```yaml
- job_name: 'updater'
  static_configs:
    - targets:
        - localhost
      labels:
        job: 'updater'
        __path__: /update_logs/updater_*.log
  pipeline_stages:
    - regex:
        expression: '^(?P<time>\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}[\\.,]?\\d{0,6})\\s+(?P<level_word>\\w+)\\s+(?P<message>.*)'
    - labels:
        level: level_word
    - timestamp:
        source: time
        format: '%Y-%m-%d %H:%M:%S,%f'
        fallback_formats:
          - '%Y-%m-%d %H:%M:%S.%f'
          - '%Y-%m-%d %H:%M:%S'
    - output:
        source: message
```

### Event Schema (recommended)
Emit JSON lines like:
```json
{"ts":"2025-01-20T12:35:01Z","state":"pull","severity":"info","message":"Pulling images"}
{"ts":"2025-01-20T12:35:10Z","state":"migrate","severity":"info","message":"Starting Alembic migrations"}
{"ts":"2025-01-20T12:36:40Z","state":"failed","severity":"error","message":"Healthcheck timed out"}
```

## Frontend UX Guidelines
- Indicate availability:
  - Poll `GET /admin/version` (or receive push) to show an "Update available" badge when `update_available=true`.
  - Show current vs latest version and changelog link (from manifest).
- Update flow:
  - Modal explaining steps (backup, pull, migrate, restart) and expected downtime.
  - Trigger `POST /admin/update/start` and subscribe to `/admin/update/stream` for live progress.
  - Display phase-by-phase progress, log tail, and failure handling with retry guidance.
- Post-update:
  - Show success message with updated version and link to logs.
  - Offer quick feedback channel if something looks wrong.

## DevOps and Security Checklist
- Least privilege:
  - Prefer Pattern A (dedicated updater) so the main backend does not have Docker privileges.
  - Restrict updater API to internal network only; require admin auth.
- Backups:
  - Verify `backups` volume mounted to all required services.
  - Implement retention policy and optional off-host sync.
- Health and Rollback:
  - Set reasonable timeouts per phase; verify health endpoints before completing.
  - Record previous image digests for rollbacks.
- Rate limiting and locking:
  - Single-flight lock during updates; rate-limit `GET /admin/version` refreshes.
- Telemetry:
  - Capture updater metrics (counts, durations, failures) for support visibility.

## Open Questions / Decisions
- Resolved: Pattern A (dedicated updater service) will be used.
- Resolved: Backups via shared `backups` named volume; retain at most two generations plus a temporary in-progress backup. Promote on success; prune older generations.
- Resolved: No signed manifests/images required in this phase; enforce digest pinning. Signature verification to be evaluated in a later milestone.
- Resolved: Updater writes to shared `update_logs` volume, scraped by Promtail.

## Deliverables for Backend Developer
1) Implement the endpoints under `/admin/update/*` as specified above.
2) Provide an orchestrator component that executes the lifecycle and emits progress events.
3) For Pattern A, implement a minimal Updater microservice with the same orchestration and expose it to the backend via an internal HTTP client.
4) Ensure strict admin-only access, single-flight lock, and comprehensive error handling.

## Deliverables for DevOps (this repo)
1) Define named volumes `backups` and `update_logs` in `docker-compose.yml` and mount them as described.
2) Add an `updater` service (Pattern A) with access to Docker Engine (via `docker-proxy` or `/var/run/docker.sock`), `backups`, and `update_logs` volumes.
3) Ensure `promtail` mounts `update_logs` and add a scrape job for updater logs in `promtail-config.yaml`.
4) Standardize backup directory layout under the `backups` volume and ensure the updater has permissions.
5) Optionally provide a cron/systemd timer or document how to export/sync backups off-host.

## Deliverables for App (Frontend)
1) Poll or subscribe for `GET /admin/version` to display an "Update available" badge and version info.
2) Implement an Update modal with steps overview, downtime notice, and a call to `POST /admin/update/start`.
3) Subscribe to `/admin/update/stream` for real-time progress and display phase progression with a log tail.
4) Handle success and failure states with clear user guidance and links to logs.
5) Provide a manual refresh control to re-check for updates and a permission gate for admin-only actions.
