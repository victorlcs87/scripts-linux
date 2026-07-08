import subprocess
from pathlib import Path

import pytest

from reforja import bootstrap


def test_ensure_bootstrap_does_nothing_when_requirements_are_present(tmp_path: Path, monkeypatch) -> None:
    installed: list[list[str]] = []
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    monkeypatch.setattr(
        bootstrap,
        "install_missing_requirements",
        lambda requirements, _root: installed.append([item.module_name for item in requirements]),
    )

    ensure_root = tmp_path / "repo"
    ensure_root.mkdir()
    bootstrap.ensure_bootstrap(ensure_root)

    assert installed == []


def test_ensure_bootstrap_installs_missing_requirements_once(tmp_path: Path, monkeypatch) -> None:
    installed: list[list[str]] = []

    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)
    monkeypatch.setattr(
        bootstrap,
        "install_missing_requirements",
        lambda requirements, _root: installed.append([item.module_name for item in requirements]),
    )

    ensure_root = tmp_path / "repo"
    ensure_root.mkdir()
    bootstrap.ensure_bootstrap(ensure_root)

    assert installed == [["InquirerPy"]]


def test_requirements_do_not_impose_dev_dependencies() -> None:
    # pytest/ruff sao deps de desenvolvimento (pip install -e .[dev]); o
    # bootstrap de runtime nao deve instala-las para o usuario final.
    names = {req.module_name for req in bootstrap.REQUIREMENTS}
    assert "pytest" not in names
    assert names == {"InquirerPy"}


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


def test_install_with_system_package_uses_dnf_on_fedora(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        bootstrap,
        "detect_distro",
        lambda: type("Distro", (), {"is_arch": False, "is_fedora": True, "family": "fedora", "immutable": False})(),
    )
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kwargs: commands.append(cmd))

    requirement = bootstrap.BootstrapRequirement(
        "exemplo", "python-exemplo", "python3-exemplo", None, "exemplo", fedora_package="python3-exemplo"
    )
    bootstrap._install_with_system_package([requirement], tmp_path)

    assert commands[-1][:4] == ["sudo", "dnf", "install", "-y"]
    assert "python3-exemplo" in commands[-1]


def test_install_with_system_package_skips_native_on_immutable(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        bootstrap,
        "detect_distro",
        lambda: type("Distro", (), {"is_arch": False, "is_fedora": True, "family": "fedora", "immutable": True})(),
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_kwargs: commands.append(cmd))

    bootstrap._install_with_system_package(list(bootstrap.REQUIREMENTS), tmp_path)

    assert commands == []


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
