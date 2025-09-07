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

## Backup/Restore Details
- PostgreSQL (container name: `postgres`)
  - Dump:
    - `docker exec postgres pg_dump -U <user> -d <db> -F c -f /var/lib/postgresql/data/backup_<ts>.dump`
    - Prefer a dedicated backup path bind-mounted to the host (e.g., `./backups`).
  - Restore (rollback):
    - Stop dependent services, `pg_restore` into a freshly created database.
- InfluxDB 2 (container name: `influxdb`)
  - Backup:
    - `docker exec influxdb influx backup /var/lib/influxdb2/backup_<ts>`
    - Bind-mount `/var/lib/influxdb2/backup_<ts>` to `./backups/influx_<ts>`.
  - Restore:
    - Use `influx restore` into a clean instance or per-bucket.
- Config Volumes
  - Tar and store under `./backups/config_<ts>.tar.gz` for critical app config.

Note: The exact host paths can be standardized to `${INSTALL_DIR}/backups/<ts>/...`.

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

## Open Questions / Decisions
- Do we require signed manifests and digest pinning at this stage?
- Backup retention policy and storage location on the host.
- Whether to move Docker privileges out of `python_backend` to a separate updater container (Pattern A).

## Deliverables for Backend Developer
1) Implement the endpoints under `/admin/update/*` as specified above.
2) Provide an orchestrator component that executes the lifecycle and emits progress events.
3) For Pattern A, implement a minimal Updater microservice with the same orchestration and expose it to the backend via an internal HTTP client.
4) Ensure strict admin-only access, single-flight lock, and comprehensive error handling.


