"""Pacote de etapas. Re-exporta as classes, ALL_STEPS e ALL_GROUPS."""

from ..steps_base import Step, StepGroup
from .appimage import UpdateAppImagesStep
from .browser import BrowserStep, WebAppsStep
from .dev import AntigravityStep, GitStep
from .gaming import AppsStep, NvidiaSteamStep, SunshineStep
from .inventory import HardwareStep
from .kde import GesturesStep, NumLockStep
from .storage import FstabStep, RcloneStep
from .system import LinuxToysStep, ShellyStep, UpdateSystemStep

ALL_STEPS: tuple[type[Step], ...] = (
    ShellyStep,
    UpdateSystemStep,
    LinuxToysStep,
    BrowserStep,
    WebAppsStep,
    NvidiaSteamStep,
    GitStep,
    RcloneStep,
    FstabStep,
    GesturesStep,
    AppsStep,
    NumLockStep,
    AntigravityStep,
    SunshineStep,
    HardwareStep,
    UpdateAppImagesStep,
)

# Camada de categorias para navegacao (CLI/GUI). Orthogonal a ALL_STEPS, que
# permanece a lista canonica sequencial (IDs 00..15 inalterados).
ALL_GROUPS: tuple[StepGroup, ...] = (
    StepGroup("sistema", "Sistema base", (ShellyStep, UpdateSystemStep)),
    StepGroup("apps", "Aplicativos", (LinuxToysStep, BrowserStep, WebAppsStep, AppsStep, UpdateAppImagesStep)),
    StepGroup("dev", "Dev", (GitStep, AntigravityStep)),
    StepGroup("jogos", "Jogos e streaming", (NvidiaSteamStep, SunshineStep)),
    StepGroup("kde", "Desktop / KDE", (GesturesStep, NumLockStep)),
    StepGroup("armazenamento", "Armazenamento", (RcloneStep, FstabStep)),
    StepGroup("info", "Hardware / Info", (HardwareStep,)),
)

__all__ = [
    "ShellyStep",
    "UpdateSystemStep",
    "LinuxToysStep",
    "BrowserStep",
    "WebAppsStep",
    "NvidiaSteamStep",
    "GitStep",
    "RcloneStep",
    "FstabStep",
    "GesturesStep",
    "AppsStep",
    "NumLockStep",
    "AntigravityStep",
    "SunshineStep",
    "HardwareStep",
    "UpdateAppImagesStep",
    "ALL_STEPS",
    "ALL_GROUPS",
    "StepGroup",
]
