from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import AsyncIterator, Dict, List, Optional, Tuple

from .schemas import (
    BackupListItem,
    BackupListResponse,
    BackupStatusResponse,
    UpdateEvent,
    UpdateStatusResponse,
)
from .version import get_current_versions, get_target_for_services

logger = logging.getLogger(__name__)


@dataclass
class UpdateSession:
    id: str
    state: str = "idle"
    phase: str = ""
    progress: int = 0
    started_at: Optional[datetime] = None
    log_lines: List[str] = field(default_factory=list)
    log_path: Optional[str] = None
    queue: asyncio.Queue[UpdateEvent] = field(default_factory=asyncio.Queue)
    cancel_requested: bool = False


class SingleFlight:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._active: Optional[str] = None

    async def try_acquire(self, update_id: str) -> bool:
        async with self._lock:
            if self._active is not None:
                return False
            self._active = update_id
            return True

    async def release(self, update_id: str) -> None:
        async with self._lock:
            if self._active == update_id:
                self._active = None

    def is_active(self) -> bool:
        return self._active is not None

    def get_active_id(self) -> Optional[str]:
        return self._active


class UpdaterOrchestrator:
    def __init__(self) -> None:
        self._sessions: Dict[str, UpdateSession] = {}
        self._sf = SingleFlight()
        self._logs_dir = os.environ.get("UPDATE_LOGS_DIR", "/update_logs")
        os.makedirs(self._logs_dir, exist_ok=True)
        # Backup/restore state
        self._backup_sessions: Dict[str, UpdateSession] = {}
        self._backup_sf = SingleFlight()

    async def cleanup_stale(self) -> None:
        active = self._sf.get_active_id()
        if not active:
            return
        sess = self._sessions.get(active)
        if not sess or sess.state in ("completed", "failed"):
            await self._sf.release(active)

    def is_active(self) -> bool:
        return self._sf.is_active()

    def is_effectively_active(self) -> bool:
        active = self._sf.get_active_id()
        if not active:
            return False
        sess = self._sessions.get(active)
        if not sess or sess.state in ("completed", "failed"):
            # clear stale flag asynchronously
            asyncio.create_task(self._sf.release(active))
            return False
        return True

    def get_active_id(self) -> Optional[str]:
        return self._sf.get_active_id()

    def get_session(self, update_id: str) -> Optional[UpdateSession]:
        return self._sessions.get(update_id)

    def create_session(self, update_id: str) -> UpdateSession:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        log_path = os.path.join(self._logs_dir, f"updater_{ts}.log")
        sess = UpdateSession(
            id=update_id,
            state="preflight",
            phase="Starting",
            progress=0,
            started_at=datetime.now(UTC),
            log_path=log_path,
        )
        self._sessions[update_id] = sess
        return sess

    def create_backup_session(self, backup_id: str) -> UpdateSession:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        log_path = os.path.join(self._logs_dir, f"backup_{ts}.log")
        sess = UpdateSession(
            id=backup_id,
            state="backup",
            phase="Starting",
            progress=0,
            started_at=datetime.now(UTC),
            log_path=log_path,
        )
        self._backup_sessions[backup_id] = sess
        return sess

    async def _write_log(self, sess: UpdateSession, line: str) -> None:
        try:
            if sess.log_path:
                with open(sess.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    async def emit(self, sess: UpdateSession, state: str, message: str, progress: int) -> None:
        sess.state = state
        sess.phase = message
        sess.progress = progress
        line = f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S,%f')} {state} {message}"
        sess.log_lines.append(line)
        await self._write_log(sess, line)
        # Mirror concise phase transitions to stdout for container logs (promtail scraping)
        try:
            print(line, flush=True)
        except Exception:
            pass
        await sess.queue.put(UpdateEvent(event="phase", state=state, message=message, ts=datetime.now(UTC)))

    async def run_update(self, update_id: str) -> None:
        acquired = await self._sf.try_acquire(update_id)
        if not acquired:
            return
        try:
            sess = self.get_session(update_id)
            if not sess:
                sess = self.create_session(update_id)

            await self.emit(sess, "preflight", "Validating environment", 5)
            await asyncio.sleep(0.2)
            # Capture previous digests for rollback
            prev = await get_current_versions(["app", "python_backend"])
            prev_app = prev.get("app", {})
            prev_backend = prev.get("backend", {})
            prev_map = {
                "app": prev_app.get("digest") or "",
                "python_backend": prev_backend.get("digest") or "",
                "app_repo": prev_app.get("repo") or "",
                "python_backend_repo": prev_backend.get("repo") or "",
            }

            if sess.cancel_requested:
                return
            await self.emit(sess, "backup", "Creating backups", 15)
            ok = await self._perform_backup_for_update(sess)
            if not ok:
                await self.emit(sess, "failed", "Backup failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="backup failed", ts=datetime.now(UTC)))
                return

            if sess.cancel_requested:
                return
            await self.emit(sess, "sync", "Downloading latest deployment files", 25)
            ok = await self._sync_deployment_repo(sess)
            if not ok:
                await self.emit(sess, "failed", "Failed to sync deployment files", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="deployment sync failed", ts=datetime.now(UTC)))
                return

            if sess.cancel_requested:
                return
            services = self._get_update_services()
            # Resolve target digests for forward pinning
            targets = await get_target_for_services([s for s in services if s in ("app", "python_backend")], channel=os.environ.get("VERSION_CHANNEL_DEFAULT", "stable"), target_version=None)
            await self.emit(sess, "pull", "docker compose pull", 40)
            ok = await self._compose(["pull", *services], sess)
            if not ok:
                await self.emit(sess, "failed", "docker compose pull failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="compose pull failed", ts=datetime.now(UTC)))
                return

            # Verify pulled digests match expected if targets known
            if targets:
                curr = await get_current_versions(["app", "python_backend"])
                mismatches = []
                for key, svc_key in (("app", "app"), ("backend", "python_backend")):
                    if svc_key not in targets:
                        continue
                    expected = targets[svc_key].get("digest")
                    cur = curr.get(key) or {}
                    cur_digest = cur.get("digest")
                    if expected and cur_digest and expected != cur_digest:
                        mismatches.append(f"{svc_key}: expected {expected} != current {cur_digest}")
                if mismatches:
                    await self.emit(sess, "failed", f"Digest verification failed: {'; '.join(mismatches)}", sess.progress)
                    await sess.queue.put(UpdateEvent(event="failed", state="failed", message="digest mismatch", ts=datetime.now(UTC)))
                    return

            if sess.cancel_requested:
                return
            await self.emit(sess, "migrate", "docker compose run --rm migrations alembic upgrade head", 60)
            ok = await self._compose(["run", "--rm", "migrations", "alembic", "upgrade", "head"], sess)
            if not ok:
                await self.emit(sess, "failed", "Migrations failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="migrations failed", ts=datetime.now(UTC)))
                return

            if sess.cancel_requested:
                return
            await self.emit(sess, "recreate", "docker compose up -d --no-build --no-deps --force-recreate --remove-orphans", 85)
            # If targets known, pin forward using persistent override
            if targets:
                workdir = os.environ.get("WORKDIR", "/workspace")
                override_path = os.path.join(workdir, "docker-compose.pinned.yml")
                content_lines = ["# Auto-generated by updater - DO NOT EDIT", "# Contains pinned image digests for version tracking", "services:"]
                if "app" in targets:
                    content_lines.append(f"  app:\n    image: {targets['app']['repo']}@{targets['app']['digest']}")
                if "python_backend" in targets:
                    content_lines.append(f"  python_backend:\n    image: {targets['python_backend']['repo']}@{targets['python_backend']['digest']}")
                with open(override_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(content_lines) + "\n")
                # Update the main compose command to always use the pinned file
                ok = await self._compose(["-f", "docker-compose.yml", "-f", override_path, "up", "-d", "--no-build", "--no-deps", "--force-recreate", "--remove-orphans", *services], sess)
            else:
                ok = await self._compose(["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--remove-orphans", *services], sess)
            if not ok:
                await self.emit(sess, "failed", "docker compose up -d failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="compose up failed", ts=datetime.now(UTC)))
                # Attempt rollback to previous digests
                await self._attempt_rollback(prev_map, sess)
                return

            if sess.cancel_requested:
                return
            await self.emit(sess, "healthcheck", "Verifying health", 90)
            # Wait for critical services to report healthy
            services_env = os.environ.get("UPDATE_HEALTH_SERVICES", "postgres,influxdb,backend")
            services = [s.strip() for s in services_env.split(",") if s.strip()]
            timeout_s = int(os.environ.get("HEALTH_TIMEOUT_SECONDS", "300"))
            ok = await self._wait_for_health(services, timeout_s, sess)
            if not ok:
                await self.emit(sess, "failed", "Health verification failed or timed out", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="health failed", ts=datetime.now(UTC)))
                # Attempt rollback to previous digests
                await self._attempt_rollback(prev_map, sess)
                return

            if sess.cancel_requested:
                return
            await self.emit(sess, "finalize", "Rebuilding updater for next update", 95)
            ok = await self._compose(["build", "updater"], sess)
            if not ok:
                # Non-fatal: updater rebuild failure doesn't invalidate the update
                await self.emit(sess, "warning", "Updater rebuild failed (non-fatal)", 98)
                sess.log_lines.append("Warning: Failed to rebuild updater - manual rebuild may be needed for next update")
                await self._write_log(sess, "Warning: Failed to rebuild updater - manual rebuild may be needed for next update")

            await self.emit(sess, "completed", "Update completed", 100)
            await sess.queue.put(UpdateEvent(event="completed", state="completed", message="done", ts=datetime.now(UTC)))
        except Exception as e:
            sess = self.get_session(update_id)
            if sess:
                await self.emit(sess, "failed", f"Error: {e}", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message=str(e), ts=datetime.now(UTC)))
        finally:
            await self._sf.release(update_id)

    async def _compose(self, args: List[str], sess: Optional[UpdateSession] = None) -> bool:
        workdir = os.environ.get("WORKDIR", "/workspace")
        project_name = os.environ.get("COMPOSE_PROJECT_NAME", "canopyos")

        docker_bin = self._resolve_docker_bin()
        if not docker_bin:
            if sess:
                await self.emit(sess, "failed", "Docker CLI not found in container PATH", 40)
            return False

        # Check if pinned override exists and add it to compose files
        pinned_path = os.path.join(workdir, "docker-compose.pinned.yml")
        base_cmd = [docker_bin, "compose", "-p", project_name]
        
        # If args already contain -f flags, don't auto-add pinned file
        if "-f" in args:
            cmd = base_cmd + args
        elif os.path.exists(pinned_path):
            # Auto-include pinned file for all compose operations
            cmd = base_cmd + ["-f", "docker-compose.yml", "-f", pinned_path] + args
        else:
            cmd = base_cmd + args
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workdir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )
            # Stream output into session logs
            assert proc.stdout is not None
            async def read_stream() -> None:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    if sess:
                        sess.log_lines.append(text)
                        await self._write_log(sess, text)
                        # Only forward concise progress lines to SSE to avoid overwhelming clients
                        if any(k in text for k in ("Pulling", "Pulled", "Downloading", "Extracting", "Complete", "complete", "already")):
                            await sess.queue.put(UpdateEvent(event="log", state=sess.state, message=text, ts=datetime.now(UTC)))

            try:
                await asyncio.wait_for(read_stream(), timeout=float(os.environ.get("COMPOSE_TIMEOUT_SECONDS", "600")))
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                if sess:
                    await self.emit(sess, "failed", f"Timeout running: {' '.join(cmd)}", sess.progress)
                return False
            finally:
                await proc.wait()

            return proc.returncode == 0
        except Exception as e:
            if sess:
                await self.emit(sess, "failed", f"Compose error: {e}", sess.progress)
            return False

    def _resolve_docker_bin(self) -> Optional[str]:
        docker_bin = os.environ.get("DOCKER_BIN") or shutil.which("docker")
        if docker_bin:
            return docker_bin
        for candidate in ("/usr/local/bin/docker", "/usr/bin/docker", "/usr/bin/docker.io"):
            if os.path.exists(candidate):
                return candidate
        return None

    async def _sync_deployment_repo(self, sess: UpdateSession) -> bool:
        """Download and extract latest deployment files from GitHub"""
        workdir = os.environ.get("WORKDIR", "/workspace")
        repo_url = os.environ.get("DEPLOYMENT_REPO_URL", "https://github.com/sabbalot/canopyos/archive/refs/heads/main.tar.gz")
        backup_dir = f"/tmp/workspace-backup-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        
        try:
            # Create backup of current workspace (excluding runtime data)
            await self.emit(sess, "sync", "Creating workspace backup", 25)
            backup_cmd = [
                "tar", "-czf", f"{backup_dir}.tar.gz",
                "--exclude=.secrets",
                "--exclude=volumes",
                "--exclude=node-red",
                "--exclude=.git",
                "--exclude=*.log",
                "-C", "/", "workspace"
            ]
            proc = await asyncio.create_subprocess_exec(
                *backup_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            await proc.wait()
            if proc.returncode != 0:
                logger.warning("Failed to create complete backup, continuing anyway")
            
            # Download repository tarball
            temp_file = "/tmp/canopyos-latest.tar.gz"
            await self.emit(sess, "sync", "Downloading repository archive", 26)
            
            proc = await asyncio.create_subprocess_exec(
                "curl", "-fSL", "-o", temp_file, repo_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            # Stream curl output to logs
            assert proc.stdout is not None
            async def read_curl_output() -> int:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    if text:
                        sess.log_lines.append(text)
                        await self._write_log(sess, text)
                await proc.wait()
                return proc.returncode
            
            returncode = await read_curl_output()
            if returncode != 0:
                await self.emit(sess, "failed", "Failed to download repository", sess.progress)
                # Clean up backup since we didn't even start extraction
                try:
                    if os.path.exists(f"{backup_dir}.tar.gz"):
                        os.remove(f"{backup_dir}.tar.gz")
                except Exception:
                    pass
                return False
            
            await self.emit(sess, "sync", "Extracting deployment files", 28)
            
            # Extract files, preserving runtime data
            extract_cmd = [
                "tar", "-xzf", temp_file,
                "--strip-components=1",
                "-C", workdir,
                "--exclude=.secrets",
                "--exclude=.secrets/*",
                "--exclude=volumes",
                "--exclude=volumes/*",
                "--exclude=.env",
                "--exclude=mosquitto/config/password.txt",
                "--exclude=node-red/*",  # Preserve Node-RED flows and config
                "--exclude=docker-compose.pinned.yml",  # Preserve current version pins
            ]
            
            proc = await asyncio.create_subprocess_exec(
                *extract_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            # Stream tar output
            assert proc.stdout is not None
            async def read_tar_output() -> int:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    if text and not text.startswith("tar:"):  # Filter noise
                        sess.log_lines.append(text)
                        await self._write_log(sess, text)
                await proc.wait()
                return proc.returncode
            
            returncode = await read_tar_output()
            
            # Clean up temp file
            try:
                os.remove(temp_file)
            except Exception:
                pass
            
            if returncode != 0:
                await self.emit(sess, "failed", "Failed to extract repository files", sess.progress)
                raise Exception("Extraction failed")  # Trigger restore
            
            await self.emit(sess, "sync", "Deployment files updated successfully", 30)
            
            # Clean up backup on success
            try:
                if os.path.exists(f"{backup_dir}.tar.gz"):
                    os.remove(f"{backup_dir}.tar.gz")
                    logger.debug(f"Removed backup: {backup_dir}.tar.gz")
            except Exception as e:
                logger.warning(f"Failed to remove backup: {e}")
            
            return True
            
        except Exception as e:
            await self.emit(sess, "failed", f"Repository sync error: {e}", sess.progress)
            
            # Attempt to restore from backup on failure
            if os.path.exists(f"{backup_dir}.tar.gz"):
                await self.emit(sess, "sync", "Restoring from backup after failure", sess.progress)
                try:
                    # First clear the workspace directory (except runtime data)
                    clear_cmd = [
                        "find", workdir,
                        "-mindepth", "1",
                        "-not", "-path", f"{workdir}/.secrets*",
                        "-not", "-path", f"{workdir}/volumes*",
                        "-not", "-path", f"{workdir}/node-red*",
                        "-not", "-path", f"{workdir}/.git*",
                        "-delete"
                    ]
                    await asyncio.create_subprocess_exec(*clear_cmd)
                    
                    # Then restore from backup
                    restore_cmd = ["tar", "-xzf", f"{backup_dir}.tar.gz", "-C", "/"]
                    proc = await asyncio.create_subprocess_exec(
                        *restore_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT
                    )
                    await proc.wait()
                    if proc.returncode == 0:
                        await self.emit(sess, "sync", "Restored workspace from backup", sess.progress)
                    else:
                        await self.emit(sess, "failed", "Failed to restore from backup", sess.progress)
                except Exception as restore_error:
                    logger.error(f"Restore failed: {restore_error}")
                finally:
                    # Clean up backup file
                    try:
                        os.remove(f"{backup_dir}.tar.gz")
                    except Exception:
                        pass
            
            return False

    def _get_update_services(self) -> List[str]:
        # By default, update all services except the updater itself
        exclude = set((os.environ.get("UPDATE_EXCLUDE", "updater").split(",")))
        exclude = set(s.strip() for s in exclude if s.strip())
        # Allow explicit include list
        include_env = os.environ.get("UPDATE_INCLUDE", "")
        if include_env.strip():
            return [s.strip() for s in include_env.split(",") if s.strip()]
        # Fallback to reading compose services via env var if provided
        default_services = [
            "influxdb",
            "postgres",
            "app",
            "python_backend",
            "docker-proxy",
            "grafana",
            "loki",
            "promtail",
            "migrations",
        ]
        return [s for s in default_services if s not in exclude]

    async def _attempt_rollback(self, prev_map: Dict[str, str], sess: UpdateSession) -> None:
        try:
            app_repo = prev_map.get("app_repo") or ""
            app_digest = prev_map.get("app") or ""
            be_repo = prev_map.get("python_backend_repo") or ""
            be_digest = prev_map.get("python_backend") or ""
            if not app_repo or not app_digest or not be_repo or not be_digest:
                await self.emit(sess, "failed", "Rollback skipped: previous digests unavailable", sess.progress)
                return
            workdir = os.environ.get("WORKDIR", "/workspace")
            pinned_path = os.path.join(workdir, "docker-compose.pinned.yml")
            content_lines = [
                "# Auto-generated by updater - DO NOT EDIT",
                "# Contains pinned image digests for version tracking",
                "services:"
            ]
            content_lines.append(f"  app:\n    image: {app_repo}@{app_digest}")
            content_lines.append(f"  python_backend:\n    image: {be_repo}@{be_digest}")
            with open(pinned_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content_lines) + "\n")
            await self.emit(sess, "recreate", "Rollback: recreating services with previous digests", max(85, sess.progress))
            # The _compose method will automatically use the pinned file
            ok = await self._compose(["up", "-d", "--force-recreate"], sess)
            if not ok:
                await self.emit(sess, "failed", "Rollback compose up failed", sess.progress)
                return
            # Best-effort health check after rollback
            services_env = os.environ.get("UPDATE_HEALTH_SERVICES", "postgres,influxdb,backend")
            services = [s.strip() for s in services_env.split(",") if s.strip()]
            timeout_s = int(os.environ.get("HEALTH_TIMEOUT_SECONDS", "300"))
            ok = await self._wait_for_health(services, timeout_s, sess)
            if ok:
                await self.emit(sess, "phase", "Rollback completed; services healthy", min(100, max(90, sess.progress)))
            else:
                await self.emit(sess, "failed", "Rollback completed but health check failed", sess.progress)
        except Exception as e:
            await self.emit(sess, "failed", f"Rollback error: {e}", sess.progress)

    async def _perform_backup_for_update(self, sess: UpdateSession) -> bool:
        """Create backups prior to update. Write logs into the update session."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        root = "/backups"
        paths = {
            "postgres": os.path.join(root, ts, "postgres"),
            "influx": os.path.join(root, ts, "influx"),
            "config": os.path.join(root, ts, "config"),
        }
        try:
            for p in paths.values():
                os.makedirs(p, exist_ok=True)
        except Exception as e:
            await self.emit(sess, "failed", f"Cannot create backup directories: {e}", sess.progress)
            return False

        ok_pg = await self._backup_postgres(paths["postgres"], sess)
        if not ok_pg:
            return False
        ok_influx = await self._backup_influx(paths["influx"], sess)
        if not ok_influx:
            return False
        ok_cfg = await self._backup_config(paths["config"], sess)
        if not ok_cfg:
            return False

        # Update latest symlink best-effort
        try:
            latest_link = os.path.join(root, "latest")
            if os.path.islink(latest_link) or os.path.exists(latest_link):
                try:
                    os.remove(latest_link)
                except Exception:
                    pass
            os.symlink(ts, latest_link)
        except Exception:
            # non-fatal
            pass
        # Prune older generations, keep 2
        try:
            gens = sorted([d for d in os.listdir(root) if d and d[0].isdigit() and os.path.isdir(os.path.join(root, d))])
            while len(gens) > 2:
                old = gens.pop(0)
                shutil.rmtree(os.path.join(root, old), ignore_errors=True)
        except Exception:
            pass
        return True

    async def _docker_exec(self, container: str, cmd: List[str], sess: Optional[UpdateSession] = None, timeout: Optional[float] = None) -> bool:
        docker_bin = self._resolve_docker_bin()
        if not docker_bin:
            if sess:
                await self.emit(sess, "failed", "Docker CLI not found", sess.progress)
            return False
        full_cmd = [docker_bin, "exec", container] + cmd
        try:
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            async def read_stream() -> None:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode(errors="replace").rstrip()
                    if sess:
                        sess.log_lines.append(text)
                        await self._write_log(sess, text)
            try:
                await asyncio.wait_for(read_stream(), timeout=timeout or 600.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                if sess:
                    await self.emit(sess, "failed", f"Timeout running: {' '.join(full_cmd)}", sess.progress)
                return False
            finally:
                await proc.wait()
            return proc.returncode == 0
        except Exception as e:
            if sess:
                await self.emit(sess, "failed", f"Exec error: {e}", sess.progress)
            return False

    async def _docker_cp_from(self, container: str, src_path: str, dest_path: str, sess: Optional[UpdateSession] = None) -> bool:
        docker_bin = self._resolve_docker_bin()
        if not docker_bin:
            if sess:
                await self.emit(sess, "failed", "Docker CLI not found", sess.progress)
            return False
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        except Exception:
            pass
        cmd = [docker_bin, "cp", f"{container}:{src_path}", dest_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if sess and out:
                text = out.decode(errors="replace").rstrip()
                if text:
                    sess.log_lines.append(text)
                    await self._write_log(sess, text)
            return proc.returncode == 0
        except Exception as e:
            if sess:
                await self.emit(sess, "failed", f"cp error: {e}", sess.progress)
            return False

    async def _backup_postgres(self, dest_dir: str, sess: UpdateSession) -> bool:
        await self.emit(sess, "backup", "Postgres: dumping database", max(20, sess.progress))
        # Dump to /tmp in postgres container then copy out
        dump_path = "/tmp/backup.dump"
        cmd = [
            "sh", "-lc",
            "pg_dump -U $(cat /run/secrets/postgres-user) -d $(cat /run/secrets/postgres-db) -F c -f /tmp/backup.dump"
        ]
        ok = await self._docker_exec("postgres", cmd, sess, timeout=900.0)
        if not ok:
            return False
        dest = os.path.join(dest_dir, "backup.dump")
        ok = await self._docker_cp_from("postgres", dump_path, dest, sess)
        # Cleanup best-effort
        await self._docker_exec("postgres", ["rm", "-f", dump_path], sess)
        return ok

    async def _backup_influx(self, dest_dir: str, sess: UpdateSession) -> bool:
        await self.emit(sess, "backup", "InfluxDB: creating backup", max(30, sess.progress))
        src_dir = "/tmp/influx_backup"
        cmd = [
            "sh", "-lc",
            "influx backup %s -t $(cat /run/secrets/influxdb-admin-token)" % src_dir,
        ]
        ok = await self._docker_exec("influxdb", cmd, sess, timeout=900.0)
        if not ok:
            return False
        dest = os.path.join(dest_dir, "backup")
        # Copy contents into destination directory (avoid nesting dir)
        ok = await self._docker_cp_from("influxdb", f"{src_dir}/.", dest, sess)
        await self._docker_exec("influxdb", ["rm", "-rf", src_dir], sess)
        return ok

    async def _backup_config(self, dest_dir: str, sess: UpdateSession) -> bool:
        await self.emit(sess, "backup", "Config: copying backend config volume", max(40, sess.progress))
        # Copy directory tree via docker cp
        src_dir = "/home/canopyos/config/."
        ok = await self._docker_cp_from("backend", src_dir, dest_dir, sess)
        return ok

    # Backup API surface
    def get_backup_session(self, backup_id: str) -> Optional[UpdateSession]:
        return self._backup_sessions.get(backup_id)

    def backup_status(self, backup_id: str) -> BackupStatusResponse:
        sess = self.get_backup_session(backup_id)
        if not sess:
            return BackupStatusResponse(backup_id=backup_id, state="idle", progress=0, phase="", log_tail=[], started_at=None)
        tail = sess.log_lines[-100:]
        return BackupStatusResponse(backup_id=backup_id, state=sess.state, progress=sess.progress, phase=sess.phase, log_tail=tail, started_at=sess.started_at)

    async def stream_backup(self, backup_id: str) -> AsyncIterator[UpdateEvent]:
        sess = self.get_backup_session(backup_id)
        if not sess:
            return
        heartbeat_interval = float(os.environ.get("SSE_HEARTBEAT_SECONDS", "15"))
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await sess.queue.put(UpdateEvent(event="progress", state=sess.state, message="heartbeat", ts=datetime.now(UTC)))
                except Exception:
                    break
        hb_task = asyncio.create_task(heartbeat())
        try:
            while True:
                evt = await sess.queue.get()
                yield evt
        finally:
            hb_task.cancel()

    async def run_backup(self, backup_id: str, scope: List[str]) -> None:
        acquired = await self._backup_sf.try_acquire(backup_id)
        if not acquired:
            return
        try:
            sess = self.get_backup_session(backup_id)
            if not sess:
                sess = self.create_backup_session(backup_id)
            await self.emit(sess, "backup", "Starting backup", 5)
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            root = "/backups"
            paths = {
                "postgres": os.path.join(root, ts, "postgres"),
                "influx": os.path.join(root, ts, "influx"),
                "config": os.path.join(root, ts, "config"),
            }
            for name in scope:
                os.makedirs(paths[name], exist_ok=True)
            ok_all = True
            if "postgres" in scope:
                ok_all = ok_all and await self._backup_postgres(paths["postgres"], sess)
            if "influx" in scope:
                ok_all = ok_all and await self._backup_influx(paths["influx"], sess)
            if "config" in scope:
                ok_all = ok_all and await self._backup_config(paths["config"], sess)
            if not ok_all:
                await self.emit(sess, "failed", "Backup failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="backup failed", ts=datetime.now(UTC)))
                return
            await self.emit(sess, "backup", "Finalizing backup", 95)
            # Update latest symlink and retention
            try:
                latest_link = os.path.join(root, "latest")
                if os.path.islink(latest_link) or os.path.exists(latest_link):
                    try:
                        os.remove(latest_link)
                    except Exception:
                        pass
                os.symlink(ts, latest_link)
                gens = sorted([d for d in os.listdir(root) if d and d[0].isdigit() and os.path.isdir(os.path.join(root, d))])
                while len(gens) > 2:
                    old = gens.pop(0)
                    shutil.rmtree(os.path.join(root, old), ignore_errors=True)
            except Exception:
                pass
            await self.emit(sess, "completed", "Backup completed", 100)
            await sess.queue.put(UpdateEvent(event="completed", state="completed", message="done", ts=datetime.now(UTC)))
        except Exception as e:
            sess = self.get_backup_session(backup_id)
            if sess:
                await self.emit(sess, "failed", f"Error: {e}", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message=str(e), ts=datetime.now(UTC)))
        finally:
            await self._backup_sf.release(backup_id)

    def list_backups(self) -> BackupListResponse:
        root = "/backups"
        items: List[BackupListItem] = []
        try:
            for name in sorted([d for d in os.listdir(root) if d and d[0].isdigit() and os.path.isdir(os.path.join(root, d))], reverse=True):
                path = os.path.join(root, name)
                size = 0
                for dirpath, _, filenames in os.walk(path):
                    for fn in filenames:
                        try:
                            size += os.path.getsize(os.path.join(dirpath, fn))
                        except Exception:
                            pass
                scope: List[str] = []
                if os.path.isdir(os.path.join(path, "postgres")):
                    scope.append("postgres")
                if os.path.isdir(os.path.join(path, "influx")):
                    scope.append("influx")
                if os.path.isdir(os.path.join(path, "config")):
                    scope.append("config")
                # Parse ts from directory name (UTC)
                try:
                    dt = datetime.strptime(name, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                except Exception:
                    dt = datetime.now(UTC)
                items.append(BackupListItem(backup_id=name, created_at=dt, size_bytes=size, scope=scope))
        except Exception:
            pass
        return BackupListResponse(items=items)

    async def run_restore(self, restore_id: str, backup_id: str, scope: List[str]) -> None:
        # For now reuse backup session machinery
        sess = self.create_backup_session(restore_id)
        await self.emit(sess, "restore", f"Restoring from {backup_id}", 5)
        root = "/backups"
        base = os.path.join(root, backup_id)
        # Stop affected services first
        try:
            services_to_stop: List[str] = []
            if "postgres" in scope:
                services_to_stop.append("postgres")
            if "influx" in scope:
                services_to_stop.append("influxdb")
            if "config" in scope:
                services_to_stop.append("backend")
            if services_to_stop:
                await self.emit(sess, "restore", f"Stopping services: {', '.join(services_to_stop)}", 10)
                await self._compose(["stop", *services_to_stop], sess)
        except Exception:
            pass
        ok_all = True
        if "postgres" in scope:
            # Copy dump into container and restore
            dump_src = os.path.join(base, "postgres", "backup.dump")
            docker_bin = self._resolve_docker_bin()
            if not docker_bin:
                ok_all = False
            else:
                # docker cp local->container
                try:
                    proc = await asyncio.create_subprocess_exec(
                        docker_bin, "cp", dump_src, "postgres:/tmp/restore.dump",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    await proc.wait()
                    if proc.returncode != 0:
                        ok_all = False
                except Exception:
                    ok_all = False
                if ok_all:
                    cmd = [
                        "sh", "-lc",
                        "pg_restore -c -U $(cat /run/secrets/postgres-user) -d $(cat /run/secrets/postgres-db) /tmp/restore.dump"
                    ]
                    ok_all = ok_all and await self._docker_exec("postgres", cmd, sess, timeout=1800.0)
                    await self._docker_exec("postgres", ["rm", "-f", "/tmp/restore.dump"], sess)
        if "influx" in scope and ok_all:
            docker_bin = self._resolve_docker_bin()
            src_dir = os.path.join(base, "influx", "backup")
            if docker_bin and os.path.isdir(src_dir):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        docker_bin, "cp", src_dir, "influxdb:/tmp/influx_restore",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    await proc.wait()
                    if proc.returncode != 0:
                        ok_all = False
                except Exception:
                    ok_all = False
                if ok_all:
                    cmd = [
                        "sh", "-lc",
                        "influx restore /tmp/influx_restore/backup -t $(cat /run/secrets/influxdb-admin-token)"
                    ]
                    ok_all = ok_all and await self._docker_exec("influxdb", cmd, sess, timeout=1800.0)
                    await self._docker_exec("influxdb", ["rm", "-rf", "/tmp/influx_restore"], sess)
        if "config" in scope and ok_all:
            docker_bin = self._resolve_docker_bin()
            src_dir = os.path.join(base, "config")
            if docker_bin and os.path.isdir(src_dir):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        docker_bin, "cp", src_dir, "backend:/tmp/config_restore",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    await proc.wait()
                    if proc.returncode != 0:
                        ok_all = False
                except Exception:
                    ok_all = False
                if ok_all:
                    cmd = [
                        "sh", "-lc",
                        "rm -rf /home/canopyos/config.bak && mv /home/canopyos/config /home/canopyos/config.bak && mkdir -p /home/canopyos/config && cp -a /tmp/config_restore/. /home/canopyos/config && rm -rf /tmp/config_restore"
                    ]
                    ok_all = ok_all and await self._docker_exec("backend", cmd, sess, timeout=900.0)
        if ok_all:
            # Start services and brief health check
            try:
                services_to_start: List[str] = []
                if "postgres" in scope:
                    services_to_start.append("postgres")
                if "influx" in scope:
                    services_to_start.append("influxdb")
                if "config" in scope:
                    services_to_start.append("backend")
                if services_to_start:
                    await self.emit(sess, "restore", f"Starting services: {', '.join(services_to_start)}", 95)
                    await self._compose(["up", "-d", "--no-build", "--no-deps", *services_to_start], sess)
                # quick health
                services_env = os.environ.get("UPDATE_HEALTH_SERVICES", "postgres,influxdb,backend")
                services = [s.strip() for s in services_env.split(",") if s.strip()]
                await self._wait_for_health(services, 120, sess)
            except Exception:
                pass
            await self.emit(sess, "completed", "Restore completed", 100)
            await sess.queue.put(UpdateEvent(event="completed", state="completed", message="done", ts=datetime.now(UTC)))
        else:
            await self.emit(sess, "failed", "Restore failed", sess.progress)
            await sess.queue.put(UpdateEvent(event="failed", state="failed", message="restore failed", ts=datetime.now(UTC)))

    async def _inspect_health(self, container: str, docker_bin: str) -> Tuple[str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                docker_bin,
                "inspect",
                "-f",
                "{{.State.Health.Status}}|{{.State.Status}}",
                container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            text = (out or b"").decode().strip()
            if "|" in text:
                health, state = text.split("|", 1)
            else:
                health, state = text, text
            return health or "", state or ""
        except Exception:
            return "", ""

    async def _wait_for_health(self, containers: List[str], timeout_seconds: int, sess: UpdateSession) -> bool:
        docker_bin = self._resolve_docker_bin()
        if not docker_bin:
            return False
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        last_report = 0.0
        while True:
            if sess.cancel_requested:
                return False
            all_ok = True
            pending: List[str] = []
            unhealthy: List[str] = []
            for name in containers:
                health, state = await self._inspect_health(name, docker_bin)
                if health == "healthy" or state == "running":
                    continue
                all_ok = False
                if health == "unhealthy" or state == "exited":
                    unhealthy.append(f"{name}({health or state})")
                else:
                    pending.append(f"{name}({health or state})")

            now = asyncio.get_event_loop().time()
            if now - last_report > 5:
                msg = "; ".join(filter(None, [
                    f"pending: {', '.join(pending)}" if pending else "",
                    f"unhealthy: {', '.join(unhealthy)}" if unhealthy else "",
                ])) or "waiting for services to become healthy"
                prog = min(99, max(90, sess.progress + 1))
                await self.emit(sess, "healthcheck", msg, prog)
                last_report = now

            if all_ok:
                return True
            if now > deadline:
                return False
            await asyncio.sleep(2)

    async def stream(self, update_id: str) -> AsyncIterator[UpdateEvent]:
        sess = self.get_session(update_id)
        if not sess:
            # No session: stop generator to let API return 404 or close cleanly
            return
        # Emit periodic heartbeats to keep the connection alive if idle
        heartbeat_interval = float(os.environ.get("SSE_HEARTBEAT_SECONDS", "15"))
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(heartbeat_interval)
                try:
                    await sess.queue.put(UpdateEvent(event="progress", state=sess.state, message="heartbeat", ts=datetime.now(UTC)))
                except Exception:
                    break

        hb_task = asyncio.create_task(heartbeat())
        try:
            while True:
                evt = await sess.queue.get()
                yield evt
        finally:
            hb_task.cancel()

    def status(self, update_id: str) -> UpdateStatusResponse:
        sess = self.get_session(update_id)
        if not sess:
            return UpdateStatusResponse(update_id=update_id, state="idle", progress=0, phase="", log_tail=[], started_at=None)
        tail = sess.log_lines[-100:]
        return UpdateStatusResponse(update_id=update_id, state=sess.state, progress=sess.progress, phase=sess.phase, log_tail=tail, started_at=sess.started_at)

    def cancel(self, update_id: str) -> None:
        sess = self.get_session(update_id)
        if sess:
            sess.cancel_requested = True


orchestrator = UpdaterOrchestrator()
