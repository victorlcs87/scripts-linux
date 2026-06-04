from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence


ANSI_ENABLED = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None and os.environ.get("TERM", "dumb") != "dumb"
SPINNER_FRAMES = ("|", "/", "-", "\\")


class Color:
    RESET = "\033[0m" if ANSI_ENABLED else ""
    BOLD = "\033[1m" if ANSI_ENABLED else ""
    DIM = "\033[2m" if ANSI_ENABLED else ""
    TITLE = "\033[1;38;5;213m" if ANSI_ENABLED else ""
    ACCENT = "\033[1;38;5;45m" if ANSI_ENABLED else ""
    INFO = "\033[1;38;5;81m" if ANSI_ENABLED else ""
    SUCCESS = "\033[1;38;5;48m" if ANSI_ENABLED else ""
    WARNING = "\033[1;38;5;226m" if ANSI_ENABLED else ""
    ERROR = "\033[1;38;5;196m" if ANSI_ENABLED else ""
    MUTED = "\033[38;5;245m" if ANSI_ENABLED else ""
    COMMAND = "\033[1;38;5;51m" if ANSI_ENABLED else ""
    DRY_RUN = "\033[1;38;5;214m" if ANSI_ENABLED else ""
    BOX = "\033[38;5;39m" if ANSI_ENABLED else ""
    CHOICE = "\033[1;38;5;118m" if ANSI_ENABLED else ""
    WAITING = "\033[1;38;5;220m" if ANSI_ENABLED else ""
    RED = ERROR
    GREEN = SUCCESS
    YELLOW = WARNING
    BLUE = ACCENT
    CYAN = INFO


@dataclass(frozen=True)
class UserInfo:
    name: str
    home: Path
    uid: int
    gid: int


class PrivilegeEscalationBlockedError(RuntimeError):
    pass


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
        self._tty = sys.stdout.isatty()
        self._transient_active = False

    def write(self, message: str = "") -> None:
        self.clear_transient()
        print(message)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(strip_ansi(message) + "\n")

    def transient(self, message: str) -> None:
        if not self._tty:
            return
        sys.stdout.write("\r\033[2K" + message)
        sys.stdout.flush()
        self._transient_active = True

    def clear_transient(self) -> None:
        if not self._tty or not self._transient_active:
            return
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()
        self._transient_active = False


def strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def paint(text: str, tone: str) -> str:
    if not tone:
        return text
    return f"{tone}{text}{Color.RESET}"


def divider(width: int = 72, *, tone: str | None = None, char: str = "=") -> str:
    return paint(char * width, tone or Color.BOX)


def badge(label: str, tone: str) -> str:
    return paint(f"[{label}]", tone)


def announce(logger: Logger, kind: str, message: str) -> None:
    tones = {
        "action": Color.ACCENT,
        "waiting": Color.WAITING,
        "done": Color.SUCCESS,
        "failed": Color.ERROR,
        "skipped": Color.WARNING,
        "info": Color.INFO,
    }
    logger.write(f"{badge(kind, tones.get(kind, Color.INFO))} {message}")


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}m{remainder:02d}s"


def prompt_user(
    prompt: str,
    logger: Logger,
    *,
    detail: str | None = None,
    prompt_label: str = "Resposta",
    allow_empty: bool = True,
) -> str:
    logger.write(divider(char="~", tone=Color.ACCENT))
    announce(logger, "waiting", prompt)
    if detail:
        logger.write(paint(detail, Color.MUTED))
    answer = input(f"{paint(prompt_label + ':', Color.CHOICE)} ").strip()
    if answer or allow_empty:
        return answer
    announce(logger, "waiting", "Entrada vazia. Tente novamente.")
    return prompt_user(prompt, logger, detail=detail, prompt_label=prompt_label, allow_empty=allow_empty)


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
        action: str | None = None,
        show_progress: bool = True,
        quiet_success: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        printable = self.cmd_text(cmd, sudo=sudo)
        action = action or infer_action(cmd, sudo=sudo)
        if self.dry_run:
            announce(self.logger, "action", action)
            self.logger.write(f"{badge('dry-run', Color.DRY_RUN)} {printable}")
            return None
        if sudo and no_new_privs_enabled():
            raise PrivilegeEscalationBlockedError(
                "este ambiente bloqueia sudo porque NoNewPrivs=1. "
                "Execute o sisteminha em uma sessao normal do seu sistema, fora de contêiner, sandbox ou terminal restrito."
            )
        full_cmd: Sequence[str] | str
        if sudo:
            if isinstance(cmd, str):
                full_cmd = f"sudo {cmd}"
                shell = True
            else:
                full_cmd = ["sudo", *cmd]
        else:
            full_cmd = cmd
        announce(self.logger, "action", action)
        self.logger.write(f"{paint('$', Color.COMMAND)} {paint(printable, Color.COMMAND)}")
        started = time.monotonic()
        process = subprocess.Popen(
            full_cmd,
            cwd=str(cwd) if cwd else None,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
        )
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        collected: list[str] = []
        buffer = ""
        decoder = None
        last_output = started
        last_heartbeat = started
        spinner_index = 0
        while True:
            chunk = self._read_chunk(process)
            now = time.monotonic()
            if chunk is not None:
                if decoder is None:
                    import codecs

                    decoder = codecs.getincrementaldecoder("utf-8")("replace")
                text = decoder.decode(chunk)
                collected.append(text)
                last_output = now
                self.logger.clear_transient()
                buffer = self._flush_buffer(buffer + text)
                continue
            if process.poll() is not None:
                break
            if show_progress and now - last_output >= 0.8 and now - last_heartbeat >= 0.2:
                spinner = SPINNER_FRAMES[spinner_index % len(SPINNER_FRAMES)]
                spinner_index += 1
                self.logger.transient(
                    f"{badge('rodando', Color.INFO)} {action} {paint(spinner, Color.INFO)} {paint(format_elapsed(now - started), Color.MUTED)}"
                )
                last_heartbeat = now
            time.sleep(0.1)
        tail = self._drain_remaining(process)
        if tail:
            if decoder is None:
                import codecs

                decoder = codecs.getincrementaldecoder("utf-8")("replace")
            text = decoder.decode(tail, final=True)
            collected.append(text)
            self.logger.clear_transient()
            buffer = self._flush_buffer(buffer + text)
        elif decoder is not None:
            text = decoder.decode(b"", final=True)
            if text:
                collected.append(text)
                buffer = self._flush_buffer(buffer + text)
        if buffer:
            self.logger.write(buffer.rstrip("\r\n"))
        self.logger.clear_transient()
        returncode = process.wait()
        elapsed = format_elapsed(time.monotonic() - started)
        stdout = "".join(collected)
        if returncode == 0:
            if not quiet_success:
                announce(self.logger, "done", f"{action} concluido em {elapsed}")
        else:
            announce(self.logger, "failed", f"{action} falhou em {elapsed}")
        if check and returncode != 0:
            raise RuntimeError(f"comando falhou ({returncode}): {printable}")
        return subprocess.CompletedProcess(args=full_cmd, returncode=returncode, stdout=stdout)

    def _read_chunk(self, process: subprocess.Popen[bytes]) -> bytes | None:
        try:
            return os.read(process.stdout.fileno(), 4096)
        except BlockingIOError:
            return None

    def _drain_remaining(self, process: subprocess.Popen[bytes]) -> bytes:
        chunks: list[bytes] = []
        while True:
            try:
                chunk = os.read(process.stdout.fileno(), 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _flush_buffer(self, text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        for line in lines[:-1]:
            self.logger.write(line)
        return lines[-1]


def infer_action(cmd: Sequence[str] | str, *, sudo: bool = False) -> str:
    if isinstance(cmd, str):
        return "Executando comando do shell"
    if not cmd:
        return "Executando comando"
    binary = Path(cmd[0]).name
    verb_map = {
        "pacman": "Executando pacman",
        "flatpak": "Executando flatpak",
        "npm": "Executando npm",
        "curl": "Baixando recurso",
        "git": "Executando git",
        "rclone": "Executando rclone",
        "systemctl": "Executando systemctl",
        "tar": "Extraindo arquivos",
        "mount": "Montando sistemas de arquivos",
        "firefoxpwa": "Configurando FirefoxPWA",
    }
    prefix = "com sudo: " if sudo else ""
    return prefix + verb_map.get(binary, f"Executando {binary}")


def quote_arg(value: str) -> str:
    if not value:
        return "''"
    if all(ch.isalnum() or ch in "@%_+=:,./-" for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def no_new_privs_enabled() -> bool:
    status = Path("/proc/self/status")
    if not status.exists():
        return False
    try:
        text = status.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "NoNewPrivs:\t1" in text or "NoNewPrivs: 1" in text


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
        runner.run(["cp", "-a", str(path), str(target)], sudo=True, action=f"Criando backup de {path.name}", show_progress=False)
    else:
        if runner.dry_run:
            runner.logger.write(f"{badge('dry-run', Color.DRY_RUN)} cp -a {path} {target}")
        else:
            shutil.copy2(path, target)
            announce(runner.logger, "done", f"Backup criado: {target}")
    return target


def write_text(path: Path, content: str, runner: Runner, *, mode: int = 0o644) -> None:
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            current = None
        if current == content and (path.stat().st_mode & 0o777) == mode:
            announce(runner.logger, "done", f"{path} ja esta atualizado")
            return
    if runner.dry_run:
        runner.logger.write(f"{badge('dry-run', Color.DRY_RUN)} escreveria {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    announce(runner.logger, "done", f"{path} atualizado")


def write_text_sudo(path: Path, content: str, runner: Runner, *, mode: int = 0o644) -> None:
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except (PermissionError, UnicodeDecodeError):
            current = None
        if current == content and (path.stat().st_mode & 0o777) == mode:
            announce(runner.logger, "done", f"{path} ja esta atualizado")
            return
    if runner.dry_run:
        runner.logger.write(f"{badge('dry-run', Color.DRY_RUN)} escreveria {path} com sudo")
        return
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(content)
        tmp_name = handle.name
    try:
        runner.run(
            ["install", "-m", f"{mode:04o}", tmp_name, str(path)],
            sudo=True,
            action=f"Atualizando {path.name} com privilegios",
            show_progress=False,
        )
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def ensure_owner(path: Path, user: UserInfo, runner: Runner, *, recursive: bool = False) -> None:
    flag = "-R" if recursive else ""
    cmd = ["chown"]
    if flag:
        cmd.append(flag)
    cmd.extend([f"{user.uid}:{user.gid}", str(path)])
    runner.run(cmd, sudo=True, check=False, action=f"Ajustando permissao de {path}", show_progress=False, quiet_success=True)


def confirm_phrase(phrase: str, logger: Logger) -> bool:
    typed = prompt_user(
        f"Digite {phrase} para continuar",
        logger,
        detail="O sisteminha esta aguardando sua confirmacao. Isso nao e travamento.",
        prompt_label="Confirmacao",
        allow_empty=False,
    )
    if typed == phrase:
        announce(logger, "done", "Confirmacao recebida")
        return True
    announce(logger, "skipped", "Confirmacao nao conferiu. Operacao cancelada.")
    return False


def print_lines(logger: Logger, lines: Iterable[str]) -> None:
    for line in lines:
        logger.write(line)
