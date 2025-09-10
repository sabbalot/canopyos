from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import httpx

from .schemas import VersionInfo


def _resolve_docker_bin() -> Optional[str]:
    docker_bin = os.environ.get("DOCKER_BIN")
    if docker_bin and os.path.exists(docker_bin):
        return docker_bin
    for candidate in ("docker", "/usr/local/bin/docker", "/usr/bin/docker", "/usr/bin/docker.io"):
        try:
            from shutil import which  # type: ignore

            found = which(candidate) if os.path.basename(candidate) == candidate else candidate
            if found and os.path.exists(found):
                return found
        except Exception:
            continue
    return None


async def _run_cmd(cmd: list[str], timeout: float = 10.0) -> Tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return 124, "timeout"
        return proc.returncode, (out or b"").decode(errors="replace")
    except Exception as e:
        return 1, str(e)


def _container_name_for_service(service: str) -> str:
    # Compose defines container_name for two critical services
    if service == "python_backend":
        return "backend"
    return service


async def _inspect_container(container: str) -> Dict[str, Any]:
    docker_bin = _resolve_docker_bin()
    if not docker_bin:
        return {}
    code, out = await _run_cmd([docker_bin, "inspect", container], timeout=5.0)
    if code != 0:
        return {}
    try:
        arr = json.loads(out)
        if isinstance(arr, list) and arr:
            return arr[0]
    except Exception:
        pass
    return {}


def _parse_current_from_inspect(obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    # returns (image_ref, tag, digest)
    try:
        image_ref = obj.get("Config", {}).get("Image")
        tag: Optional[str] = None
        if isinstance(image_ref, str) and ":" in image_ref and "@" not in image_ref:
            # repo:tag
            tag = image_ref.split(":", 1)[1]
        elif isinstance(image_ref, str) and "@" in image_ref:
            # repo@sha256:… → no tag
            tag = None
        digest: Optional[str] = None
        repo_digests = obj.get("RepoDigests") or []
        if isinstance(repo_digests, list) and repo_digests:
            try:
                ref = repo_digests[0]
                if isinstance(ref, str) and "@" in ref:
                    digest = ref.split("@", 1)[1]
            except Exception:
                pass
        return image_ref, tag, digest
    except Exception:
        return None, None, None


def _repo_from_image_ref(image_ref: Optional[str]) -> Optional[str]:
    if not image_ref:
        return None
    if "@" in image_ref:
        return image_ref.split("@", 1)[0]
    if ":" in image_ref:
        return image_ref.rsplit(":", 1)[0]
    return image_ref


async def get_current_versions(services: list[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for svc in services:
        name = _container_name_for_service(svc)
        info = await _inspect_container(name)
        image_ref, tag, digest = _parse_current_from_inspect(info)
        out_key = "backend" if svc == "python_backend" else svc
        out[out_key] = {
            "image": image_ref or "",
            "repo": _repo_from_image_ref(image_ref) or "",
            "tag": tag or "",
            "digest": digest or "",
        }
    return out


async def _fetch_manifest(channel: str) -> Optional[Dict[str, Any]]:
    url_tpl = os.environ.get("VERSION_MANIFEST_URL", "")
    if not url_tpl:
        return None
    url = url_tpl.format(channel=channel)
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _resolve_latest_from_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    latest: Dict[str, Any] = {
        "version": manifest.get("version", "latest"),
        "services": manifest.get("services", {}),
    }
    # Optionally include digests if present
    digests = manifest.get("digests") or {}
    if digests:
        latest["digests"] = digests
    return latest


def _compute_update_available(current: Dict[str, Dict[str, Any]], latest: Dict[str, Any]) -> bool:
    # Focus on app and backend
    latest_services = latest.get("services", {}) if isinstance(latest, dict) else {}
    latest_digests = latest.get("digests", {}) if isinstance(latest, dict) else {}
    for key, svc_key in (("app", "app"), ("backend", "python_backend")):
        cur = current.get(key) or {}
        cur_digest = cur.get("digest") or ""
        # Prefer digest comparison if provided; else fallback to tag mismatch
        lat_digest = latest_digests.get(svc_key) or ""
        if lat_digest and cur_digest and lat_digest != cur_digest:
            return True
        if not lat_digest:
            # Fallback compare tags
            lat_ref = latest_services.get(svc_key) or ""
            lat_tag = lat_ref.split(":", 1)[1] if ":" in lat_ref and "@" not in lat_ref else ""
            cur_tag = cur.get("tag") or ""
            if lat_tag and cur_tag and lat_tag != cur_tag:
                return True
    return False


@dataclass
class _Cache:
    payload: Optional[VersionInfo] = None
    expires_at: Optional[datetime] = None
    min_refresh_at: Optional[datetime] = None


_cache = _Cache()


async def get_version_info(refresh: bool = False) -> VersionInfo:
    now = datetime.now(UTC)
    ttl_seconds = int(os.environ.get("VERSION_CACHE_TTL_SECONDS", "3600"))
    min_refresh_seconds = int(os.environ.get("VERSION_MIN_REFRESH_SECONDS", "120"))
    channel = os.environ.get("VERSION_CHANNEL_DEFAULT", "stable")

    # Handle cache
    if _cache.payload and _cache.expires_at and now < _cache.expires_at:
        if not refresh or (_cache.min_refresh_at and now < _cache.min_refresh_at):
            return _cache.payload

    # Determine current
    current = await get_current_versions(["app", "python_backend"])

    # Determine latest from manifest (optional)
    latest: Dict[str, Any] = {"version": "latest", "services": {}}
    last_result = "ok"
    try:
        manifest = await _fetch_manifest(channel)
        if manifest:
            latest = _resolve_latest_from_manifest(manifest)
    except Exception as e:
        last_result = f"manifest_error: {e}"

    update_available = _compute_update_available(current, latest)

    payload = VersionInfo(
        current=current,
        latest=latest,
        update_available=update_available,
        update_in_progress=False,  # Caller (orchestrator) can update this flag separately
        channel=channel,
        last_checked_at=now,
        last_result=last_result,
    )

    # Cache
    _cache.payload = payload
    _cache.expires_at = now + timedelta(seconds=ttl_seconds)
    _cache.min_refresh_at = now + timedelta(seconds=min_refresh_seconds)
    return payload


# ---- Digest resolution and target mapping ----

def _parse_image_ref(image: str) -> Tuple[str, str, str]:
    """Return (registry, repository, reference) where reference is tag or digest.
    Defaults registry to docker hub.
    """
    # registry/repo[:tag|@digest]
    if "/" not in image.split("/")[0]:
        # No registry host, assume docker hub
        registry = "registry-1.docker.io"
        remainder = image
    else:
        parts = image.split("/", 1)
        registry, remainder = parts[0], parts[1]
        # Map docker.io to registry-1
        if registry in ("docker.io", "index.docker.io"):
            registry = "registry-1.docker.io"
    reference = "latest"
    if "@" in remainder:
        repository, reference = remainder.split("@", 1)
    elif ":" in remainder:
        repository, reference = remainder.rsplit(":", 1)
    else:
        repository = remainder
    # For official images on docker hub, insert library/
    if registry == "registry-1.docker.io" and "/" not in repository:
        repository = f"library/{repository}"
    return registry, repository, reference


async def _registry_auth_and_get_digest(registry: str, repository: str, reference: str) -> Optional[str]:
    """Resolve a tag or digest to a canonical digest using OCI Distribution API with anonymous auth.
    Returns digest like 'sha256:...'
    """
    base = f"https://{registry}"
    path = f"/v2/{repository}/manifests/{reference}"
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(base + path, headers={"Accept": accept})
        if resp.status_code == 401 and "www-authenticate" in resp.headers:
            hdr = resp.headers.get("www-authenticate", "")
            # Parse Bearer realm,service,scope
            try:
                scheme, rest = hdr.split(" ", 1)
                if scheme.lower() != "bearer":
                    return None
                parts = {}
                for kv in rest.split(","):
                    if "=" in kv:
                        k, v = kv.strip().split("=", 1)
                        parts[k.lower()] = v.strip('"')
                realm = parts.get("realm")
                service = parts.get("service")
                scope = parts.get("scope") or f"repository:{repository}:pull"
                if not realm or not service:
                    return None
                tok = await client.get(realm, params={"service": service, "scope": scope})
                tok.raise_for_status()
                token = tok.json().get("token") or tok.json().get("access_token")
                if not token:
                    return None
                resp = await client.get(base + path, headers={"Accept": accept, "Authorization": f"Bearer {token}"})
            except Exception:
                return None
        if resp.status_code >= 200 and resp.status_code < 300:
            dcd = resp.headers.get("Docker-Content-Digest") or resp.headers.get("docker-content-digest")
            if dcd:
                return dcd
    return None


async def resolve_tag_to_digest(image_ref: str) -> Optional[str]:
    """Resolve an image tag to a digest (sha256:...). If already digest, return it."""
    if "@sha256:" in image_ref:
        return image_ref.split("@", 1)[1]
    registry, repository, reference = _parse_image_ref(image_ref)
    if reference.startswith("sha256:"):
        return reference
    return await _registry_auth_and_get_digest(registry, repository, reference)


async def get_target_for_services(services: list[str], channel: Optional[str], target_version: Optional[str]) -> Dict[str, Dict[str, str]]:
    """Return mapping: service -> {repo, digest} for target images, using manifest then OCI fallback.
    Services should use compose keys (e.g., 'app', 'python_backend').
    """
    result: Dict[str, Dict[str, str]] = {}
    manifest: Optional[Dict[str, Any]] = None
    try:
        manifest = await _fetch_manifest(channel or os.environ.get("VERSION_CHANNEL_DEFAULT", "stable"))
    except Exception:
        manifest = None
    services_map: Dict[str, str] = {}
    if manifest and isinstance(manifest.get("services"), dict):
        services_map = manifest["services"]
    digests_map: Dict[str, str] = {}
    if manifest and isinstance(manifest.get("digests"), dict):
        digests_map = manifest["digests"]

    for svc in services:
        ref = services_map.get(svc)
        if not ref:
            # No manifest entry; skip
            continue
        repo = _repo_from_image_ref(ref) or ""
        digest = digests_map.get(svc)
        if not digest:
            digest = await resolve_tag_to_digest(ref) or ""
        if repo and digest:
            result[svc] = {"repo": repo, "digest": digest}
    return result


