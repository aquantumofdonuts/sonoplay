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

"""
DataStore abstraction layer for future database migration.
Currently backed by JSON files, but can be swapped for SQLAlchemy/ORM later.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, Optional, Any, Callable
from copy import deepcopy


class DataStore(ABC):
    """Abstract interface for data persistence operations."""
    
    @abstractmethod
    def get_device_stats(self, uuid: str) -> Dict[str, Any]:
        """Get statistics for a device by UUID."""
        pass
    
    @abstractmethod
    def save_device_stats(self, uuid: str, stats: Dict[str, Any]) -> None:
        """Save statistics for a device."""
        pass
    
    @abstractmethod
    def increment_play_count(self, uuid: str) -> None:
        """Increment play count for a device."""
        pass
    
    @abstractmethod
    def add_play_duration_ms(self, uuid: str, duration_ms: int) -> None:
        """Add to total play duration for a device."""
        pass
    
    @abstractmethod
    def mark_device_status(self, uuid: str, status: str) -> None:
        """Update device status (online/offline/playing)."""
        pass
    
    @abstractmethod
    def get_token_for_uuid(self, uuid: str) -> Optional[str]:
        """Get Plex token for a device."""
        pass
    
    @abstractmethod
    def set_token_for_uuid(self, uuid: str, token: str) -> None:
        """Set Plex token for a device."""
        pass
    
    @abstractmethod
    def get_dlna_name_alias(self, uuid: str) -> Optional[str]:
        """Get custom name alias for a device."""
        pass
    
    @abstractmethod
    def save_dlna_name_alias(self, uuid: str, name: str) -> None:
        """Save custom name alias for a device."""
        pass
    
    @abstractmethod
    def get_all_device_uuids(self) -> list[str]:
        """Get list of all known device UUIDs."""
        pass

    @abstractmethod
    def get_onboarding_state(self) -> Dict[str, Any]:
        """Return onboarding wizard state and flags."""
        pass

    @abstractmethod
    def set_onboarding_state(self, *, completed: Optional[bool] = None,
                              steps: Optional[Dict[str, Any]] = None,
                              completed_at: Optional[str] = None) -> None:
        """Persist onboarding wizard state changes."""
        pass

    @abstractmethod
    def get_audio_settings(self) -> Dict[str, Any]:
        """Get audio transcoding settings."""
        pass

    @abstractmethod
    def set_audio_settings(self, *, bitrate_kbps: Optional[int] = None,
                            sample_rate_hz: Optional[int] = None) -> None:
        """Persist audio transcoding settings."""
        pass


# Default audio settings - safe values for most DLNA speakers
DEFAULT_AUDIO_SETTINGS = {
    "bitrate_kbps": 1500,      # 1500 kbps covers CD quality (1411)
    "sample_rate_hz": 48000,   # 48kHz max for Sonos/most DLNA
}

DEFAULT_ONBOARDING_STATE = {
    "completed": False,
    "completed_at": None,
    "steps": {}
}


class JSONDataStore(DataStore):
    """JSON file-based implementation of DataStore."""
    
    def __init__(self, settings_instance):
        """Initialize with reference to existing Settings instance."""
        self._settings = settings_instance

    _META_KEY = "__meta__"

    def _load_data(self) -> Dict[str, Any]:
        return dict(self._settings.load_data())

    def _mutate_entry(self, uuid: str, mutator: Callable[[Dict[str, Any]], None]) -> None:
        # Hold the data lock across the whole read-modify-write; taking it
        # separately for the read and the write leaves a lost-update window
        # against concurrent writers (e.g. _mutate_device_stats).
        from settings import _data_lock
        with _data_lock:
            data = self._load_data()
            entry = dict(data.get(uuid, {}))
            mutator(entry)
            data[uuid] = entry
            self._settings.save_data(data)

    def _mutate_meta(self, mutator: Callable[[Dict[str, Any]], None]) -> None:
        from settings import _data_lock
        with _data_lock:
            data = self._load_data()
            meta = dict(data.get(self._META_KEY, {}))
            mutator(meta)
            data[self._META_KEY] = meta
            self._settings.save_data(data)

    def get_device_stats(self, uuid: str) -> Dict[str, Any]:
        """Get statistics for a device by UUID."""
        data = self._load_data()
        stats = dict(data.get(uuid, {}).get('stats', {}))
        return {
            'play_count': stats.get('play_count', 0),
            'play_duration_ms': stats.get('play_duration_ms', 0),
            'status': stats.get('status', 'offline'),
            'last_seen': stats.get('last_seen', None)
        }

    def save_device_stats(self, uuid: str, stats: Dict[str, Any]) -> None:
        """Save statistics for a device."""

        def mutator(entry: Dict[str, Any]) -> None:
            entry['stats'] = dict(stats)

        self._mutate_entry(uuid, mutator)

    @staticmethod
    def _stamp_last_seen(stats: Dict[str, Any]) -> None:
        stats['last_seen'] = datetime.now(timezone.utc).isoformat()

    def increment_play_count(self, uuid: str) -> None:
        """Increment play count for a device."""
        stats = self.get_device_stats(uuid)
        stats['play_count'] = stats.get('play_count', 0) + 1
        stats['status'] = 'playing'
        self._stamp_last_seen(stats)
        self.save_device_stats(uuid, stats)
    
    def add_play_duration_ms(self, uuid: str, duration_ms: int) -> None:
        """Add to total play duration for a device."""
        stats = self.get_device_stats(uuid)
        stats['play_duration_ms'] = stats.get('play_duration_ms', 0) + duration_ms
        self._stamp_last_seen(stats)
        self.save_device_stats(uuid, stats)
    
    def mark_device_status(self, uuid: str, status: str) -> None:
        """Update device status (online/offline/playing)."""
        stats = self.get_device_stats(uuid)
        stats['status'] = status
        self._stamp_last_seen(stats)
        self.save_device_stats(uuid, stats)
    
    def get_token_for_uuid(self, uuid: str) -> Optional[str]:
        """Get Plex token for a device."""
        data = self._load_data()
        return data.get(uuid, {}).get('token')
    
    def set_token_for_uuid(self, uuid: str, token: str) -> None:
        """Set Plex token for a device."""
        def mutator(entry: Dict[str, Any]) -> None:
            entry['token'] = token

        self._mutate_entry(uuid, mutator)
    
    def get_dlna_name_alias(self, uuid: str) -> Optional[str]:
        """Get custom name alias for a device."""
        data = self._load_data()
        return data.get(uuid, {}).get('alias')
    
    def save_dlna_name_alias(self, uuid: str, name: str) -> None:
        """Save custom name alias for a device."""
        def mutator(entry: Dict[str, Any]) -> None:
            entry['alias'] = name

        self._mutate_entry(uuid, mutator)
    
    def get_all_device_uuids(self) -> list[str]:
        """Get list of all known device UUIDs."""
        data = self._load_data()
        return [uuid for uuid in data.keys() if uuid != self._META_KEY]

    def get_onboarding_state(self) -> Dict[str, Any]:
        data = self._load_data()
        meta = dict(data.get(self._META_KEY, {}))
        onboarding = deepcopy(DEFAULT_ONBOARDING_STATE)
        stored_state = meta.get("onboarding", {})
        if isinstance(stored_state, dict):
            onboarding.update({k: v for k, v in stored_state.items() if k in onboarding})
            steps = stored_state.get("steps", {})
            onboarding["steps"] = steps if isinstance(steps, dict) else {}
        else:
            onboarding["steps"] = {}
        return onboarding

    def set_onboarding_state(self, *, completed: Optional[bool] = None,
                              steps: Optional[Dict[str, Any]] = None,
                              completed_at: Optional[str] = None) -> None:
        def mutator(meta: Dict[str, Any]) -> None:
            onboarding = dict(meta.get("onboarding", {}))
            if completed is not None:
                onboarding["completed"] = bool(completed)
            if steps is not None:
                onboarding["steps"] = dict(steps)
            if completed_at is not None:
                onboarding["completed_at"] = completed_at
            meta["onboarding"] = onboarding

        self._mutate_meta(mutator)

    def get_audio_settings(self) -> Dict[str, Any]:
        """Get audio transcoding settings from storage or defaults."""
        data = self._load_data()
        meta = dict(data.get(self._META_KEY, {}))
        audio = deepcopy(DEFAULT_AUDIO_SETTINGS)
        stored = meta.get("audio_settings", {})
        if isinstance(stored, dict):
            # Only update keys that exist in defaults
            audio.update({k: v for k, v in stored.items() if k in audio})
        return audio

    def set_audio_settings(self, *, bitrate_kbps: Optional[int] = None,
                            sample_rate_hz: Optional[int] = None) -> None:
        """Persist audio transcoding settings."""
        def mutator(meta: Dict[str, Any]) -> None:
            audio = dict(meta.get("audio_settings", {}))
            if bitrate_kbps is not None:
                audio["bitrate_kbps"] = int(bitrate_kbps) if bitrate_kbps > 0 else None
            if sample_rate_hz is not None:
                audio["sample_rate_hz"] = int(sample_rate_hz) if sample_rate_hz > 0 else None
            meta["audio_settings"] = audio

        self._mutate_meta(mutator)
