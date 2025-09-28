from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import httpx

from .schemas import VersionInfo

logger = logging.getLogger(__name__)


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


async def _inspect_image(image: str) -> Dict[str, Any]:
    docker_bin = _resolve_docker_bin()
    if not docker_bin or not image:
        return {}
    code, out = await _run_cmd([docker_bin, "image", "inspect", image], timeout=5.0)
    if code != 0:
        return {}
    try:
        arr = json.loads(out)
        if isinstance(arr, list) and arr:
            return arr[0]
    except Exception:
        pass
    return {}


def _parse_current_from_inspect(obj: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    # returns (image_ref, tag, digest, image_id)
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
        # If container is running an image pinned by digest, Config.Image may already contain repo@digest
        if isinstance(image_ref, str) and "@" in image_ref:
            try:
                digest = image_ref.split("@", 1)[1]
            except Exception:
                pass
        
        # Get Image ID as fallback identifier
        image_id = obj.get("Image") or ""
        if image_id.startswith("sha256:"):
            image_id = image_id[7:]  # Remove sha256: prefix
            
        return image_ref, tag, digest, image_id
    except Exception:
        return None, None, None, None


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
        image_ref, tag, digest, image_id = _parse_current_from_inspect(info)
        # If digest not present from container inspect, resolve via local image inspect
        if not digest:
            # Prefer inspecting by the container's Image field (full sha256:...)
            image_identifier = info.get("Image") or ""
            # Fallback to the ref string if available
            if not image_identifier:
                image_identifier = image_ref or ""
            img_info = await _inspect_image(image_identifier)
            repo_digests = img_info.get("RepoDigests") or []
            if isinstance(repo_digests, list) and repo_digests:
                prefer_repo = _repo_from_image_ref(image_ref)
                chosen: Optional[str] = None
                # Prefer a digest that matches the repo we ran with
                if prefer_repo:
                    for ref in repo_digests:
                        if isinstance(ref, str) and ref.startswith(prefer_repo + "@"):
                            chosen = ref
                            break
                if not chosen:
                    # Fallback to the first valid entry
                    for ref in repo_digests:
                        if isinstance(ref, str) and "@" in ref:
                            chosen = ref
                            break
                if chosen and "@" in chosen:
                    try:
                        digest = chosen.split("@", 1)[1]
                    except Exception:
                        pass
        
        # Note: We cannot fetch digest from registry for current version
        # because that would give us the latest digest, not what's running!
        # We use image_id as a fallback when RepoDigests is empty
        
        out_key = "backend" if svc == "python_backend" else svc
        out[out_key] = {
            "image": image_ref or "",
            "repo": _repo_from_image_ref(image_ref) or "",
            "tag": tag or "",
            "digest": digest or "",
            "image_id": image_id or "",
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
        cur_image_id = cur.get("image_id") or ""
        
        # Get latest digest
        lat_digest = latest_digests.get(svc_key) or ""
        
        # Compare digests when available
        if lat_digest:
            # Extract just the hash part for comparison
            lat_hash = lat_digest.split(":", 1)[1] if ":" in lat_digest else lat_digest
            
            # Compare against RepoDigest if available
            if cur_digest:
                cur_hash = cur_digest.split(":", 1)[1] if ":" in cur_digest else cur_digest
                if cur_hash != lat_hash:
                    return True
            # If we don't have a current digest, we cannot reliably compare against
            # a registry digest (image IDs are not comparable to manifest digests).
            # In that case, do not flag an update here; fall back to tag compare only
            # when latest digest is unavailable.
        
        if not lat_digest:
            # Fallback compare tags
            lat_ref = latest_services.get(svc_key) or ""
            lat_tag = lat_ref.split(":", 1)[1] if ":" in lat_ref and "@" not in lat_ref else ""
            cur_tag = cur.get("tag") or ""
            if lat_tag and cur_tag and lat_tag != cur_tag:
                return True
    return False


@dataclass
class _LatestCache:
    """Cache only the 'latest' (manifest/digests) to avoid registry pressure.
    Always recompute 'current' from local docker on each request.
    """

    latest: Optional[Dict[str, Any]] = None
    last_result: str = "ok"
    expires_at: Optional[datetime] = None
    min_refresh_at: Optional[datetime] = None
    channel: Optional[str] = None


_latest_cache = _LatestCache()


def invalidate_latest_cache() -> None:
    """Explicitly clear the latest cache (e.g., after successful update)."""
    _latest_cache.latest = None
    _latest_cache.last_result = "ok"
    _latest_cache.expires_at = None
    _latest_cache.min_refresh_at = None
    _latest_cache.channel = None


async def _get_latest(refresh: bool = False) -> Tuple[Dict[str, Any], str]:
    """Return latest info with caching. If refresh=True, bypass cache windows."""
    now = datetime.now(UTC)
    ttl_seconds = int(os.environ.get("VERSION_CACHE_TTL_SECONDS", "3600"))
    min_refresh_seconds = int(os.environ.get("VERSION_MIN_REFRESH_SECONDS", "120"))
    channel = os.environ.get("VERSION_CHANNEL_DEFAULT", "stable")

    # Use cached latest when valid and not forcing refresh
    if (
        not refresh
        and _latest_cache.latest is not None
        and _latest_cache.expires_at is not None
        and now < _latest_cache.expires_at
        and _latest_cache.channel == channel
    ):
        # Respect min refresh window only when not explicitly refreshed
        if _latest_cache.min_refresh_at is None or now < _latest_cache.min_refresh_at:
            return _latest_cache.latest, _latest_cache.last_result

    # (Re)compute latest
    latest: Dict[str, Any] = {"version": "latest", "services": {}}
    last_result = "ok"
    try:
        manifest = await _fetch_manifest(channel)
        if manifest:
            latest = _resolve_latest_from_manifest(manifest)
        else:
            # No manifest - fetch latest digests directly from Docker Hub
            logger.info("No manifest found, fetching latest digests from Docker Hub...")
            latest_digests: Dict[str, str] = {}
            for svc_key, image_ref in [("app", "phyrron/canopyos-app:latest"),
                                       ("python_backend", "phyrron/canopyos-backend:latest")]:
                try:
                    logger.info(f"Fetching latest digest for {image_ref}...")
                    digest = await resolve_tag_to_digest(image_ref)
                    if digest:
                        latest_digests[svc_key] = digest
                        logger.info(f"Got latest digest for {svc_key}: {digest}")
                except Exception as e:
                    logger.warning(f"Failed to fetch latest digest for {image_ref}: {e}")
            if latest_digests:
                latest["digests"] = latest_digests
    except Exception as e:
        last_result = f"manifest_error: {e}"

    # Update cache (even on errors, to avoid hot loops)
    _latest_cache.latest = latest
    _latest_cache.last_result = last_result
    _latest_cache.expires_at = now + timedelta(seconds=ttl_seconds)
    _latest_cache.min_refresh_at = now + timedelta(seconds=min_refresh_seconds)
    _latest_cache.channel = channel

    return latest, last_result


async def get_version_info(refresh: bool = False) -> VersionInfo:
    now = datetime.now(UTC)
    channel = os.environ.get("VERSION_CHANNEL_DEFAULT", "stable")

    # Always recompute current from local docker
    current = await get_current_versions(["app", "python_backend"])

    # Fetch latest using cache
    latest, last_result = await _get_latest(refresh=refresh)

    update_available = _compute_update_available(current, latest)

    return VersionInfo(
        current=current,
        latest=latest,
        update_available=update_available,
        update_in_progress=False,
        channel=channel,
        last_checked_at=now,
        last_result=last_result,
    )


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
    logger.debug(f"Fetching digest from {base}{path}")
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
                logger.debug(f"Got digest for {repository}:{reference} = {dcd}")
                return dcd
            else:
                logger.warning(f"No docker-content-digest header for {repository}:{reference}")
        else:
            logger.warning(f"Failed to fetch manifest: {resp.status_code}")
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


