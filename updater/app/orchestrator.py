from __future__ import annotations

import asyncio
import os
import shutil
import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import AsyncIterator, Dict, List, Optional, Tuple

from .schemas import UpdateEvent, UpdateStatusResponse


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

            if sess.cancel_requested:
                return
            await self.emit(sess, "backup", "Creating backups (stub)", 20)
            await asyncio.sleep(0.2)

            if sess.cancel_requested:
                return
            services = self._get_update_services()
            await self.emit(sess, "pull", "docker compose pull", 40)
            ok = await self._compose(["pull", *services], sess)
            if not ok:
                await self.emit(sess, "failed", "docker compose pull failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="compose pull failed", ts=datetime.now(UTC)))
                return

            if sess.cancel_requested:
                return
            await self.emit(sess, "migrate", "Running migrations (stub)", 60)
            await asyncio.sleep(0.2)

            if sess.cancel_requested:
                return
            await self.emit(sess, "recreate", "docker compose up -d --no-build --no-deps --force-recreate --remove-orphans", 85)
            ok = await self._compose(["up", "-d", "--no-build", "--no-deps", "--force-recreate", "--remove-orphans", *services], sess)
            if not ok:
                await self.emit(sess, "failed", "docker compose up -d failed", sess.progress)
                await sess.queue.put(UpdateEvent(event="failed", state="failed", message="compose up failed", ts=datetime.now(UTC)))
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
                return

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

        cmd = [docker_bin, "compose", "-p", project_name] + args
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
