"""Pacote de etapas. Re-exporta as classes e ALL_STEPS para preservar a API."""

from ..steps_base import Step
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
]
