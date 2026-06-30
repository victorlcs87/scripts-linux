"""Terminal embutido baseado em pty para comandos interactive_tty.

Comandos como pacman/apt precisam de um TTY real para perguntar (Y/n, escolha
de mirror). Em vez de assumir o terminal do processo, rodamos esses comandos
num pty cujo master e lido pelo event loop do Qt e exibido num widget. O usuario
digita normalmente; as teclas vao para o pty.

O emulador e proposital e pragmatico: trata \\r, \\n, backspace e tabulacao,
removendo sequencias ANSI. Cobre bem os gerenciadores de pacotes; nao pretende
ser um emulador VT completo.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import signal
import struct
import termios
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QSocketNotifier, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][0-9A-Za-z]")
_MAX_LINES = 5000


class TerminalWidget(QPlainTextEdit):
    finished = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(False)
        self.setUndoRedoEnabled(False)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self._master_fd: int | None = None
        self._pid: int | None = None
        self._notifier: QSocketNotifier | None = None
        self._on_finished: Callable[[int], None] | None = None
        self._lines: list[str] = [""]
        self._col = 0

    # --- ciclo de vida do processo ----------------------------------------------
    def run_command(
        self,
        cmd: Sequence[str] | str,
        cwd: Path | None,
        env: dict[str, str],
        *,
        on_finished: Callable[[int], None] | None = None,
    ) -> None:
        self._on_finished = on_finished
        self._reset_screen()
        pid, master_fd = pty.fork()
        if pid == 0:  # processo filho
            try:
                if cwd:
                    os.chdir(str(cwd))
                if isinstance(cmd, str):
                    os.execvpe("/bin/sh", ["/bin/sh", "-c", cmd], env)
                else:
                    os.execvpe(cmd[0], list(cmd), env)
            except Exception:  # noqa: BLE001 - precisamos sair do filho de qualquer forma
                os._exit(127)
        self._pid = pid
        self._master_fd = master_fd
        self._set_winsize()
        self._notifier = QSocketNotifier(master_fd, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._read_ready)

    def _read_ready(self) -> None:
        if self._master_fd is None:
            return
        try:
            data = os.read(self._master_fd, 4096)
        except OSError:
            data = b""
        if not data:
            self._finish()
            return
        self._feed(data.decode("utf-8", "replace"))

    def _finish(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier = None
        returncode = 0
        if self._pid is not None:
            try:
                _, status = os.waitpid(self._pid, 0)
                if os.WIFEXITED(status):
                    returncode = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    returncode = 128 + os.WTERMSIG(status)
            except OSError:
                returncode = 0
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        self._master_fd = None
        self._pid = None
        if self._on_finished is not None:
            self._on_finished(returncode)
        self.finished.emit(returncode)

    def interrupt(self) -> None:
        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGINT)
            except OSError:
                pass

    def _set_winsize(self) -> None:
        if self._master_fd is None:
            return
        cols = max(20, self.viewport().width() // max(1, self.fontMetrics().horizontalAdvance("M")))
        rows = max(5, self.viewport().height() // max(1, self.fontMetrics().height()))
        try:
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    # --- emulacao de tela --------------------------------------------------------
    def _reset_screen(self) -> None:
        self._lines = [""]
        self._col = 0
        self.clear()

    def _feed(self, text: str) -> None:
        text = _ANSI_RE.sub("", text)
        for ch in text:
            if ch == "\n":
                self._lines.append("")
                self._col = 0
            elif ch == "\r":
                self._col = 0
            elif ch == "\b":
                if self._col > 0:
                    self._col -= 1
            elif ch == "\t":
                spaces = 8 - (self._col % 8)
                self._write_chars(" " * spaces)
            elif ch == "\x07":
                continue
            else:
                self._write_chars(ch)
        if len(self._lines) > _MAX_LINES:
            del self._lines[: len(self._lines) - _MAX_LINES]
        self._render()

    def _write_chars(self, chars: str) -> None:
        line = self._lines[-1]
        end = self._col + len(chars)
        if self._col <= len(line):
            line = line[: self._col] + chars + line[end:]
        else:
            line = line + " " * (self._col - len(line)) + chars
        self._lines[-1] = line
        self._col = end

    def _render(self) -> None:
        self.setPlainText("\n".join(self._lines))
        self.moveCursor(self.textCursor().MoveOperation.End)

    # --- entrada do usuario ------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (override Qt)
        if self._master_fd is None:
            return
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            data = b"\r"
        elif key == Qt.Key.Key_Backspace:
            data = b"\x7f"
        elif key == Qt.Key.Key_Tab:
            data = b"\t"
        elif event.modifiers() & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_C:
            data = b"\x03"
        else:
            text = event.text()
            if not text:
                return
            data = text.encode("utf-8")
        try:
            os.write(self._master_fd, data)
        except OSError:
            pass


class TerminalExecutor(QObject):
    """Adapter InteractiveExecutor: roda o comando no TerminalWidget (thread de UI)
    e bloqueia a thread de trabalho ate o processo terminar."""

    _start = Signal(object)

    def __init__(self, terminal: TerminalWidget, *, on_activate: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._terminal = terminal
        self._on_activate = on_activate
        self._start.connect(self._on_start, Qt.ConnectionType.QueuedConnection)

    def _on_start(self, req: dict) -> None:
        if self._on_activate is not None:
            self._on_activate()
        self._terminal.run_command(
            req["cmd"],
            req["cwd"],
            req["env"],
            on_finished=lambda rc: self._on_done(req, rc),
        )

    def _on_done(self, req: dict, rc: int) -> None:
        req["returncode"] = rc
        req["event"].set()

    def __call__(self, cmd, *, cwd, env, action) -> int:
        req = {"cmd": cmd, "cwd": cwd, "env": env, "returncode": 0, "event": threading.Event()}
        self._start.emit(req)
        req["event"].wait()
        return int(req["returncode"])
