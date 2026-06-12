# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025 plexdlnaplayer-enhanced contributors
#
# This file is part of plexdlnaplayer-enhanced, a fork of plexdlnaplayer.
# Original project: https://github.com/songchenwen/plexdlnaplayer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from dotmap import DotMap  # type: ignore[import]

logger = logging.getLogger(__name__)

from settings import settings
from utils import convert_volume, extract_value

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from dlna.dlna_device import DlnaDevice
    from plex.adapters import PlexDlnaAdapter
    from plex.gdm import PlexGDM

# Local imports placed inside functions when necessary to avoid circular dependencies.


class VirtualDeviceError(Exception):
    """Base error for virtual device operations."""


class CapabilityMismatchError(VirtualDeviceError):
    """Raised when selected members do not share identical capabilities."""

    def __init__(self, message: str, offending_members: Optional[List[str]] = None):
        super().__init__(message)
        self.offending_members = offending_members or []


class UnknownMemberError(VirtualDeviceError):
    """Raised when a requested member device cannot be found."""

    def __init__(self, member_uuid: str):
        super().__init__(f"Unknown member device {member_uuid}")
        self.member_uuid = member_uuid


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_path() -> Path:
    """Return the virtual device store path (same base directory as settings data)."""
    base = Path(settings.config_path)
    base.mkdir(parents=True, exist_ok=True)
    return base.joinpath("virtual_devices.json")


async def _read_store() -> List[Dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []

    def _read() -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8") as fh:
            try:
                return json.load(fh)
            except json.JSONDecodeError:
                return []

    return await asyncio.to_thread(_read)


async def _write_store(definitions: Iterable[Dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = list(definitions)

    def _write() -> None:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=4)

    await asyncio.to_thread(_write)


@dataclass
class VirtualDeviceDefinition:
    uuid: str
    name: str
    member_uuids: List[str]
    capabilities_hash: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    is_heterogeneous: bool = False
    member_capabilities: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "VirtualDeviceDefinition":
        return cls(
            uuid=payload["uuid"],
            name=payload["name"],
            member_uuids=list(payload.get("member_uuids", [])),
            capabilities_hash=payload.get("capabilities_hash", ""),
            metadata=dict(payload.get("metadata", {})),
            created_at=payload.get("created_at", _now_iso()),
            updated_at=payload.get("updated_at", _now_iso()),
            is_heterogeneous=payload.get("is_heterogeneous", False),
            member_capabilities=dict(payload.get("member_capabilities", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        # dataclasses.asdict performs deep copy; ensure lists are JSON serializable already
        return payload


class VirtualDlnaDevice:
    """Runtime representation of a virtual DLNA group."""

    def __init__(self, definition: VirtualDeviceDefinition):
        self.definition = definition
        self.uuid = definition.uuid
        self.name = definition.name
        self.member_uuids = list(definition.member_uuids)
        self.capabilities_hash = definition.capabilities_hash
        # Heterogeneous support
        self.is_heterogeneous = definition.is_heterogeneous
        self.member_capabilities = dict(definition.member_capabilities)
        metadata = definition.metadata or {}
        self.model = metadata.get("model", f"{self.name} (Virtual Group)")
        self.ip = metadata.get("ip", "virtual")
        volume_meta = metadata.get("volume", {})
        self.volume_min = volume_meta.get("min", 0)
        self.volume_max = volume_meta.get("max", 100)
        self.volume_step = volume_meta.get("step", 1)
        self.services = metadata.get("services", [])
        self._metadata = metadata
        self._lock = asyncio.Lock()
        # Track if this virtual device is actively playing (commanded to play, not just members playing)
        self._is_actively_playing = False
        self._attached_adapters = {}
        self._suspended_member_uuids = set()
        self._active_target_uri: Optional[str] = None

    # ---------------------------------------------------------------------
    # Utility helpers
    # ---------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"VirtualDlnaDevice(name={self.name!r}, members={len(self.member_uuids)})"

    def __str__(self) -> str:
        return self.name

    async def get_data(self):
        """Parity with DlnaDevice API. Nothing to hydrate, but keep awaitable."""
        return self

    def update_definition(self, definition: VirtualDeviceDefinition) -> None:
        self.definition = definition
        self.uuid = definition.uuid
        self.name = definition.name
        self.member_uuids = list(definition.member_uuids)
        self.capabilities_hash = definition.capabilities_hash
        # Heterogeneous support
        self.is_heterogeneous = definition.is_heterogeneous
        self.member_capabilities = dict(definition.member_capabilities)
        metadata = definition.metadata or {}
        self._metadata = metadata
        self.model = metadata.get("model", f"{self.name} (Virtual Group)")
        self.ip = metadata.get("ip", "virtual")
        volume_meta = metadata.get("volume", {})
        self.volume_min = volume_meta.get("min", 0)
        self.volume_max = volume_meta.get("max", 100)
        self.volume_step = volume_meta.get("step", 1)
        self.services = metadata.get("services", [])

    # ------------------------------------------------------------------
    # Member resolution helpers
    # ------------------------------------------------------------------
    async def _resolve_members(self) -> Tuple[List["DlnaDevice"], List[str]]:
        from dlna.dlna_device import devices as physical_devices  # avoid circular import

        resolved = []
        missing = []
        member_order = {uuid: idx for idx, uuid in enumerate(self.member_uuids)}

        for device in physical_devices:
            if device.uuid in member_order:
                await device.get_data()
                resolved.append(device)
        resolved.sort(key=lambda d: member_order.get(d.uuid, 0))
        missing = [uuid for uuid in self.member_uuids if uuid not in {d.uuid for d in resolved}]
        return resolved, missing

    async def _primary_device(self) -> Optional["DlnaDevice"]:
        from plex.adapters import adapter_by_device  # local import to avoid cycle

        resolved, _ = await self._resolve_members()
        if not resolved:
            return None
        for device in resolved:
            adapter = await adapter_by_device(device)
            stats = adapter.stats_snapshot()
            if stats.get("status") in {"playing", "online"}:
                return device
        return resolved[0]

    def _ensure_control(self, member_adapters: Iterable["PlexDlnaAdapter"]):
        active_ids = set()
        for adapter in member_adapters:
            if self._is_member_suspended(adapter.dlna.uuid):
                continue
            active_ids.add(adapter.dlna.uuid)
            current = self._attached_adapters.get(adapter.dlna.uuid)
            if current is not adapter:
                adapter.attach_virtual_controller(self)
                self._attached_adapters[adapter.dlna.uuid] = adapter
        # Detach adapters that are no longer active members
        for uuid, adapter in list(self._attached_adapters.items()):
            if uuid not in active_ids:
                adapter.detach_virtual_controller(self)
                self._attached_adapters.pop(uuid, None)

    def _release_all_control(self):
        for adapter in list(self._attached_adapters.values()):
            adapter.detach_virtual_controller(self)
        self._attached_adapters.clear()

    def _is_member_suspended(self, member_uuid: str) -> bool:
        return member_uuid in self._suspended_member_uuids

    def suspend_member(self, adapter: "PlexDlnaAdapter", *, reason: str = "") -> None:
        member_uuid = adapter.dlna.uuid
        if member_uuid in self._suspended_member_uuids:
            return
        self._suspended_member_uuids.add(member_uuid)
        note = f" ({reason})" if reason else ""
        logger.info("virtual group %s: suspending member %s%s", self.name, adapter.dlna.name, note)
        if adapter.virtual_controller() is self:
            adapter.detach_virtual_controller(self)
        self._attached_adapters.pop(member_uuid, None)

    def _filter_active_devices(self, devices: Iterable["DlnaDevice"]) -> List["DlnaDevice"]:
        return [device for device in devices if not self._is_member_suspended(device.uuid)]

    def _summarize_result(self, result: Any) -> str:
        if isinstance(result, DotMap):
            keys = list(result.keys())[:5]
            return f"DotMap keys={keys}"
        if isinstance(result, (str, int, float, bool)):
            return f"{type(result).__name__} value={result!r}"
        if result is None:
            return "None"
        return f"{type(result).__name__}"

    async def _fan_out(self, method_name: str, *args, **kwargs):
        from plex.adapters import adapter_by_device  # local import to avoid cycle
        
        resolved, _ = await self._resolve_members()
        if not resolved:
            raise RuntimeError("Virtual device has no available members online")

        if method_name in ("SetAVTransportURI", "Play"):
            if self._suspended_member_uuids:
                logger.info(
                    "virtual group %s: resuming %d suspended member(s) for %s",
                    self.name, len(self._suspended_member_uuids), method_name
                )
            self._suspended_member_uuids.clear()
            active_devices = resolved
        else:
            active_devices = self._filter_active_devices(resolved)
            if not active_devices:
                logger.info(
                    "virtual group %s: no active members available for %s; skipping command",
                    self.name, method_name
                )
                return None

        # Set flag on all member adapters to prevent them from tracking stats
        member_adapters = []
        for device in active_devices:
            member_adapters.append(await adapter_by_device(device))
        self._ensure_control(member_adapters)
        previous_flags: List[bool] = []
        for adapter in member_adapters:
            previous_flags.append(adapter._controlled_by_virtual_device)
            adapter._controlled_by_virtual_device = True

        try:
            # Helper to call method on a single device with logging
            async def call_member(device, method_name, *args, **kwargs):
                method = getattr(device, method_name)
                logger.debug("virtual group %s: %s start for member %s", self.name, method_name, device.name)
                try:
                    result = await method(*args, **kwargs)
                    summary = self._summarize_result(result)
                    logger.debug(
                        "virtual group %s: %s success for member %s -> %s",
                        self.name, method_name, device.name, summary
                    )
                    return result
                except Exception as exc:
                    logger.warning(
                        "virtual group %s: %s error for member %s: %s %s",
                        self.name, method_name, device.name, exc.__class__.__name__, exc
                    )
                    raise

            # Fan out to all members in parallel
            tasks = [
                call_member(device, method_name, *args, **kwargs)
                for device in active_devices
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Extract first success, track last exception
            first_success = None
            last_exception = None
            for result in results:
                if isinstance(result, Exception):
                    last_exception = result
                elif first_success is None:
                    first_success = result
            if first_success is None and last_exception is not None:
                raise last_exception
        finally:
            # Always restore the previous flag value after commands complete
            for adapter, previous in zip(member_adapters, previous_flags):
                adapter._controlled_by_virtual_device = previous
        return first_success

    async def _aggregate_first(self, method_name: str, *args, **kwargs):
        resolved, _ = await self._resolve_members()
        for device in resolved:
            try:
                method = getattr(device, method_name)
                result = await method(*args, **kwargs)
                if result is not None:
                    return result
            except Exception:
                continue
        return None

    async def handle_member_stop_request(self, adapter: "PlexDlnaAdapter") -> None:
        # Ignore direct stop requests while members are under virtual control so group playback persists
        logger.debug(
            "virtual group %s: ignoring stop request routed from member %s",
            self.name, adapter.dlna.name
        )

    # ------------------------------------------------------------------
    # DLNA-like control surface
    # ------------------------------------------------------------------
    async def SetAVTransportURI(self, uri: str, client=None):
        from plex.adapters import adapter_by_device  # local import to avoid cycle

        # Before starting virtual device playback, force any solo-playing members to
        # stop cleanly so the upcoming group start owns transport state entirely.
        resolved, _ = await self._resolve_members()
        member_adapters = []
        for device in resolved:
            member_adapters.append(await adapter_by_device(device))
        self._ensure_control(member_adapters)

        # Identify members that appear to be actively playing on their own.
        handoff_adapters = []
        for device, adapter in zip(resolved, member_adapters):
            state_value = getattr(adapter.state, "state", None)
            stats = adapter.stats_snapshot()
            is_playing = (state_value == "PLAYING") or (stats.get("status") == "playing")
            if is_playing:
                handoff_adapters.append((device, adapter))

        for device, adapter in handoff_adapters:
            elapsed_ms = getattr(adapter.state, "elapsed", 0) or 0
            logger.info(
                "virtual group %s: stopping solo playback on %s before group start (elapsed=%dms)",
                self.name, device.name, elapsed_ms
            )
            try:
                await adapter.stop(force=True)
            except Exception as exc:
                logger.warning(
                    "virtual group %s: failed to stop %s prior to group hand-off: %s",
                    self.name, device.name, exc
                )
            await asyncio.sleep(0.3)

        try:
            self._is_actively_playing = True  # Virtual device is being commanded to play
            self._active_target_uri = uri
            return await self._fan_out("SetAVTransportURI", uri, client=client)
        except Exception:
            self._is_actively_playing = False
            self._active_target_uri = None
            self._release_all_control()
            raise

    async def Play(self, client=None):
        self._is_actively_playing = True  # Virtual device is being commanded to play
        return await self._fan_out("Play", client=client)

    async def Stop(self, client=None):
        self._is_actively_playing = False  # Virtual device is being stopped
        self._active_target_uri = None
        try:
            return await self._fan_out("Stop", client=client)
        finally:
            self._release_all_control()

    async def Pause(self, client=None):
        self._is_actively_playing = False  # Virtual device is being paused
        return await self._fan_out("Pause", client=client)

    async def Seek(self, position: str, client=None):
        return await self._fan_out("Seek", position, client=client)

    async def SetVolume(self, volume_value: int, client=None):
        from plex.adapters import adapter_by_device  # local import to avoid cycle
        from dlna.virtual.volume import map_volume_to_device
        
        resolved, _ = await self._resolve_members()
        active_devices = self._filter_active_devices(resolved)
        if not active_devices:
            logger.info("virtual group %s: no active members available for SetVolume; skipping command", self.name)
            return True

        # Set flag on all member adapters to prevent them from tracking stats
        member_adapters = []
        for device in active_devices:
            member_adapters.append(await adapter_by_device(device))
        self._ensure_control(member_adapters)
        previous_flags: List[bool] = []
        for adapter in member_adapters:
            previous_flags.append(adapter._controlled_by_virtual_device)
            adapter._controlled_by_virtual_device = True

        try:
            coros = []
            for device in active_devices:
                # For heterogeneous groups, use member_capabilities for volume mapping
                if self.is_heterogeneous and device.uuid in self.member_capabilities:
                    caps = self.member_capabilities[device.uuid]
                    device_volume = map_volume_to_device(
                        volume_value,
                        caps.get("volume_min", 0),
                        caps.get("volume_max", 100),
                    )
                else:
                    # Homogeneous or missing capabilities: use device attributes
                    device_volume = convert_volume(
                        volume_value,
                        100,
                        0,
                        getattr(device, "volume_max", 100),
                        getattr(device, "volume_min", 0),
                        getattr(device, "volume_step", 1),
                    )
                coros.append(device.SetVolume(device_volume, client=client))
            results = await asyncio.gather(*coros, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    raise result
        finally:
            # Always clear the flag after commands complete
            for adapter, previous in zip(member_adapters, previous_flags):
                adapter._controlled_by_virtual_device = previous
        return True

    async def GetVolume(self, client=None):
        resolved, _ = await self._resolve_members()
        active_devices = self._filter_active_devices(resolved)
        if not active_devices:
            return DotMap(CurrentVolume=0)
        volumes = []
        for device in active_devices:
            try:
                info = await device.GetVolume(client=client)
                if info is not None:
                    volumes.append(int(extract_value(getattr(info, "CurrentVolume", 0), 0)))
            except Exception:
                continue
        if not volumes:
            # All member queries failed even though devices were resolved
            return DotMap(CurrentVolume=0)
        avg = sum(volumes) // len(volumes)
        return DotMap(CurrentVolume=avg)

    async def GetMute(self, client=None):
        resolved, _ = await self._resolve_members()
        active_devices = self._filter_active_devices(resolved)
        if not active_devices:
            return DotMap(CurrentMute=False)
        any_muted = False
        for device in active_devices:
            try:
                info = await device.GetMute(client=client)
                muted_val = extract_value(getattr(info, "CurrentMute", False), False)
                if isinstance(muted_val, str):
                    muted_val = muted_val.lower() in {"1", "true", "yes"}
                any_muted = any_muted or bool(muted_val)
            except Exception:
                continue
        return DotMap(CurrentMute=any_muted)

    async def GetTransportInfo(self, client=None):
        from plex.adapters import adapter_by_device  # local import to avoid cycle

        # Only return playing state if this virtual device was explicitly commanded to play
        # This prevents stats from incrementing when only member devices are playing individually
        if not self._is_actively_playing:
            return DotMap(CurrentTransportState="STOPPED")

        adapter = await adapter_by_device(self)
        override_state = getattr(adapter, "_transport_state_override", None)
        expected_uri = self._active_target_uri
        info = await self._aggregate_first("GetTransportInfo", client=client)

        state_val: Optional[str] = None
        if info is not None:
            raw_state = extract_value(getattr(info, "CurrentTransportState", None), None)
            if raw_state is not None:
                state_val = str(raw_state).upper()

        member_state = await self._summarize_member_state(expected_uri)
        state_val = self._prioritize_state(state_val, member_state)

        if override_state:
            override_val = str(override_state.get("state", "TRANSITIONING")).upper()
            state_val = self._prioritize_state(state_val, override_val)

        if not state_val:
            if expected_uri:
                state_val = "PLAYING" if getattr(adapter, "_active_operation_state_confirmed", False) else "TRANSITIONING"
            else:
                state_val = "STOPPED"

        if state_val == "STOPPED" and expected_uri and (
            getattr(adapter, "_active_operation_id", 0) or getattr(adapter, "_active_operation_state_ready", False)
        ):
            state_val = "PLAYING" if getattr(adapter, "_active_operation_state_confirmed", False) else "TRANSITIONING"

        if getattr(adapter, "_active_operation_target_paused", False):
            state_val = "PAUSED_PLAYBACK" if getattr(adapter, "_active_operation_state_confirmed", False) else "TRANSITIONING"

        if info is None:
            info = DotMap()

        info.CurrentTransportState = state_val
        return info

    async def GetPositionInfo(self, client=None):
        from plex.adapters import adapter_by_device  # local import to avoid cycle

        # Only return position info if this virtual device was explicitly commanded to play
        # This prevents stats from incrementing when only member devices are playing individually
        if not self._is_actively_playing:
            return DotMap(RelTime="00:00:00", TrackDuration="00:00:00", TrackURI="")

        adapter = await adapter_by_device(self)
        override_state = getattr(adapter, "_transport_state_override", None)
        current_uri = None
        if override_state:
            current_uri = override_state.get("current_uri") or self._active_target_uri
        else:
            current_uri = self._active_target_uri

        def _duration_from_track() -> str:
            duration_ms = getattr(getattr(adapter, "current_track_info", None), "duration", None)
            return self._format_ms(duration_ms)

        if override_state:
            track_duration = _duration_from_track()
            return DotMap(
                RelTime="00:00:00",
                TrackDuration=track_duration,
                TrackURI=current_uri or "",
            )

        info = await self._aggregate_first("GetPositionInfo", client=client)
        if info is None:
            return DotMap(
                RelTime="00:00:00",
                TrackDuration=_duration_from_track(),
                TrackURI=current_uri or "",
            )

        if current_uri:
            reported_uri = extract_value(getattr(info, "TrackURI", None), "")
            if reported_uri != current_uri:
                info.TrackURI = current_uri

        if not getattr(info, "TrackDuration", None):
            info.TrackDuration = _duration_from_track()

        return info

    @staticmethod
    def _state_priority(state: Optional[str]) -> int:
        priority_map = {
            "PLAYING": 500,
            "PAUSED_PLAYBACK": 400,
            "TRANSITIONING": 300,
            "STOPPED": 150,
            "NO_MEDIA_PRESENT": 100,
        }
        if state is None:
            return 0
        return priority_map.get(str(state).upper(), 0)

    @classmethod
    def _prioritize_state(cls, current: Optional[str], candidate: Optional[str]) -> Optional[str]:
        candidate_norm = str(candidate).upper() if candidate else None
        current_norm = str(current).upper() if current else None
        if candidate_norm is None:
            return current_norm
        if current_norm is None:
            return candidate_norm
        if cls._state_priority(candidate_norm) > cls._state_priority(current_norm):
            return candidate_norm
        return current_norm

    async def _summarize_member_state(self, expected_uri: Optional[str]) -> Optional[str]:
        from plex.adapters import adapter_by_device  # local import to avoid cycle

        resolved, _ = await self._resolve_members()
        active_devices = self._filter_active_devices(resolved)
        if not active_devices:
            return None

        best_state: Optional[str] = None
        best_score = -1
        for device in active_devices:
            adapter = await adapter_by_device(device)
            raw_state = getattr(adapter.state, "state", None)
            if raw_state is None:
                continue
            normalized = str(raw_state).upper()
            if not normalized:
                continue
            score = self._state_priority(normalized)
            current_uri = getattr(adapter.state, "current_uri", None)
            if expected_uri:
                if current_uri == expected_uri:
                    score += 1000
                elif current_uri:
                    score -= 50
            if score > best_score:
                best_state = normalized
                best_score = score
        return best_state

    @staticmethod
    def _format_ms(duration_ms: Optional[int]) -> str:
        if not duration_ms or duration_ms <= 0:
            return "00:00:00"
        total_seconds = max(0, int(duration_ms // 1000))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    # ------------------------------------------------------------------
    # Summaries and diagnostics
    # ------------------------------------------------------------------
    def _capability_snapshot(self) -> Dict[str, Any]:
        return {
            "services": list(self.services),
            "volume": {
                "min": self.volume_min,
                "max": self.volume_max,
                "step": self.volume_step,
            },
            "model": self.model,
            "capabilities_hash": self.capabilities_hash,
            "members": list(self.member_uuids),
        }

    async def member_details(self) -> List[Dict[str, Any]]:
        from plex.adapters import adapter_by_device
        from settings import settings as settings_singleton

        resolved, missing = await self._resolve_members()
        resolved_map = {d.uuid: d for d in resolved}
        details: List[Dict[str, Any]] = []

        expected_uri = self._active_target_uri if self._is_actively_playing else None

        for member_uuid in self.member_uuids:
            device = resolved_map.get(member_uuid)
            if device is not None:
                adapter = await adapter_by_device(device)
                stats = adapter.stats_snapshot()
                state_value = getattr(adapter.state, "state", None)
                normalized_state = str(state_value).upper() if state_value else None
                current_uri = getattr(adapter.state, "current_uri", None)
                elapsed_ms = getattr(adapter.state, "elapsed", 0) or 0
                status = stats.get("status", "offline")

                if normalized_state == "PLAYING":
                    status = "playing"
                elif normalized_state == "PAUSED_PLAYBACK":
                    status = "paused"
                elif normalized_state == "TRANSITIONING":
                    if self._is_actively_playing or (expected_uri and current_uri == expected_uri):
                        status = "playing"
                    elif status == "offline":
                        status = "online"
                elif normalized_state == "STOPPED" and self._is_actively_playing:
                    if expected_uri and current_uri == expected_uri and elapsed_ms > 0:
                        status = "playing"

                if status in {"online", "offline"} and self._is_actively_playing:
                    if expected_uri and current_uri == expected_uri:
                        status = "playing"
                    elif getattr(adapter, "_controlled_by_virtual_device", False) and elapsed_ms > 0:
                        status = "playing"

                available = status in {"online", "playing", "paused"}
                details.append(
                    {
                        "uuid": member_uuid,
                        "name": device.name,
                        "ip": device.ip,
                        "status": status,
                        "available": available,
                        "play_count": stats.get("play_count", 0),
                        "play_duration_ms": stats.get("play_duration_ms", 0),
                        "current_session_ms": stats.get("current_session_ms", 0),
                        "capabilities_hash": capability_signature(device),
                    }
                )
            else:
                # Device is not currently resolved (offline/missing)
                snapshot = self._metadata.get("members_snapshot", {}).get(member_uuid, {})
                stored_stats = settings_singleton.get_device_stats(member_uuid)
                # IMPORTANT: Override status to "offline" since device is not in resolved list
                # Even if stored_stats says "playing" or "online", if it's not resolved, it's offline
                details.append(
                    {
                        "uuid": member_uuid,
                        "name": snapshot.get("name", member_uuid),
                        "ip": snapshot.get("ip"),
                        "status": "offline",  # Force offline status for unresolved devices
                        "available": False,
                        "play_count": stored_stats.get("play_count", 0),
                        "play_duration_ms": stored_stats.get("play_duration_ms", 0),
                        "current_session_ms": stored_stats.get("current_session_ms", 0),
                        "capabilities_hash": snapshot.get("capabilities_hash"),
                    }
                )
        return details

    async def summary(self) -> Dict[str, Any]:
        from plex.adapters import adapter_by_device
        from plex import pin_login

        resolved, missing = await self._resolve_members()
        details = await self.member_details()

        overall_status = []
        current_track = None
        artwork_urls: List[str] = []
        plex_client = None

        # Collect member statuses for overall health
        for detail in details:
            status = detail.get("status", "offline")
            overall_status.append(status)
        

        # Get stats ONLY from the virtual device's own adapter
        # (not from member devices - only when playing directly to virtual device)
        virtual_adapter = await adapter_by_device(self)
        virtual_stats = virtual_adapter.stats_snapshot()
        aggregated_play_count = virtual_stats.get("play_count", 0)
        aggregated_play_duration = virtual_stats.get("play_duration_ms", 0)
        aggregated_session = virtual_stats.get("current_session_ms", 0)

        # Check the virtual device's own adapter for current track
        if virtual_adapter.current_track_info and current_track is None:
            track = virtual_adapter.current_track_info
            current_track = {
                "title": getattr(track, "title", None),
                "artist": getattr(track, "grandparentTitle", None),
                "album": getattr(track, "parentTitle", None),
                "duration": getattr(track, "duration", None),
                "key": getattr(track, "key", None),
                "ratingKey": getattr(track, "ratingKey", None),
                "grandparentKey": getattr(track, "grandparentKey", None),
                "parentKey": getattr(track, "parentKey", None),
                "grandparentRatingKey": getattr(track, "grandparentRatingKey", None),
                "parentRatingKey": getattr(track, "parentRatingKey", None),
            }
            plex_lib = virtual_adapter.plex_lib
            if plex_lib and plex_lib.protocol and plex_lib.address:
                track_key = getattr(track, "key", None)
                if track_key and plex_lib.machine_id:
                    current_track["plex_web_url"] = (
                        f"{plex_lib.protocol}://{plex_lib.address}:{plex_lib.port}/web/index.html#!/server/"
                        f"{plex_lib.machine_id}/details?key={track_key}"
                    )
                for attr in ["thumb", "art", "grandparentThumb", "parentThumb"]:
                    url = getattr(track, attr, None)
                    if url:
                        artwork_urls.append(plex_lib.build_url(url, token=True))
        if plex_client is None and virtual_adapter.plex_lib and virtual_adapter.plex_lib.address:
            plex_client = {
                "protocol": virtual_adapter.plex_lib.protocol,
                "address": virtual_adapter.plex_lib.address,
                "port": virtual_adapter.plex_lib.port,
                "machine_id": virtual_adapter.plex_lib.machine_id,
            }

        # Check member devices only for Plex connection fallback (not for stats or track info)
        if plex_client is None:
            for device in resolved:
                adapter = await adapter_by_device(device)
                if adapter.plex_lib and adapter.plex_lib.address:
                    plex_client = {
                        "protocol": adapter.plex_lib.protocol,
                        "address": adapter.plex_lib.address,
                        "port": adapter.plex_lib.port,
                        "machine_id": adapter.plex_lib.machine_id,
                    }
                    break

        # Determine aggregate status lamp
        available_count = sum(1 for status in overall_status if status in {"playing", "online"})
        if available_count == len(self.member_uuids) and available_count > 0:
            status_key = "all_available"
            status_class = "lamp-online"
            status_label = "Available"
        elif available_count == 0:
            status_key = "unavailable"
            status_class = "lamp-offline"
            status_label = "Unavailable"
        else:
            status_key = "degraded"
            status_class = "lamp-playing"
            status_label = "Degraded"
        

        token = settings.get_token_for_uuid(self.uuid)
        binded = token is not None
        pin_code = None
        pin_id = None
        if not binded:
            pin_code, pin_id = await pin_login.get_pin(self)

        return {
            "uuid": self.uuid,
            "name": self.name,
            "model": self.model,
            "ip": self.ip,
            "status": status_key,
            "status_label": status_label,
            "status_class": status_class,
            "members": details,
            "member_count": len(self.member_uuids),
            "missing_members": missing,
            "binded": binded,
            "pin": pin_code,
            "pin_id": pin_id,
            "play_count": aggregated_play_count,
            "play_duration_ms": aggregated_play_duration,
            "current_session_ms": aggregated_session,
            "elapsed_ms": getattr(virtual_adapter.state, "elapsed", 0) or 0,
            "current_track": current_track,
            "artwork_urls": artwork_urls,
            "plex_client": plex_client,
            "capabilities": self._capability_snapshot(),
        }

    async def refresh_member_snapshot(self) -> None:
        resolved, _ = await self._resolve_members()
        snapshot = {}
        for device in resolved:
            snapshot[device.uuid] = {
                "name": device.name,
                "ip": device.ip,
                "model": device.model,
                "capabilities_hash": capability_signature(device),
            }
        self._metadata.setdefault("members_snapshot", {}).update(snapshot)
        self.definition.metadata = self._metadata


_registry_lock = asyncio.Lock()
_virtual_devices: Dict[str, VirtualDlnaDevice] = {}
_virtual_gdm_sessions: Dict[str, "PlexGDM"] = {}


async def _ensure_virtual_runtime(device: VirtualDlnaDevice) -> None:
    """Ensure adapter and discovery advertisement exist for a virtual device."""
    from plex.adapters import adapters as adapter_registry, adapter_by_device
    from plex.gdm import PlexGDM

    adapter = adapter_registry.get(device.uuid)
    adapter_created = False
    if adapter is None:
        adapter = await adapter_by_device(device)
        adapter_created = True
    if adapter_created:
        adapter.start_plex_tv_notify()
    if settings.host_ip not in (None, "0.0.0.0"):
        asyncio.create_task(adapter.update_plex_tv_connection())

    existing_gdm = _virtual_gdm_sessions.get(device.uuid)
    if existing_gdm is not None:
        existing_gdm.stop()
    gdm = PlexGDM(device)
    _virtual_gdm_sessions[device.uuid] = gdm
    gdm.run()

    settings.mark_device_status(device.uuid, "online")


async def _stop_virtual_runtime(uuid_value: str, device: Optional[VirtualDlnaDevice] = None) -> None:
    """Tear down adapter and discovery advertisement for a virtual device."""
    from plex.adapters import adapters as adapter_registry, remove_adapter
    from plex.subscribe import sub_man

    adapter = adapter_registry.get(uuid_value)
    if adapter is not None:
        try:
            adapter.state.state = "STOPPED"
            adapter.state._thread_should_stop = True
            running_loop = getattr(adapter.state, "running_loop", None)
            looping_event = getattr(adapter.state, "looping_wait_event", None)
            if running_loop is not None and not running_loop.is_closed():
                def _wake() -> None:
                    if looping_event is not None:
                        looping_event.set()

                running_loop.call_soon_threadsafe(_wake)
            elif looping_event is not None:
                looping_event.set()
        except Exception as e:
            logger.debug("Expected cleanup error during adapter wakeup: %s", e)
        adapter.queue = None
        await remove_adapter(adapter)

    if device is None:
        device = _virtual_devices.get(uuid_value)
    if device is not None:
        try:
            await sub_man.notify_device_disconnected(device)
            await sub_man.notify_server_device(device, force=True)
        except Exception as e:
            logger.debug("Expected cleanup error during subscription notification: %s", e)

    gdm = _virtual_gdm_sessions.pop(uuid_value, None)
    if gdm is not None:
        gdm.stop()

    settings.mark_device_status(uuid_value, "offline")


async def load_virtual_devices() -> None:
    async with _registry_lock:
        # Tear down any existing runtime before reloading
        for uuid_value, device in list(_virtual_devices.items()):
            await _stop_virtual_runtime(uuid_value, device=device)
        _virtual_devices.clear()
        raw = await _read_store()
        for entry in raw:
            definition = VirtualDeviceDefinition.from_dict(entry)
            device = VirtualDlnaDevice(definition)
            _virtual_devices[device.uuid] = device
            await _ensure_virtual_runtime(device)


def list_virtual_devices() -> List[VirtualDlnaDevice]:
    return list(_virtual_devices.values())


async def list_virtual_devices_with_summaries() -> List[Dict[str, Any]]:
    devices = list_virtual_devices()
    return [await device.summary() for device in devices]


async def get_virtual_device_by_uuid(uuid_value: str) -> Optional[VirtualDlnaDevice]:
    device = _virtual_devices.get(uuid_value)
    if device is None:
        return None
    await device.get_data()
    return device


def capability_signature(device: "DlnaDevice") -> str:
    services = sorted(getattr(device, "services", {}).keys())
    volume_info = {
        "min": getattr(device, "volume_min", 0),
        "max": getattr(device, "volume_max", 100),
        "step": getattr(device, "volume_step", 1),
    }
    model = getattr(device, "model", "")
    info = getattr(device, "info", {}) or {}
    extra = {}
    if isinstance(info, dict):
        device_block = info.get("device", {})
        if isinstance(device_block, dict):
            for key in ("X_DLNACAP", "X_DLNADOC", "modelName", "modelDescription"):
                if key in device_block:
                    extra[key] = device_block[key]
    fingerprint = {
        "services": services,
        "volume": volume_info,
        "model": model,
        "extra": extra,
    }
    serialized = json.dumps(fingerprint, sort_keys=True, default=str)
    return uuid.uuid5(uuid.NAMESPACE_DNS, serialized).hex


async def _resolve_physical_members(member_uuids: Iterable[str]) -> List["DlnaDevice"]:
    from dlna.dlna_device import devices as physical_devices

    resolved: List["DlnaDevice"] = []
    requested = list(dict.fromkeys(member_uuids))  # preserve order, remove duplicates
    by_uuid = {device.uuid: device for device in physical_devices}
    for member_uuid in requested:
        device = by_uuid.get(member_uuid)
        if device is None:
            raise UnknownMemberError(member_uuid)
        await device.get_data()
        resolved.append(device)
    return resolved


async def _build_definition(name: str, member_devices: List["DlnaDevice"], existing_uuid: Optional[str] = None) -> VirtualDeviceDefinition:
    if not member_devices:
        raise VirtualDeviceError("Virtual device must include at least one member")

    # Compute signatures for all members
    member_signatures = {device.uuid: capability_signature(device) for device in member_devices}
    unique_signatures = set(member_signatures.values())
    is_heterogeneous = len(unique_signatures) > 1

    # Use first device's signature as the group's reference
    reference_signature = member_signatures[member_devices[0].uuid]

    # Store per-member capabilities
    member_capabilities = {}
    for device in member_devices:
        member_capabilities[device.uuid] = {
            "volume_min": getattr(device, "volume_min", 0),
            "volume_max": getattr(device, "volume_max", 100),
            "volume_step": getattr(device, "volume_step", 1),
            "capability_hash": member_signatures[device.uuid],
        }

    volume_info = {
        "min": member_devices[0].volume_min,
        "max": member_devices[0].volume_max,
        "step": member_devices[0].volume_step,
    }
    metadata = {
        "model": f"{name} (Virtual Group)",
        "ip": settings.host_ip or "virtual",
        "services": sorted(member_devices[0].services.keys()),
        "volume": volume_info,
        "members_snapshot": {
            device.uuid: {
                "name": device.name,
                "ip": device.ip,
                "model": device.model,
                "capabilities_hash": capability_signature(device),
            }
            for device in member_devices
        },
    }

    virtual_uuid = existing_uuid or f"virtual-{uuid.uuid4()}"
    now = _now_iso()
    definition = VirtualDeviceDefinition(
        uuid=virtual_uuid,
        name=name,
        member_uuids=[device.uuid for device in member_devices],
        capabilities_hash=reference_signature,
        is_heterogeneous=is_heterogeneous,
        member_capabilities=member_capabilities,
        metadata=metadata,
        created_at=now if existing_uuid is None else _virtual_devices[existing_uuid].definition.created_at,
        updated_at=now,
    )
    return definition


async def create_virtual_device(name: str, member_uuids: List[str]) -> Dict[str, Any]:
    async with _registry_lock:
        member_devices = await _resolve_physical_members(member_uuids)
        definition = await _build_definition(name, member_devices)
        device = VirtualDlnaDevice(definition)
        _virtual_devices[device.uuid] = device
        # Persist to store
        raw = await _read_store()
        raw.append(definition.to_dict())
        await _write_store(raw)

    await _ensure_virtual_runtime(device)
    return await device.summary()


async def update_virtual_device(uuid_value: str, *, name: Optional[str] = None, member_uuids: Optional[List[str]] = None) -> Dict[str, Any]:
    async with _registry_lock:
        device = _virtual_devices.get(uuid_value)
        if device is None:
            raise VirtualDeviceError(f"Virtual device {uuid_value} not found")

        # Load current definition data
        current_definition = device.definition
        new_name = name or current_definition.name

        if member_uuids is None:
            # Rename only – keep existing membership/meta, just bump updated_at/name
            definition = VirtualDeviceDefinition(
                uuid=current_definition.uuid,
                name=new_name,
                member_uuids=list(current_definition.member_uuids),
                capabilities_hash=current_definition.capabilities_hash,
                metadata=dict(current_definition.metadata),
                created_at=current_definition.created_at,
                updated_at=_now_iso(),
            )
        else:
            member_devices = await _resolve_physical_members(member_uuids)
            definition = await _build_definition(new_name, member_devices, existing_uuid=uuid_value)

        # Update runtime + store
        device.update_definition(definition)
        await device.refresh_member_snapshot()

        raw = await _read_store()
        for idx, entry in enumerate(raw):
            if entry.get("uuid") == uuid_value:
                raw[idx] = definition.to_dict()
                break
        await _write_store(raw)

    await _ensure_virtual_runtime(device)
    return await device.summary()


async def delete_virtual_device(uuid_value: str) -> None:
    async with _registry_lock:
        device = _virtual_devices.pop(uuid_value, None)
        if device is None:
            return

        raw = await _read_store()
        raw = [entry for entry in raw if entry.get("uuid") != uuid_value]
        await _write_store(raw)

    await _stop_virtual_runtime(uuid_value, device=device)
    settings.set_token_for_uuid(uuid_value, None)


async def list_physical_device_snapshots() -> List[Dict[str, Any]]:
    from dlna.dlna_device import devices as physical_devices
    from plex.adapters import adapter_by_device

    snapshots: List[Dict[str, Any]] = []
    for device in physical_devices:
        await device.get_data()
        adapter = await adapter_by_device(device)
        stats = adapter.stats_snapshot()
        status = stats.get("status", "offline")
        if status == "playing":
            lamp_class = "lamp-playing"
            label = "Playing"
        elif status == "online":
            lamp_class = "lamp-online"
            label = "Available"
        else:
            lamp_class = "lamp-offline"
            label = "Unavailable"
        snapshots.append(
            {
                "uuid": device.uuid,
                "name": device.name,
                "model": device.model,
                "ip": device.ip,
                "status": status,
                "status_class": lamp_class,
                "status_label": label,
                "play_count": stats.get("play_count", 0),
                "play_duration_ms": stats.get("play_duration_ms", 0),
                "capabilities_hash": capability_signature(device),
                "services": sorted(device.services.keys()),
                "volume": {
                    "min": device.volume_min,
                    "max": device.volume_max,
                    "step": device.volume_step,
                },
            }
        )
    return snapshots
