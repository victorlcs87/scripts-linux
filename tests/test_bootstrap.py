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
    monkeypatch.setattr(bootstrap, "install_missing_requirements", lambda requirements, _root: installed.append([item.module_name for item in requirements]))

    ensure_root = tmp_path / "repo"
    ensure_root.mkdir()
    bootstrap.ensure_bootstrap(ensure_root)

    assert installed == [["InquirerPy", "pytest"]]
    assert bootstrap.bootstrap_state_path().exists()


def test_install_missing_requirements_raises_clear_error_when_pip_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_install_with_system_package", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bootstrap, "_install_with_aur", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bootstrap,
        "_install_with_pip",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ["python", "-m", "pip"])),
    )

    with pytest.raises(bootstrap.BootstrapError) as excinfo:
        bootstrap.install_missing_requirements(list(bootstrap.REQUIREMENTS), tmp_path)

    assert "falha ao instalar dependencias internas" in str(excinfo.value)


def test_install_with_aur_uses_aur_package_when_module_is_missing(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_find_spec(name: str):
        if name == "InquirerPy":
            return None
        return object()

    monkeypatch.setattr("importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr(bootstrap, "detect_distro", lambda: type("Distro", (), {"is_arch": True, "family": "arch"})())
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paru" if name == "paru" else None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kwargs: commands.append(cmd))

    bootstrap._install_with_aur([bootstrap.REQUIREMENTS[0]], tmp_path)

    assert commands[0][:4] == ["paru", "-S", "--needed", "--noconfirm"]
    assert "python-inquirerpy" in commands[0]


def test_install_with_pip_bootstraps_ensurepip_when_pip_is_missing(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_find_spec(name: str):
        if name in {"pip", "InquirerPy"}:
            return None
        return object()

    monkeypatch.setattr("importlib.util.find_spec", fake_find_spec)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kwargs: commands.append(cmd))

    bootstrap._install_with_pip([bootstrap.REQUIREMENTS[0]], tmp_path)

    assert commands[0][1:3] == ["-m", "ensurepip"]
    assert commands[1][1:4] == ["-m", "pip", "install"]
