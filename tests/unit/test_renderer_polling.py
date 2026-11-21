import subprocess
import sys
import textwrap


def test_track_switch_settles_when_renderer_continues_playing():
    # Run the real polling code in a fresh process to avoid dependencies
    # replaced by other tests during collection.
    script = """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        import plex
        from dotmap import DotMap
        from plex.adapters import DlnaState, PlexDlnaAdapter

        async def main():
            old_uri = "http://pms.example/old"
            new_uri = "http://pms.example/new"
            renderer = SimpleNamespace(name="renderer", GetPositionInfo=AsyncMock(), GetTransportInfo=AsyncMock())
            adapter = object.__new__(PlexDlnaAdapter)
            adapter.dlna = renderer
            adapter.loop = asyncio.get_running_loop()
            adapter._operation_sequence = 0
            adapter._check_post_operation_false_stop = lambda change: True
            with patch.object(DlnaState, "start_looping"):
                adapter.state = DlnaState(adapter, adapter.state_changed_callback)
            adapter._start_transport_operation(new_uri)
            adapter.state.state = "TRANSITIONING"

            renderer.GetTransportInfo.return_value = DotMap(CurrentTransportState="PLAYING")
            renderer.GetPositionInfo.return_value = DotMap(TrackURI=old_uri, RelTime="00:00:01", TrackDuration="00:03:00")
            await adapter.state.check(None, check_count=10)
            assert not adapter._active_operation_state_ready

            renderer.GetPositionInfo.return_value.TrackURI = new_uri
            await adapter.state.check(None, check_count=11)
            assert adapter._active_operation_state_confirmed
            assert adapter._active_operation_event.is_set()

        asyncio.run(main())
    """
    subprocess.run([sys.executable, "-c", textwrap.dedent(script)], check=True)
