import asyncio
import logging
from html import escape as html_escape

logger = logging.getLogger(__name__)

from plex.adapters import adapter_by_device
from utils import subscriber_send_headers, pms_header, g
from settings import settings
from dlna import devices, get_device_by_uuid, list_virtual_devices
from datetime import datetime, timedelta, timezone

TIMELINE_STOPPED = '<MediaContainer commandID="{command_id}">' \
                   '<Timeline type="music" state="stopped"/>' \
                   '<Timeline type="video" state="stopped"/>' \
                   '<Timeline type="photo" state="stopped"/>' \
                   '</MediaContainer>'


TIMELINE_DISCONNECTED = '<MediaContainer commandID="{command_id}" disconnected="1">' \
                        '<Timeline type="music" state="stopped"/>' \
                        '<Timeline type="video" state="stopped"/>' \
                        '<Timeline type="photo" state="stopped"/>' \
                        '</MediaContainer>'


CONTROLLABLE = 'playPause,stop,volume,shuffle,repeat,seekTo,skipPrevious,skipNext,stepBack,stepForward'

TIMELINE_PLAYING = '<MediaContainer commandID="{command_id}"><Timeline controllable="' + CONTROLLABLE + '" ' \
                   'type="music" {parameters}/><Timeline type="video" state="stopped"/><Timeline type="photo" ' \
                   'state="stopped"/></MediaContainer> '


class SubscribeManager(object):
    subscribers = {}
    running = True
    last_server_notify_state = {}

    @property
    def subscriber_ttl_seconds(self):
        """Get TTL from settings for testability."""
        return settings.subscriber_ttl_seconds

    def cleanup_stale_subscribers(self) -> int:
        """Remove subscribers that haven't been seen within TTL.
        
        Returns:
            Number of subscribers removed.
        """
        now = datetime.now(timezone.utc)
        ttl = timedelta(seconds=self.subscriber_ttl_seconds)
        removed_count = 0
        
        for target_uuid in list(self.subscribers.keys()):
            subs = self.subscribers.get(target_uuid, [])
            stale = []
            for sub in subs:
                last_seen = getattr(sub, 'last_seen', None)
                if last_seen is None or (now - last_seen) > ttl:
                    stale.append(sub)
            
            for sub in stale:
                subs.remove(sub)
                removed_count += 1
            
            # Clean up empty lists
            if len(subs) == 0:
                del self.subscribers[target_uuid]
        
        return removed_count

    async def start_cleanup_task(self) -> None:
        """Start background task to periodically clean stale subscribers."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def stop_cleanup_task(self) -> None:
        """Stop the background cleanup task."""
        if hasattr(self, '_cleanup_task') and self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def _cleanup_loop(self) -> None:
        """Background loop that periodically cleans stale subscribers."""
        while self.running:
            await asyncio.sleep(60)  # Run every minute
            removed = self.cleanup_stale_subscribers()
            if removed > 0:
                logger.info("Cleaned up %d stale subscriber(s)", removed)

    def get_subscriber(self, target_uuid: str, client_uuid: str):
        s = [s for s in self.subscribers.get(target_uuid, []) if s.uuid == client_uuid]
        if len(s) > 0:
            return s[0]
        return None

    def update_command_id(self, target_uuid: str, client_uuid: str, command_id: int):
        s = self.get_subscriber(target_uuid, client_uuid)
        if s is not None:
            s.command_id = command_id

    async def add_subscriber(self,
                       target_uuid: str,
                       client_uuid: str,
                       host: str,
                       port: int,
                       protocol: str = "http",
                       command_id: int = 0):
        logger.info("add sub %s to %s", client_uuid, target_uuid)
        s = self.get_subscriber(target_uuid, client_uuid)
        if s is not None:
            if s.host != host or s.port != port or s.protocol != protocol:
                await self.remove_subscriber(s.uuid)
            else:
                s.command_id = command_id
                return
        l = self.subscribers.get(target_uuid, [])
        l.append(Subscriber(client_uuid, host, port, self, protocol, command_id))
        self.subscribers[target_uuid] = l

    async def remove_subscriber(self, uuid, target_uuid: str = None):
        logger.info("remove sub %s from %s", uuid, target_uuid)
        for tu in [target_uuid] if target_uuid is not None else self.subscribers.keys():
            l = self.subscribers.get(tu, [])
            remove = None
            for s in l:
                if s.uuid == uuid:
                    remove = s
                    break
            if remove in l:
                l.remove(remove)
            if len(l) == 0:
                device = await get_device_by_uuid(tu)
                if device is not None and len(self.subscribers.get(tu, [])) == 0 and hasattr(device, "stop_subscribe"):
                    device.stop_subscribe()

    def stop(self):
        self.running = False

    async def notify_server(self):
        target_devices = list(devices)
        target_devices.extend(list_virtual_devices())
        await asyncio.gather(
            *[self.notify_server_device(device) for device in target_devices],
            return_exceptions=True,
        )

    async def notify_server_device(self, device, force=False):
        subs = self.subscribers.get(device.uuid, [])
        if len(subs) == 0 and not force:
            return
        adapter = await adapter_by_device(device)
        if adapter.plex_lib is None or adapter.queue is None:
            return
        if adapter.no_notice and not force:
            logger.debug("ignore sub notice for server")
            return
        if adapter.plex_state is None:
            return
        if self.last_server_notify_state.get(device.uuid, "") == adapter.plex_state == "stopped" and not force:
            return
        self.last_server_notify_state[device.uuid] = adapter.plex_state
        params = await adapter.get_pms_state()
        if not params or params.get('state', None) is None:
            return
        params.update(pms_header(device))
        timeline_url = adapter.plex_lib.get_timeline()
        try:
            async with g.http.get(timeline_url, params=params) as res:
                try:
                    res.raise_for_status()
                except Exception as e:
                    body = await res.text()
                    logger.error("notify server error: %s, %s, %s", e, body, params)
        except asyncio.TimeoutError:
            logger.debug("notify server timeout for %s", device.name)
        except Exception as e:
            logger.warning("notify server failed for %s: %s", device.name, e)

    async def notify(self):
        await self.notify_server()
        target_devices = list(devices)
        target_devices.extend(list_virtual_devices())
        tasks = [self.notify_device(device) for device in target_devices]
        await asyncio.gather(*tasks)

    async def msg_for_device(self, device):
        adapter = await adapter_by_device(device)
        if adapter.no_notice:
            return None
        if adapter.state.state is None or adapter.state.state == "STOPPED" or adapter.queue is None:
            return TIMELINE_STOPPED
        state = await adapter.get_state()
        if not state or state.get('state', None) is None:
            return TIMELINE_STOPPED
        state['itemType'] = 'music'
        # Filter out None values before building XML
        filtered_state = {k: v for k, v in state.items() if v is not None}
        # Escape XML special characters in string values
        escaped_state = {k: html_escape(str(v)) if isinstance(v, str) else v for k, v in filtered_state.items()}
        xml = TIMELINE_PLAYING.format(parameters=" ".join([f'{k}="{v}"' for k, v in escaped_state.items()]),
                                      command_id="{command_id}")
        return xml

    async def notify_device(self, device):
        subs = self.subscribers.get(device.uuid, [])
        adapter = await adapter_by_device(device)
        if adapter.no_notice:
            logger.debug("ignore sub notice for %s", adapter.dlna.name)
            return
        msg = await self.msg_for_device(device)
        if msg is None:
            return
        await asyncio.gather(*[sub.send(msg, device) for sub in subs])

    async def notify_device_disconnected(self, device):
        subs = self.subscribers.get(device.uuid, [])
        await asyncio.gather(*[sub.send(TIMELINE_DISCONNECTED, device) for sub in subs])
        # Remove subscribers in background — await the gather wrapped in a coroutine
        # (asyncio.gather returns a Future, which create_task rejects)
        async def _cleanup():
            await asyncio.gather(
                *[self.remove_subscriber(sub.uuid, target_uuid=device.uuid) for sub in subs],
                return_exceptions=True,
            )
        task = asyncio.create_task(_cleanup())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() and t.exception() else None)

    async def start(self):
        await self.notify()
        while self.running:
            await asyncio.sleep(settings.plex_notify_interval)
            wait_timeout = settings.plex_notify_interval * 10
            try:
                target_devices = []
                none_uuids = []
                for u, l in self.subscribers.items():
                    if len(l) > 0:
                        d = await get_device_by_uuid(u)
                        if d is not None:
                            target_devices.append(d)
                        else:
                            none_uuids.append(u)
                for u in none_uuids:
                    if u in self.subscribers:
                        del self.subscribers[u]
                if len(target_devices) == 0:
                    continue
                wait_tasks = []
                for device in target_devices:
                    adapter = await adapter_by_device(device)
                    wait_tasks.append(asyncio.create_task(adapter.wait_for_event(wait_timeout)))
                done, pending = await asyncio.wait(wait_tasks,
                                                   timeout=wait_timeout,
                                                   return_when=asyncio.FIRST_COMPLETED)
                # Cancel leftover waiters so they don't pile up in each
                # adapter's wait_state_change_events between iterations.
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.exceptions.TimeoutError:
                pass
            try:
                await self.notify()
            except Exception as e:
                logger.error("subscribe notify error: %s", e)


class Subscriber(object):

    MAX_SEND_ERRORS = 3

    def __init__(self, uuid, host, port, manager: SubscribeManager, protocol: str = "http", command_id: int = 0):
        self.uuid = uuid
        self.host = host
        self.port = port
        self.protocol = protocol
        self.command_id = command_id
        self.url = f"{protocol}://{host}:{port}/:/timeline"
        self.manager = manager
        self.last_seen = datetime.now(timezone.utc)
        self.consecutive_errors = 0

    async def send(self, msg: str, device):
        msg = msg.format(command_id=self.command_id)
        response = None
        try:
            async with g.http.post(self.url, data=msg, headers=subscriber_send_headers(device),
                                   timeout=1) as response:
                response.raise_for_status()
                self.consecutive_errors = 0
        except Exception as e:
            self.consecutive_errors += 1
            resp_text = 'None'
            if response is not None:
                try:
                    resp_text = await response.text()
                except Exception:
                    resp_text = '<unreadable>'
            if self.consecutive_errors >= self.MAX_SEND_ERRORS:
                logger.warning("subscriber %s evicted after %d consecutive errors: %s, %s",
                               self, self.consecutive_errors, e, resp_text)
                await self.manager.remove_subscriber(self.uuid)
            else:
                logger.debug("subscriber send error %s (attempt %d/%d): %s",
                             self, self.consecutive_errors, self.MAX_SEND_ERRORS, e)

    def __eq__(self, other):
        return self.uuid == other.uuid

    def __repr__(self):
        return f"{self.host}:{self.port}"


sub_man = SubscribeManager()

