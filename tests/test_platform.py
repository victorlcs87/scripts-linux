from pathlib import Path

import pytest

from postformat.core import Logger, Runner
from postformat import platform


def write_os_release(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(text, encoding="utf-8")
    return path


def test_detect_distro_recognizes_cachyos_as_arch(tmp_path: Path) -> None:
    path = write_os_release(tmp_path, 'ID=cachyos\nID_LIKE=arch\nPRETTY_NAME="CachyOS"\n')

    distro = platform.detect_distro(path)

    assert distro.family == "arch"
    assert distro.is_arch


def test_detect_distro_recognizes_ubuntu_as_debian(tmp_path: Path) -> None:
    path = write_os_release(tmp_path, 'ID=ubuntu\nID_LIKE="debian"\nPRETTY_NAME="Ubuntu"\n')

    distro = platform.detect_distro(path)

    assert distro.family == "debian"
    assert distro.is_debian


def test_detect_distro_rejects_unknown_distribution(tmp_path: Path) -> None:
    path = write_os_release(tmp_path, 'ID=void\nPRETTY_NAME="Void Linux"\n')

    with pytest.raises(platform.UnsupportedDistroError) as excinfo:
        platform.detect_distro(path)

    assert "distribuicao nao suportada" in str(excinfo.value)


def test_install_system_package_uses_pacman_on_arch(tmp_path: Path, monkeypatch) -> None:
    commands: list[tuple[list[str], bool]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("cachyos", ("arch",), "arch"))
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)
    monkeypatch.setattr(runner, "run", lambda cmd, **kwargs: commands.append((list(cmd), bool(kwargs.get("sudo")))))

    platform.install_system_package("git", runner)

    assert commands == [(["pacman", "-S", "--needed", "git"], True)]


def test_install_system_package_uses_apt_update_before_install_on_debian(tmp_path: Path, monkeypatch) -> None:
    commands: list[tuple[list[str], bool]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("ubuntu", ("debian",), "debian"))
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)
    monkeypatch.setattr(runner, "run", lambda cmd, **kwargs: commands.append((list(cmd), bool(kwargs.get("sudo")))))

    platform.install_system_package("git", runner)

    assert commands == [
        (["apt-get", "update"], True),
        (["apt-get", "install", "-y", "git"], True),
    ]


def test_update_system_uses_apt_update_and_upgrade_on_debian(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("debian", (), "debian"))
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(list(cmd)))

    platform.update_system(runner)

    assert commands == [["apt-get", "update"], ["apt-get", "upgrade", "-y"]]
