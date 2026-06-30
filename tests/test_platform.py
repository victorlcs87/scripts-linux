from pathlib import Path

import pytest

from reforja import platform
from reforja.core import Logger, Runner


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


def test_detect_distro_recognizes_fedora(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "_detect_immutable", lambda _id: False)
    path = write_os_release(tmp_path, 'ID=fedora\nPRETTY_NAME="Fedora Linux"\n')

    distro = platform.detect_distro(path)

    assert distro.family == "fedora"
    assert distro.is_fedora
    assert not distro.immutable


def test_detect_distro_recognizes_bazzite_as_immutable_fedora(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "_detect_immutable", lambda _id: True)
    path = write_os_release(tmp_path, 'ID=bazzite\nID_LIKE=fedora\nPRETTY_NAME="Bazzite"\n')

    distro = platform.detect_distro(path)

    assert distro.family == "fedora"
    assert distro.immutable


def test_detect_distro_recognizes_steamos_as_immutable_arch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "_detect_immutable", lambda _id: True)
    path = write_os_release(tmp_path, 'ID=steamos\nID_LIKE=arch\nPRETTY_NAME="SteamOS"\n')

    distro = platform.detect_distro(path)

    assert distro.family == "arch"
    assert distro.immutable


def test_detect_distro_rejects_unknown_distribution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(platform, "_detect_immutable", lambda _id: False)
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


def test_install_system_package_uses_dnf_on_fedora(tmp_path: Path, monkeypatch) -> None:
    commands: list[tuple[list[str], bool]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("fedora", (), "fedora"))
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)
    monkeypatch.setattr(runner, "run", lambda cmd, **kwargs: commands.append((list(cmd), bool(kwargs.get("sudo")))))

    platform.install_system_package("git", runner)

    assert commands == [(["dnf", "install", "-y", "git"], True)]


def test_install_system_package_skips_native_on_immutable(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(
        platform, "current_distro", lambda: platform.Distro("bazzite", ("fedora",), "fedora", immutable=True)
    )
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(list(cmd)))

    platform.install_system_package("sunshine", runner)

    assert commands == []
    assert "imutavel" in logger.path.read_text(encoding="utf-8")


def test_install_system_or_aur_returns_false_on_immutable(tmp_path: Path, monkeypatch) -> None:
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(
        platform, "current_distro", lambda: platform.Distro("steamos", ("arch",), "arch", immutable=True)
    )
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)

    assert platform.install_system_or_aur("heroic", "heroic-bin", runner) is False


def test_update_system_uses_rpm_ostree_on_immutable_fedora(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(
        platform, "current_distro", lambda: platform.Distro("bazzite", ("fedora",), "fedora", immutable=True)
    )
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(list(cmd)))

    platform.update_system(runner)

    assert commands == [["rpm-ostree", "upgrade"]]


def test_update_system_uses_dnf_on_mutable_fedora(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("fedora", (), "fedora"))
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(list(cmd)))

    platform.update_system(runner)

    assert commands == [["dnf", "upgrade", "--refresh", "-y"]]


def test_update_system_steamos_immutable_is_manual_only(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(
        platform, "current_distro", lambda: platform.Distro("steamos", ("arch",), "arch", immutable=True)
    )
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(list(cmd)))

    platform.update_system(runner)

    assert commands == []
    assert "steamos-update" in logger.path.read_text(encoding="utf-8")


def test_ensure_rpmfusion_installs_repos_on_mutable_fedora(tmp_path: Path, monkeypatch) -> None:
    commands: list[str] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(platform, "current_distro", lambda: platform.Distro("fedora", (), "fedora"))
    monkeypatch.setattr(platform, "system_installed", lambda pkg: False)
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(cmd))

    platform.ensure_rpmfusion(runner)

    assert len(commands) == 1
    assert "rpmfusion-free-release" in commands[0]
    assert "rpmfusion-nonfree-release" in commands[0]


def test_ensure_rpmfusion_is_noop_on_immutable(tmp_path: Path, monkeypatch) -> None:
    commands: list[str] = []
    logger = Logger(tmp_path, "test")
    runner = Runner(logger)

    monkeypatch.setattr(
        platform, "current_distro", lambda: platform.Distro("bazzite", ("fedora",), "fedora", immutable=True)
    )
    monkeypatch.setattr(runner, "run", lambda cmd, **_kwargs: commands.append(cmd))

    platform.ensure_rpmfusion(runner)

    assert commands == []
