import logging

from dotmap import DotMap
from starlette.datastructures import URL, QueryParams
import math

logger = logging.getLogger(__name__)

from utils import g

UNLIMITED = math.inf

MIN_QUEUE_GAP = 25


class PlayQueue(object):

    @classmethod
    def from_url(cls, url):
        from plex.adapters import PlexLib
        url = URL(url)
        plex_lib = PlexLib()
        plex_lib.protocol = url.scheme
        plex_lib.address = url.hostname
        plex_lib.port = url.port
        q = QueryParams(url.query)
        plex_lib.token = q.get("X-Plex-Token")
        return PlayQueue(url.path + "?" + url.remove_query_params("X-Plex-Token").query,
                         plex_lib)

    def __init__(self, container_key, plex_lib):
        self.container_key = container_key
        self.plex_lib = plex_lib
        self.info = None
        self.start_offset = None
        self.repeat = 0

    def _fetch_key(self) -> str:
        # Strip `own=1` — Plex enforces client-id ownership which won't match ours
        key = URL("http://x" + self.container_key).remove_query_params("own")
        return key.path + ("?" + key.query if key.query else "")

    async def get_info(self):
        if self.info is None:
            url = self.plex_lib.build_url(self._fetch_key())
            logger.debug("get queue %s", url)
            async with g.http.get(url, headers=self.plex_lib.request_headers(accept_json=True)) as res:
                res.raise_for_status()
                self.info = DotMap((await res.json())['MediaContainer'])
                for idx, track in enumerate(await self.available_tracks()):
                    if track.playQueueItemID == await self.selected_item_id():
                        self.start_offset = await self.selected_offset() - idx
                        break
        return self.info

    async def refresh_queue(self, playQueueID):
        if playQueueID != self.info.playQueueID:
            logger.debug("refresh to a different queue? %s -> %s", self.info.playQueueID, playQueueID)
            self.container_key = str(self.container_key).replace(str(self.info.playQueueID), str(playQueueID), 1)
        old_selected_item_id = await self.selected_item_id()
        old_selected_item_offset = await self.selected_offset()
        url = self.plex_lib.build_url(self._fetch_key())
        logger.debug("refresh queue from %s", url)
        async with g.http.get(url, headers=self.plex_lib.request_headers(accept_json=True)) as res:
            res.raise_for_status()
            info = DotMap((await res.json())['MediaContainer'])
            logger.debug(
                "refresh queue raw selected id/offset %s %s total %s metadata %s",
                info.playQueueSelectedItemID,
                info.playQueueSelectedItemOffset,
                info.playQueueTotalCount,
                len(info.Metadata)
            )
            found = 0
            new_available_offset = None
            start_offset = None
            for idx, track in enumerate(info.Metadata):
                if track.playQueueItemID == old_selected_item_id:
                    new_available_offset = idx
                    found += 1
                if track.playQueueItemID == info.playQueueSelectedItemID:
                    start_offset = info.playQueueSelectedItemOffset - idx
                    found += 1
                if found >= 2:
                    break
            if new_available_offset is None or start_offset is None:
                raise Exception("refreshed queue has no current selected item?")
            selected_offset = new_available_offset + start_offset
            logger.debug(
                "refreshed queue mapping oldOffset %s -> %s localStart %s -> %s "
                "newAvailableOffset %s rawSelectedOffset %s rawStartOffset %s",
                old_selected_item_offset, selected_offset,
                self.start_offset, start_offset,
                new_available_offset,
                info.playQueueSelectedItemOffset,
                start_offset
            )
        info.playQueueSelectedItemID = old_selected_item_id
        info.playQueueSelectedItemOffset = selected_offset
        self.info = info
        self.start_offset = start_offset

    async def set_selected_offset(self, offset):
        total = await self.total_count()
        if not (0 <= offset < total):
            raise ValueError(f"Queue offset {offset} out of range [0, {total})")
        await self.get_info()

        while True:
            last_offset = self.last_offset
            if last_offset is not None and offset > last_offset - MIN_QUEUE_GAP and last_offset + 1 < total:
                expanded = await self.more(after=True)
                if not expanded:
                    break
                continue
            if self.start_offset is not None and offset < self.start_offset + MIN_QUEUE_GAP and self.start_offset > 0:
                expanded = await self.more(after=False)
                if not expanded:
                    break
                continue
            break

        info = await self.get_info()
        selected_track = await self.track(offset)
        info.playQueueSelectedItemOffset = offset
        info.playQueueSelectedItemID = selected_track.playQueueItemID

    async def track(self, offset):
        if self.info is None:
            await self.get_info()
        total = await self.total_count()
        if not (0 <= offset < total):
            raise ValueError(f"Queue offset {offset} out of range [0, {total})")

        while True:
            last_offset = self.last_offset
            if last_offset is not None and offset > last_offset:
                if not await self.more(after=True):
                    raise IndexError(f"play queue cannot move forward to offset {offset}; last_offset={last_offset}")
                continue
            if self.start_offset is not None and offset < self.start_offset:
                if not await self.more(after=False):
                    raise IndexError(f"play queue cannot move backward to offset {offset}; start_offset={self.start_offset}")
                continue
            break

        local_offset = offset - (self.start_offset or 0)
        tracks = await self.available_tracks()
        return tracks[local_offset]

    async def selected_track(self):
        return await self.track(await self.selected_offset())

    async def prev_track(self):
        return await self.next_track(reverse=True)

    async def next_track(self, reverse=False):
        direction = -1 if reverse else 1
        return await self.track(await self.selected_offset() + direction)

    async def select_track_key(self, key):
        for idx, track in enumerate(await self.available_tracks()):
            if track.key == key:
                await self.set_selected_offset(idx + self.start_offset)
                break

    def build_transcode_url(self, track):
        """
        Build a Plex transcode URL that Sonos can play.
        
        Uses Plex's universal transcode endpoint. Requires session ID,
        protocol, and client identifier for Plex to accept the request.
        
        Args:
            track: The track object containing ratingKey
        
        Returns:
            URL string for transcoded audio stream
        """
        from urllib.parse import quote
        import uuid
        
        rating_key = track.ratingKey
        
        # URL-encode the path (slashes become %2F)
        encoded_path = quote(f'/library/metadata/{rating_key}', safe='')
        
        # Generate a unique session ID for this transcode request
        session_id = f"sonoplay-{uuid.uuid4().hex[:8]}"
        
        # Get client identifier from plex_lib if available
        client_id = getattr(self.plex_lib, 'client_identifier', None)
        if not client_id and hasattr(self.plex_lib, 'device') and self.plex_lib.device:
            client_id = getattr(self.plex_lib.device, 'uuid', 'sonoplay-default')
        if not client_id:
            client_id = 'sonoplay-default'
        
        # Build query with required parameters for Plex transcode
        query_parts = [
            f"path={encoded_path}",
            f"session={session_id}",
            "protocol=http",
            "directPlay=0",
            "directStream=0",
            "mediaIndex=0",
            "partIndex=0",
            "fastSeek=1",
            "copyts=1",
            "offset=0",
            "X-Plex-Platform=Chrome",
            f"X-Plex-Client-Identifier={client_id}",
        ]
        query = "&".join(query_parts)
        
        # Build base URL - use .mp3 suffix for Sonos compatibility
        # Sonos requires a recognized audio file extension
        base_path = "/audio/:/transcode/universal/start.mp3"
        
        return self.plex_lib.build_url(f"{base_path}?{query}")

    def url_for_track(self, track, force_transcode=False):
        """
        Get the URL for a track, using transcoding if needed.
        
        Args:
            track: The track object containing media information
            force_transcode: If True, returns a Plex transcode URL
        
        Returns:
            The URL string for playing the track
        """
        if force_transcode:
            logger.info("Using Plex transcode for high-bitrate track: %s", getattr(track, 'title', 'Unknown'))
            return self.build_transcode_url(track)
        
        return self.plex_lib.build_url(track.Media[0].Part[0].key)
    
    def is_track_playable(self, track):
        """
        Check if a track is playable based on bitrate/sample rate thresholds.
        Returns True if playable, False if it should be skipped.
        """
        from settings import settings
        
        # Get configured thresholds
        threshold_kbps = settings.audio_transcode_threshold_kbps
        sample_limit_hz = settings.audio_transcode_max_sample_rate_hz
        
        # If no thresholds configured, allow everything
        if not threshold_kbps and not sample_limit_hz:
            return True
        
        try:
            # Extract media info
            media = track.Media[0] if hasattr(track, 'Media') and track.Media else None
            if not media:
                return True  # No media info, assume playable
            
            # Check bitrate
            bitrate = getattr(media, 'bitrate', None)
            if bitrate and threshold_kbps and bitrate > threshold_kbps:
                return False
            
            # Check sample rate
            sample_rate = getattr(media, 'audioSampleRate', None)
            if sample_rate and sample_limit_hz and sample_rate > sample_limit_hz:
                return False
            
            return True
        except Exception:
            return True  # On error, assume playable

    async def allow_shuffle(self):
        info = await self.get_info()
        if info.get("allowShuffle", None) is None:
            if (await self.total_count()) == UNLIMITED:
                return False
            return True
        return info.allowShuffle

    @property
    def last_offset(self):
        if self.start_offset is None:
            return None
        return self.start_offset + len(self.info.Metadata) - 1

    async def more(self, after=True):
        if self.info is None:
            await self.get_info()
        url = URL(self.plex_lib.build_url(self.container_key))
        url = url.remove_query_params(["center", "includeBefore", "includeAfter"])
        args = {'includeAfter': 0, 'includeBefore': 0}
        if after:
            last_offset = self.last_offset
            total = await self.total_count()
            if last_offset is None or last_offset >= total - 1:
                return False
            args['includeAfter'] = 1
            t = await self.track(self.start_offset + (await self.available_count()) - 1)
            args['center'] = t.playQueueItemID
        else:
            if self.start_offset is None or self.start_offset <= 1:
                return False
            args['includeBefore'] = 1
            t = await self.track(self.start_offset)
            args['center'] = t.playQueueItemID
        url = url.include_query_params(**args)
        async with g.http.get(str(url), headers=self.plex_lib.request_headers(accept_json=True)) as res:
            res.raise_for_status()
            info = DotMap((await res.json())['MediaContainer'])
            new_items = list(getattr(info, 'Metadata', []))
            if after:
                self.info.Metadata += new_items
                logger.debug("queue %s append %d items", self.container_key, len(new_items))
            else:
                self.info.Metadata = new_items + self.info.Metadata
                logger.debug("queue %s prepend %d items", self.container_key, len(new_items))
                self.start_offset = max(0, self.start_offset - len(new_items))
        return len(new_items) > 0

    async def available_tracks(self):
        info = await self.get_info()
        return info.Metadata

    async def available_count(self):
        return len(await self.available_tracks())

    async def total_count(self):
        info = await self.get_info()
        if not info.playQueueTotalCount:
            return UNLIMITED
        return info.playQueueTotalCount

    async def selected_item_id(self):
        info = await self.get_info()
        return info.playQueueSelectedItemID

    async def selected_offset(self):
        info = await self.get_info()
        return info.playQueueSelectedItemOffset

    async def get_track_info(self):
        track = await self.selected_track()
        info = {
            'duration': track.duration,
            'key': track.key,
            'ratingKey': track.ratingKey,
            'containerKey': f"/playQueues/{self.info.playQueueID}",
            'playQueueID': self.info.playQueueID,
            'playQueueVersion': self.info.playQueueVersion,
            'playQueueItemID': track.playQueueItemID,
            # Extended metadata for UI
            'title': getattr(track, 'title', None),
            'artist': getattr(track, 'grandparentTitle', None),
            'album': getattr(track, 'parentTitle', None),
            'thumb': getattr(track, 'thumb', None),
            'art': getattr(track, 'art', None),
            'grandparentThumb': getattr(track, 'grandparentThumb', None),
            'parentThumb': getattr(track, 'parentThumb', None),
        }
        return info

