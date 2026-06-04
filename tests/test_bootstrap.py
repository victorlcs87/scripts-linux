from pathlib import Path
import subprocess

import pytest

from postformat import bootstrap


def test_ensure_bootstrap_writes_state_when_requirements_are_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())

    ensure_root = tmp_path / "repo"
    ensure_root.mkdir()
    bootstrap.ensure_bootstrap(ensure_root)

    state_path = bootstrap.bootstrap_state_path()
    assert state_path.exists()
    assert '"version": 1' in state_path.read_text(encoding="utf-8")


def test_ensure_bootstrap_installs_missing_requirements_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    installed: list[list[str]] = []

    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr(bootstrap, "install_missing_requirements", lambda requirements, _root: installed.append([item.system_package for item in requirements]))

    ensure_root = tmp_path / "repo"
    ensure_root.mkdir()
    bootstrap.ensure_bootstrap(ensure_root)

    assert installed == [["python-pytest"]]
    assert bootstrap.bootstrap_state_path().exists()


def test_install_missing_requirements_raises_clear_error_when_pacman_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/pacman")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["sudo", "pacman"])),
    )

    with pytest.raises(bootstrap.BootstrapError) as excinfo:
        bootstrap.install_missing_requirements(list(bootstrap.REQUIREMENTS), tmp_path)

    assert "falha ao instalar dependencias internas via pacman" in str(excinfo.value)
