from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


class Color:
    RESET = "\033[0m"
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"


@dataclass(frozen=True)
class UserInfo:
    name: str
    home: Path
    uid: int
    gid: int


def detect_user() -> UserInfo:
    name = os.environ.get("SUDO_USER") or os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
    entry = pwd.getpwnam(name)
    home = Path(entry.pw_dir)
    if not home.exists():
        raise RuntimeError(f"nao consegui detectar a home do usuario {name}")
    return UserInfo(name=name, home=home, uid=entry.pw_uid, gid=entry.pw_gid)


class Logger:
    def __init__(self, run_dir: Path, name: str) -> None:
        self.log_dir = run_dir / "LOGS"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.log"

    def write(self, message: str = "") -> None:
        print(message)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(strip_ansi(message) + "\n")


def strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class Runner:
    def __init__(self, logger: Logger, dry_run: bool = False) -> None:
        self.logger = logger
        self.dry_run = dry_run

    def cmd_text(self, cmd: Sequence[str] | str, sudo: bool = False) -> str:
        if isinstance(cmd, str):
            text = cmd
        else:
            text = " ".join(quote_arg(part) for part in cmd)
        return f"sudo {text}" if sudo else text

    def run(
        self,
        cmd: Sequence[str] | str,
        *,
        sudo: bool = False,
        check: bool = True,
        cwd: Path | None = None,
        shell: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        printable = self.cmd_text(cmd, sudo=sudo)
        if self.dry_run:
            self.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} {printable}")
            return None
        full_cmd: Sequence[str] | str
        if sudo:
            if isinstance(cmd, str):
                full_cmd = f"sudo {cmd}"
                shell = True
            else:
                full_cmd = ["sudo", *cmd]
        else:
            full_cmd = cmd
        self.logger.write(f"{Color.CYAN}$ {printable}{Color.RESET}")
        result = subprocess.run(
            full_cmd,
            cwd=str(cwd) if cwd else None,
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if result.stdout:
            for line in result.stdout.rstrip().splitlines():
                self.logger.write(line)
        if check and result.returncode != 0:
            raise RuntimeError(f"comando falhou ({result.returncode}): {printable}")
        return result


def quote_arg(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "@%_+=:,./-" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def is_root() -> bool:
    return os.geteuid() == 0


def backup_path(path: Path, suffix: str = "backup-pos-formatacao") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.{suffix}-{stamp}")


def backup_existing(path: Path, runner: Runner, *, sudo: bool = False) -> Path | None:
    if not path.exists():
        return None
    target = backup_path(path)
    if sudo:
        runner.run(["cp", "-a", str(path), str(target)], sudo=True)
    else:
        if runner.dry_run:
            runner.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} cp -a {path} {target}")
        else:
            shutil.copy2(path, target)
            runner.logger.write(f"{Color.GREEN}OK:{Color.RESET} Backup criado: {target}")
    return target


def write_text(path: Path, content: str, runner: Runner, *, mode: int = 0o644) -> None:
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            current = None
        if current == content and (path.stat().st_mode & 0o777) == mode:
            runner.logger.write(f"{Color.GREEN}OK:{Color.RESET} {path} ja esta atualizado")
            return
    if runner.dry_run:
        runner.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} escreveria {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def write_text_sudo(path: Path, content: str, runner: Runner, *, mode: int = 0o644) -> None:
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except (PermissionError, UnicodeDecodeError):
            current = None
        if current == content and (path.stat().st_mode & 0o777) == mode:
            runner.logger.write(f"{Color.GREEN}OK:{Color.RESET} {path} ja esta atualizado")
            return
    if runner.dry_run:
        runner.logger.write(f"{Color.YELLOW}[dry-run]{Color.RESET} escreveria {path} com sudo")
        return
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        tmp_name = handle.name
    try:
        runner.run(["install", "-m", f"{mode:04o}", tmp_name, str(path)], sudo=True)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def ensure_owner(path: Path, user: UserInfo, runner: Runner, *, recursive: bool = False) -> None:
    flag = "-R" if recursive else ""
    cmd = ["chown"]
    if flag:
        cmd.append(flag)
    cmd.extend([f"{user.uid}:{user.gid}", str(path)])
    runner.run(cmd, sudo=True, check=False)


def confirm_phrase(phrase: str) -> bool:
    typed = input(f"Digite {phrase} para continuar: ").strip()
    return typed == phrase


def print_lines(logger: Logger, lines: Iterable[str]) -> None:
    for line in lines:
        logger.write(line)
