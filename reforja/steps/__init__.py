"""Pacote de etapas. Re-exporta as classes, ALL_STEPS e ALL_GROUPS."""

from ..steps_base import Step, StepGroup
from .appimage import UpdateAppImagesStep
from .browser import BrowserStep
from .dev import AntigravityStep, GitStep
from .gaming import AppsStep, GpuStep, SunshineStep
from .inventory import HardwareStep
from .kde import KdeStep
from .storage import FstabStep, RcloneStep
from .system import ShellyStep

ALL_STEPS: tuple[type[Step], ...] = (
    ShellyStep,
    BrowserStep,
    GpuStep,
    GitStep,
    RcloneStep,
    FstabStep,
    KdeStep,
    AppsStep,
    AntigravityStep,
    SunshineStep,
    HardwareStep,
    UpdateAppImagesStep,
)

# Camada de categorias para navegacao (CLI/GUI). Orthogonal a ALL_STEPS, que
# permanece a lista canonica sequencial. Lacunas de ID vem de fusoes:
# 02 (Linux Toys) virou item do passo 10; 04 (WebApps) fundiu no 03;
# 11 (Num Lock) fundiu no 09.
ALL_GROUPS: tuple[StepGroup, ...] = (
    StepGroup("sistema", "Sistema base", (ShellyStep,)),
    StepGroup("apps", "Aplicativos", (BrowserStep, AppsStep, UpdateAppImagesStep)),
    StepGroup("dev", "Dev", (GitStep, AntigravityStep)),
    StepGroup("jogos", "Jogos e streaming", (GpuStep, SunshineStep)),
    StepGroup("kde", "Desktop / KDE", (KdeStep,)),
    StepGroup("armazenamento", "Armazenamento", (RcloneStep, FstabStep)),
    StepGroup("info", "Hardware / Info", (HardwareStep,)),
)

__all__ = [
    "ShellyStep",
    "BrowserStep",
    "GpuStep",
    "GitStep",
    "RcloneStep",
    "FstabStep",
    "KdeStep",
    "AppsStep",
    "AntigravityStep",
    "SunshineStep",
    "HardwareStep",
    "UpdateAppImagesStep",
    "ALL_STEPS",
    "ALL_GROUPS",
    "StepGroup",
]
