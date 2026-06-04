from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core import Runner, backup_existing, write_text


@dataclass(frozen=True)
class DesktopEntry:
    name: str
    exec_line: str
    icon: str | None = None
    comment: str | None = None
    categories: tuple[str, ...] = ("Utility",)
    terminal: bool = False
    mime_types: tuple[str, ...] = field(default_factory=tuple)
    startup_wm_class: str | None = None

    def render(self) -> str:
        lines = [
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            f"Name={self.name}",
        ]
        if self.comment:
            lines.append(f"Comment={self.comment}")
        lines.append(f"Exec={self.exec_line}")
        if self.icon:
            lines.append(f"Icon={self.icon}")
        lines.extend(
            [
                f"Terminal={'true' if self.terminal else 'false'}",
                "StartupNotify=true",
            ]
        )
        if self.startup_wm_class:
            lines.append(f"StartupWMClass={self.startup_wm_class}")
        lines.append(f"Categories={';'.join(self.categories)};")
        if self.mime_types:
            lines.append(f"MimeType={';'.join(self.mime_types)};")
        return "\n".join(lines) + "\n"


def install_desktop_entry(path: Path, entry: DesktopEntry, runner: Runner) -> None:
    backup_existing(path, runner)
    write_text(path, entry.render(), runner, mode=0o644)
    runner.run(["update-desktop-database", str(path.parent)], check=False)
