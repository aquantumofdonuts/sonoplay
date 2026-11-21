import subprocess
import sys
import textwrap


def test_debug_output_redacts_plex_tokens_in_urls_mappings_and_tracebacks():
    script = """
        import io
        import logging
        import sys

        sys.path.insert(0, "src")

        from sonoplay.core.logging import apply_log_level, configure_logging

        configure_logging("DEBUG")
        access = logging.getLogger("uvicorn.access")
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(logging.Formatter("access: %(message)s"))
        access.addHandler(handler)
        access.setLevel(logging.INFO)
        apply_log_level(" debug ")

        logging.debug("queue http://plex.example/queue?X-Plex-Token=%s&other=1", "queue-token")
        logging.debug("params: %s", {"X-Plex-Token": "mapping-token"})
        try:
            raise ValueError("http://plex.example/track?x-plex-token=exception-token")
        except ValueError:
            logging.exception("stream error")
        access.info("GET /player?X-Plex-Token=%s", "access-token")
        access_output = output.getvalue()
        assert "access: GET /player?X-Plex-Token=[redacted]" in access_output
        print(access_output, end="")
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)], check=True, capture_output=True, text=True,
    )
    for token in ("queue-token", "mapping-token", "exception-token", "access-token"):
        assert token not in result.stdout
    assert "X-Plex-Token=[redacted]&other=1" in result.stdout
    assert "{'X-Plex-Token': '[redacted]'}" in result.stdout
    assert "x-plex-token=[redacted]" in result.stdout
