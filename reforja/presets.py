"""Perfis (presets) de pos-formatacao: conjuntos de itens que atravessam etapas.

Cada preset mapeia `{id_da_etapa: (chaves_de_StepTask,)}` referenciando itens reais
declarados em `steps/*.py`. A GUI usa isso para pre-marcar a previa consolidada
(`BatchPreviewDialog`) com "o kit do perfil", em vez do padrao "so o que falta".

As chaves seguem os `StepTask.key`:
  - etapa 10 (apps): o nome do app ("Steam", "Discord"...).
  - etapa 03 (navegador): "navegador" e "webapp-<slug>".
  - etapas mono-tarefa (05 GPU, 06 Git, 12 Antigravity, 13 Sunshine, 09 KDE): a
    chave da tarefa principal. GPU/Git/KDE usam tarefas dinamicas; para essas o
    preset lista a ETAPA (valor vazio ()) e a previa marca o que a etapa oferece.

Nada aqui roda comandos — e so a curadoria dos conjuntos, ajustavel a vontade.
"""

from __future__ import annotations

# {nome_do_preset: {id_etapa: (chaves,)}}. Uma tupla vazia () significa "todos os
# itens preselecionaveis desta etapa" (resolvido na hora, contra o plano sondado).
PRESETS: dict[str, dict[str, tuple[str, ...]]] = {
    "Gamer": {
        "00": (),  # atualizar/preparar o sistema
        "05": (),  # GPU/drivers (itens dinamicos por fabricante)
        "10": ("Steam", "Heroic", "Discord", "TeamSpeak", "Minecraft Bedrock Launcher"),
        "13": (),  # Sunshine/Moonlight (streaming)
    },
    "Dev": {
        "00": (),
        "06": (),  # Git / GitHub
        "03": ("navegador",),
        "10": ("Codex CLI", "ONLYOFFICE"),
        "12": (),  # Antigravity IDE
    },
    "Essencial": {
        "00": (),  # atualizar + Flatpak/Flathub + AppImage/FUSE
        "03": ("navegador",),
        "09": (),  # ajustes KDE
        "10": ("Bitwarden", "LocalSend", "Flatseal"),
    },
    "Comunicacao": {
        "10": ("Discord", "TeamSpeak", "ZapZap"),
    },
}


def preset_names() -> list[str]:
    return list(PRESETS)


def preset_selection(name: str) -> dict[str, tuple[str, ...]]:
    """Mapa {id_etapa: (chaves,)} do preset (copia rasa; () = todos os itens)."""
    return dict(PRESETS.get(name, {}))
