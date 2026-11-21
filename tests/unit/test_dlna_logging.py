import subprocess
import sys
import textwrap


def test_discovery_cache_expires_and_evicts_old_entries():
    script = """
        import asyncio
        import importlib
        from unittest.mock import AsyncMock, patch

        server = importlib.import_module("plex.plexserver")

        async def main():
            with (
                patch.object(server, "_DISCOVERY_ERROR_LIMIT", 2),
                patch.object(server, "_DISCOVERY_ERROR_TTL", 5),
                patch.object(server.time, "monotonic", return_value=10) as clock,
                patch.object(server.DlnaDevice, "get_data", new_callable=AsyncMock) as get_data,
                patch.object(server.logger, "log") as log,
            ):
                get_data.side_effect = OSError("test renderer unavailable")
                first = "http://renderer.example/first"
                await server.on_new_dlna_device(first)
                await server.on_new_dlna_device(first)
                assert log.call_count == 1

                clock.return_value = 16
                await server.on_new_dlna_device(first)
                assert log.call_count == 2

                await server.on_new_dlna_device("http://renderer.example/second")
                await server.on_new_dlna_device("http://renderer.example/third")
                assert len(server._discovery_errors) == 2
                assert first not in server._discovery_errors

        asyncio.run(main())
    """
    subprocess.run([sys.executable, "-c", textwrap.dedent(script)], check=True)
