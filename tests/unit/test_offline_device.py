import subprocess
import sys
import textwrap


def test_removal_never_recreates_an_adapter_or_removes_a_replacement():
    # Import the real application in a fresh process because other unit
    # modules replace its dependencies in sys.modules during collection.
    script = """
        import asyncio
        from unittest.mock import AsyncMock, patch

        import plex
        from dlna.dlna_device import DlnaDevice, devices
        from plex.adapters import adapters
        from plex.subscribe import sub_man
        from settings import settings

        async def main():
            old = DlnaDevice("http://renderer.example/description")
            replacement = DlnaDevice(old.location_url)
            old.uuid = replacement.uuid = "test-renderer"
            devices.append(old)

            with (
                patch("plex.adapters.PlexDlnaAdapter") as factory,
                patch.object(type(settings), "mark_device_status") as mark_status,
                patch.object(sub_man, "notify_device_disconnected", new_callable=AsyncMock) as disconnect,
            ):
                await old.remove_self()
                devices.append(replacement)
                await old.remove_self()

                assert old.uuid not in adapters
                assert devices[0] is replacement
                factory.assert_not_called()
                disconnect.assert_awaited_once_with(old)
                mark_status.assert_called_once_with(old.uuid, "offline")

        asyncio.run(main())
    """
    subprocess.run([sys.executable, "-c", textwrap.dedent(script)], check=True)
