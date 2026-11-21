"""Shared storage, Docker, permissions, and presentation helpers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"
SPECS_FILE = DATA_DIR / "specs.json"
BANNER_FILE = ROOT / "assets" / "banner.png"
BOT_OWNER_ID = 1481235590283530312
LOG = logging.getLogger("ktsbot")
# ``datetime.UTC`` exists only in Python 3.11+. Keep compatibility with
# Ubuntu releases that still ship Python 3.10.
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def display_bytes(value: float | int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    number = float(value)
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            return f"{number:.2f} {unit}"
        number /= 1024
    return f"{number:.2f} TiB"


def parse_docker_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.year > 1 else None


class JsonStore:
    """Small atomic JSON store; VPS lists are keyed by Discord user ID strings."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._user_locks: dict[int, asyncio.Lock] = {}

    def user_lock(self, user_id: int) -> asyncio.Lock:
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    @staticmethod
    def _normalize_users(payload: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid user data in users.json")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for user_id, value in payload.items():
            if isinstance(value, dict):
                records = [value]
            elif isinstance(value, list) and all(isinstance(item, dict) for item in value):
                records = value
            else:
                raise RuntimeError(f"Invalid VPS records for user {user_id}")
            for number, record in enumerate(records, start=1):
                record.setdefault("vps_number", number)
            normalized[str(user_id)] = records
        return normalized

    @staticmethod
    def _read(path: Path, default: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in {path}") from exc

    @staticmethod
    def _write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        temporary.replace(path)

    async def users(self) -> dict[str, list[dict[str, Any]]]:
        async with self.lock:
            payload = await asyncio.to_thread(self._read, USERS_FILE, {})
            return self._normalize_users(payload)

    async def get_users(self, user_id: int) -> list[dict[str, Any]]:
        return (await self.users()).get(str(user_id), [])

    async def get_user(self, user_id: int, vps_number: int = 1) -> dict[str, Any] | None:
        return next(
            (record for record in await self.get_users(user_id) if record["vps_number"] == vps_number),
            None,
        )

    async def get_user_by_container(self, container_id: str) -> dict[str, Any] | None:
        for records in (await self.users()).values():
            for record in records:
                if record.get("container_id") == container_id:
                    return record
        return None

    async def put_user(self, user_id: int, record: dict[str, Any]) -> None:
        async with self.lock:
            payload = await asyncio.to_thread(self._read, USERS_FILE, {})
            users = self._normalize_users(payload)
            records = users.setdefault(str(user_id), [])
            if any(item.get("container_id") == record.get("container_id") for item in records):
                raise RuntimeError("This VPS is already registered.")
            if any(item["vps_number"] == record["vps_number"] for item in records):
                raise RuntimeError(f"VPS #{record['vps_number']} is already registered.")
            records.append(record)
            await asyncio.to_thread(self._write, USERS_FILE, users)

    async def update_user(self, user_id: int, container_id: str, updates: dict[str, Any]) -> None:
        async with self.lock:
            payload = await asyncio.to_thread(self._read, USERS_FILE, {})
            users = self._normalize_users(payload)
            records = users.get(str(user_id), [])
            record = next(
                (item for item in records if item.get("container_id") == container_id),
                None,
            )
            if record is None:
                raise LookupError("This VPS is no longer registered.")
            record.update(updates)
            await asyncio.to_thread(self._write, USERS_FILE, users)

    async def remove_user(self, user_id: int, container_id: str) -> None:
        async with self.lock:
            payload = await asyncio.to_thread(self._read, USERS_FILE, {})
            users = self._normalize_users(payload)
            records = users.get(str(user_id), [])
            remaining = [record for record in records if record.get("container_id") != container_id]
            if len(remaining) == len(records):
                raise LookupError("This VPS is no longer registered.")
            if remaining:
                users[str(user_id)] = remaining
            else:
                users.pop(str(user_id), None)
            await asyncio.to_thread(self._write, USERS_FILE, users)

    async def admins(self) -> set[int]:
        async with self.lock:
            values = await asyncio.to_thread(self._read, ADMINS_FILE, [])
            return {int(value) for value in values}

    async def add_admin(self, user_id: int) -> None:
        async with self.lock:
            admins = set(await asyncio.to_thread(self._read, ADMINS_FILE, []))
            admins.add(user_id)
            await asyncio.to_thread(self._write, ADMINS_FILE, sorted(admins))

    async def remove_admin(self, user_id: int) -> None:
        async with self.lock:
            admins = set(await asyncio.to_thread(self._read, ADMINS_FILE, []))
            admins.discard(user_id)
            await asyncio.to_thread(self._write, ADMINS_FILE, sorted(admins))

    async def get_specs(self, guild_id: int) -> tuple[int, int]:
        async with self.lock:
            specs = await asyncio.to_thread(self._read, SPECS_FILE, {})
        if not isinstance(specs, dict):
            raise RuntimeError("Invalid server VPS specs in specs.json")
        values = specs.get(str(guild_id))
        if values is None:
            return DockerService.DEFAULT_RAM_MB, DockerService.DEFAULT_CORES
        if not isinstance(values, dict):
            raise RuntimeError(f"Invalid VPS specs for server {guild_id}")
        ram_mb = values.get("ram_mb")
        cores = values.get("cores")
        if (
            not isinstance(ram_mb, int) or isinstance(ram_mb, bool) or ram_mb < 1
            or not isinstance(cores, int) or isinstance(cores, bool) or cores < 1
        ):
            raise RuntimeError(f"Invalid VPS specs for server {guild_id}")
        return ram_mb, cores

    async def set_specs(self, guild_id: int, ram_mb: int, cores: int) -> None:
        if ram_mb < 1 or cores < 1:
            raise ValueError("RAM and CPU core limits must both be positive.")
        async with self.lock:
            specs = await asyncio.to_thread(self._read, SPECS_FILE, {})
            if not isinstance(specs, dict):
                raise RuntimeError("Invalid server VPS specs in specs.json")
            specs[str(guild_id)] = {"ram_mb": ram_mb, "cores": cores}
            await asyncio.to_thread(self._write, SPECS_FILE, specs)


class DockerService:
    IMAGE = "ktsbot-ubuntu:24.04-systemd-v4"
    DEFAULT_RAM_MB = 32 * 1024
    DEFAULT_CORES = 4
    MEMORY_LIMIT = DEFAULT_RAM_MB * 1024**2
    CPU_NANO = DEFAULT_CORES * 1_000_000_000

    def __init__(self) -> None:
        self._client: docker.DockerClient | None = None
        self._client_lock = asyncio.Lock()
        self._image_lock = asyncio.Lock()
        self._image_ready = False

    async def client(self) -> docker.DockerClient:
        async with self._client_lock:
            if self._client is None:
                base_url = os.getenv("DOCKER_BASE_URL")
                self._client = await asyncio.to_thread(
                    docker.DockerClient, base_url=base_url, timeout=60
                ) if base_url else await asyncio.to_thread(docker.from_env, timeout=60)
                await asyncio.to_thread(self._client.ping)
            return self._client

    async def get(self, name_or_id: str):
        client = await self.client()
        container = await asyncio.to_thread(client.containers.get, name_or_id)
        if (container.labels or {}).get("kts.host.managed") == "true":
            await self.ensure_restart_policy(container)
        return container

    @staticmethod
    async def ensure_restart_policy(container: Any) -> None:
        restart_policy = container.attrs.get("HostConfig", {}).get("RestartPolicy", {})
        if restart_policy.get("Name") != "unless-stopped":
            await asyncio.to_thread(
                container.update,
                restart_policy={"Name": "unless-stopped"},
            )

    @staticmethod
    def _build_image(client: docker.DockerClient) -> None:
        _, logs = client.images.build(
            path=str(ROOT / "container"),
            dockerfile="Dockerfile",
            tag=DockerService.IMAGE,
            rm=True,
        )
        for event in logs:
            if event.get("error"):
                raise DockerException(event["error"])

    async def ensure_image(self, client: docker.DockerClient) -> None:
        async with self._image_lock:
            if self._image_ready:
                return
            try:
                image = await asyncio.to_thread(client.images.get, self.IMAGE)
            except ImageNotFound:
                image = None
            if image is None or (image.labels or {}).get("kts.host.image_version") != "4":
                await asyncio.to_thread(self._build_image, client)
            self._image_ready = True

    async def reload(self, container: Any) -> None:
        await asyncio.to_thread(container.reload)

    async def ensure_started_at(self, container: Any) -> None:
        state = container.attrs.get("State", {})
        if parse_docker_time(state.get("StartedAt")) or status_name(container).lower() != "running":
            return
        command = (
            "host_uptime=$(awk 'NR == 1 {print $1}' /proc/uptime); "
            "start_ticks=$(awk '{sub(/^.*\\) /, \"\"); print $20}' /proc/1/stat); "
            "ticks_per_second=$(getconf CLK_TCK); "
            "awk -v host=\"$host_uptime\" -v start=\"$start_ticks\" "
            "-v hz=\"$ticks_per_second\" "
            "'BEGIN { if (hz > 0 && host >= start / hz) "
            "printf \"%.0f\\n\", host - start / hz; else exit 1}'"
        )
        try:
            result = await asyncio.to_thread(
                container.exec_run,
                ["sh", "-c", command],
            )
        except APIError as exc:
            LOG.warning("Could not infer VPS start time from PID 1: %s", exc)
            return
        if getattr(result, "exit_code", 1) != 0:
            return
        output = getattr(result, "output", b"")
        if isinstance(output, tuple):
            output = b"".join(part or b"" for part in output)
        if isinstance(output, bytes):
            output = output.decode("ascii", errors="replace")
        try:
            elapsed = int(str(output).strip())
        except ValueError:
            return
        if elapsed < 0:
            return
        state["StartedAt"] = (utcnow() - timedelta(seconds=elapsed)).isoformat()

    async def create(self, owner_id: int, vps_number: int, ram_mb: int, cores: int):
        client = await self.client()
        await self.ensure_image(client)
        suffix = "" if vps_number == 1 else f"-{vps_number - 1}"
        name = f"user_{owner_id}{suffix}"
        try:
            container = await asyncio.to_thread(
                client.containers.run,
                self.IMAGE,
                name=name,
                hostname=f"KTS-{owner_id}{suffix}",
                detach=True,
                tty=True,
                stdin_open=True,
                restart_policy={"Name": "unless-stopped"},
                privileged=True,
                network_mode="bridge",
                tmpfs={"/run": "rw,nosuid,nodev", "/run/lock": "rw,nosuid,nodev"},
                volumes={
                    "/sys/fs/cgroup": {
                        "bind": "/sys/fs/cgroup",
                        "mode": "rw",
                    }
                },
                mem_limit=ram_mb * 1024**2,
                nano_cpus=cores * 1_000_000_000,
                labels={
                    "kts.host.managed": "true",
                    "kts.host.owner_id": str(owner_id),
                    "kts.host.vps_number": str(vps_number),
                },
            )
            return container, True
        except APIError as exc:
            if getattr(exc.response, "status_code", None) != 409:
                raise

            # Docker name conflicts include stopped containers, which are not
            # visible in `docker ps`. Reuse only a container this service owns.
            try:
                existing = await asyncio.to_thread(client.containers.get, name)
            except NotFound:
                raise exc

            labels = existing.labels or {}
            if (
                labels.get("kts.host.managed") != "true"
                or labels.get("kts.host.owner_id") != str(owner_id)
                or labels.get("kts.host.vps_number") != str(vps_number)
            ):
                raise exc

            await asyncio.to_thread(existing.reload)
            await self.ensure_restart_policy(existing)
            if existing.attrs.get("Config", {}).get("Image") != self.IMAGE:
                raise RuntimeError(
                    "This existing container uses an older image. "
                    "Back up its data, then delete and recreate it to use the latest systemd, Wings, and Git image."
                )
            if existing.attrs.get("State", {}).get("Status") != "running":
                await asyncio.to_thread(existing.start)
                await asyncio.to_thread(existing.reload)
            return existing, False

    async def action(self, container: Any, action: str) -> None:
        method = getattr(container, action)
        await asyncio.to_thread(method)

    async def remove(self, container: Any) -> None:
        await asyncio.to_thread(container.remove, force=True)

    async def stats(self, container: Any) -> dict[str, Any]:
        return await asyncio.to_thread(container.stats, stream=False)

    async def exec(self, container: Any, command: str, *, detach: bool = False) -> Any:
        return await asyncio.to_thread(container.exec_run, ["bash", "-lc", command], detach=detach)


store = JsonStore()
docker_service = DockerService()


async def is_admin(member: discord.abc.User, guild: discord.Guild | None) -> bool:
    return (
        member.id == BOT_OWNER_ID
        or bool(guild and member.id == guild.owner_id)
        or member.id in await store.admins()
    )


async def can_manage(member: discord.abc.User, guild: discord.Guild | None, owner_id: int) -> bool:
    return member.id == owner_id or await is_admin(member, guild)


def status_name(container: Any) -> str:
    return str(container.attrs.get("State", {}).get("Status", "unknown")).capitalize()


def uptime(
    container: Any,
    fallback_started_at: str | None = None,
) -> str:
    if status_name(container).lower() != "running":
        return "Not running"
    started = parse_docker_time(container.attrs.get("State", {}).get("StartedAt"))
    if not started:
        started = parse_docker_time(fallback_started_at)
    if not started:
        return "Unavailable"
    seconds = max(0, int((utcnow() - started).total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    return f"{days}d {hours}h {minutes}m"


def stats_values(payload: dict[str, Any]) -> dict[str, str]:
    cpu = payload.get("cpu_stats", {})
    previous = payload.get("precpu_stats", {})
    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - previous.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(cpu.get("cpu_usage", {}).get("percpu_usage", []))
    cpu_percent = (cpu_delta / system_delta * online * 100) if cpu_delta >= 0 and system_delta > 0 and online > 0 else None
    networks = payload.get("networks", {})
    rx = sum(item.get("rx_bytes", 0) for item in networks.values())
    tx = sum(item.get("tx_bytes", 0) for item in networks.values())
    return {
        "cpu": f"{cpu_percent:.2f}%" if cpu_percent is not None else "Unavailable",
        "rx": display_bytes(rx) if networks else "Unavailable",
        "tx": display_bytes(tx) if networks else "Unavailable",
    }


def container_specs(container: Any, record: dict[str, Any] | None = None) -> tuple[int, float]:
    host_config = container.attrs.get("HostConfig", {})
    memory_bytes = host_config.get("Memory", 0)
    memory_mb = int(memory_bytes / 1024**2) if memory_bytes else 0
    if memory_mb < 1:
        memory_mb = int((record or {}).get("ram_mb", DockerService.DEFAULT_RAM_MB))

    nano_cpus = host_config.get("NanoCpus", 0)
    if nano_cpus:
        cores = nano_cpus / 1_000_000_000
    else:
        quota = host_config.get("CpuQuota", 0)
        period = host_config.get("CpuPeriod", 0)
        cores = quota / period if quota > 0 and period > 0 else 0
    if cores <= 0:
        cores = float((record or {}).get("cores", DockerService.DEFAULT_CORES))
    return memory_mb, cores


def management_text(record: dict[str, Any], container: Any) -> str:
    created = parse_docker_time(record.get("created_at"))
    ram_mb, cores = container_specs(container, record)
    state = container.attrs.get("State", {})
    state_lines = []
    if status_name(container).lower() != "running":
        if state.get("Error"):
            state_lines.append(f"**Container Error:** {state['Error']}")
        if state.get("OOMKilled"):
            state_lines.append("**Exit:** OOM killed (memory limit exceeded)")
        elif state.get("ExitCode") not in (None, 0):
            state_lines.append(f"**Exit Code:** {state['ExitCode']}")
    lines = [
        f"**Owner:** {record['owner_name']} (`{record['owner_id']}`)",
        f"**Container:** `{record['container_name']}` (`{record['container_id'][:12]}`)",
        f"**Status:** {status_name(container)}",
        "**OS:** Ubuntu 24.04",
        f"**VPS RAM Limit:** {display_bytes(ram_mb * 1024**2)}",
        f"**VPS CPU Limit:** {cores:g} cores",
        f"**Uptime:** {uptime(container, record.get('last_started_at'))}",
        f"**Creation Date:** {discord.utils.format_dt(created, 'F') if created else 'Unknown'}",
        *state_lines,
        "",
        f"-# Owner ID: {record['owner_id']} • VPS #{record.get('vps_number', 1)} "
        f"• VPS ID: {record['container_id']} • Use the controls below",
    ]
    return "\n".join(lines)


def owner_from_message(message: discord.Message) -> int | None:
    text = ""
    if message.embeds and message.embeds[0].footer:
        text = message.embeds[0].footer.text or ""
    if not text:
        text = component_text(message.components)
    match = re.search(r"Owner ID: (\d+)", text)
    return int(match.group(1)) if match else None


def component_text(components: Any) -> str:
    pieces: list[str] = []
    for component in components:
        content = getattr(component, "content", None)
        if content:
            pieces.append(content)
        children = getattr(component, "children", None)
        if children:
            pieces.append(component_text(children))
    return "\n".join(pieces)


def container_from_message(message: discord.Message) -> str | None:
    text = ""
    if message.embeds and message.embeds[0].footer:
        text = message.embeds[0].footer.text or ""
    if not text:
        text = component_text(message.components)
    match = re.search(r"VPS ID: ([a-fA-F0-9]+)", text)
    return match.group(1) if match else None


def docker_error(exc: Exception) -> str:
    if isinstance(exc, NotFound):
        return (
            "This VPS has been suspended for using QEMU. "
            "Suspended by AutoMod. Create a ticket to request unsuspension."
        )
    if isinstance(exc, ImageNotFound):
        return "The Ubuntu image could not be found or pulled."
    if isinstance(exc, (APIError, DockerException)):
        LOG.exception("Docker operation failed")
        return "Docker could not complete that operation. Check the bot logs and Docker daemon."
    LOG.exception("Unexpected operation failure")
    return "An unexpected error occurred."


def docker_error_kwargs(exc: Exception, *, edit: bool = False) -> dict[str, Any]:
    if isinstance(exc, NotFound):
        from ui_components import suspended_vps_view

        payload: dict[str, Any] = {"view": suspended_vps_view()}
        if edit:
            payload.update(content=None, embeds=[])
        return payload
    return {"content": docker_error(exc)}
