from pathlib import Path
from subprocess import CompletedProcess

from reforja import installers
from reforja.core import Logger, Runner


class _RecordingRunner(Runner):
    def __init__(self, logger: Logger) -> None:
        super().__init__(logger, dry_run=False)
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):  # type: ignore[override]
        self.calls.append(list(cmd) if not isinstance(cmd, str) else [cmd])
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _runner(tmp_path: Path) -> _RecordingRunner:
    return _RecordingRunner(Logger(tmp_path, "test"))


def test_fetch_json_parses_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        installers,
        "capture",
        lambda cmd, **k: CompletedProcess(cmd, 0, stdout='{"tag_name": "v1"}', stderr=""),
    )
    assert installers.fetch_json("https://x") == {"tag_name": "v1"}


def test_fetch_json_returns_none_on_failure_or_invalid(monkeypatch) -> None:
    monkeypatch.setattr(installers, "capture", lambda cmd, **k: CompletedProcess(cmd, 22, stdout="", stderr="404"))
    assert installers.fetch_json("https://x") is None
    monkeypatch.setattr(installers, "capture", lambda cmd, **k: CompletedProcess(cmd, 0, stdout="nao-json", stderr=""))
    assert installers.fetch_json("https://x") is None


def test_ensure_flatpak_runs_once_per_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(installers, "command_exists", lambda name: True)
    runner = _runner(tmp_path)

    installers.ensure_flatpak(runner)
    installers.ensure_flatpak(runner)

    remote_adds = [cmd for cmd in runner.calls if cmd[:2] == ["flatpak", "remote-add"]]
    assert len(remote_adds) == 1


def test_ensure_flatpak_does_not_memoize_dry_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(installers, "command_exists", lambda name: True)
    logger = Logger(tmp_path, "test")
    dry = Runner(logger, dry_run=True)

    installers.ensure_flatpak(dry)
    assert installers._flathub_ready is False


def test_install_system_or_flatpak_falls_back_to_flatpak(tmp_path: Path, monkeypatch) -> None:
    installed: list[str] = []
    monkeypatch.setattr(installers, "install_system_or_aur", lambda *_a, **_k: False)
    monkeypatch.setattr(installers, "install_flatpak", lambda app_id, _runner: installed.append(app_id))

    installers.install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap", _runner(tmp_path))

    assert installed == ["com.rtosta.zapzap"]


def test_install_system_or_flatpak_prefers_native(tmp_path: Path, monkeypatch) -> None:
    installed: list[str] = []
    monkeypatch.setattr(installers, "install_system_or_aur", lambda *_a, **_k: True)
    monkeypatch.setattr(installers, "install_flatpak", lambda app_id, _runner: installed.append(app_id))

    installers.install_system_or_flatpak("zapzap", "zapzap", "com.rtosta.zapzap", _runner(tmp_path))

    assert installed == []
