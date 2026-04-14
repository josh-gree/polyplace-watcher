import json
import logging
from pathlib import Path

from polyplace_watcher.observability import configure_logging, reset_logging


def test_configure_logging_writes_all_json_logs_to_stdout_and_file(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    log_file = tmp_path / "logs" / "polyplace-watcher.log"

    reset_logging()
    try:
        configure_logging()
        logging.getLogger("polyplace_watcher.test").info(
            "info_event",
            extra={
                "component": "test",
                "answer": 42,
                "config": {
                    "WEB3_HTTP_URL": "https://user:pass@rpc.example.com/v2/token?api_key=abc",
                    "GRID_ADDRESS": "0x" + "12" * 20,
                    "SNAPSHOT_PATH": tmp_path / "snapshot.json",
                },
            },
        )
        logging.getLogger("polyplace_watcher.test").debug("debug_event")

        stdout_lines = capsys.readouterr().out.strip().splitlines()
        file_lines = log_file.read_text().strip().splitlines()
    finally:
        reset_logging()

    assert len(stdout_lines) == 2
    assert len(file_lines) == 2

    stdout_record = json.loads(stdout_lines[0])
    file_record = json.loads(file_lines[0])

    for record in (stdout_record, file_record):
        assert record["message"] == "info_event"
        assert record["level"] == "INFO"
        assert record["logger"] == "polyplace_watcher.test"
        assert record["component"] == "test"
        assert record["answer"] == 42
        assert record["config"] == {
            "WEB3_HTTP_URL": "https://user:pass@rpc.example.com/v2/token?api_key=abc",
            "GRID_ADDRESS": "0x" + "12" * 20,
            "SNAPSHOT_PATH": str(tmp_path / "snapshot.json"),
        }
        assert "timestamp" in record

    for line in (stdout_lines[1], file_lines[1]):
        record = json.loads(line)
        assert record["message"] == "debug_event"
        assert record["level"] == "DEBUG"
