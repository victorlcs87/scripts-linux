# scripts-linux - sisteminha pos-formatacao CachyOS/KDE

Automacao modular em Python para refazer o ambiente apos formatar o CachyOS/KDE. O ponto de entrada continua simples:

```fish
python 00-pos-formatacao-cachyos.py
```

Os scripts numerados ainda existem, mas agora sao wrappers para o sistema Python. Ao executar qualquer um deles diretamente, o menu interativo da etapa sera aberto.

## Menus

O sistema preserva o padrao desejado:

- `Apply`
- `Dry-run`
- `Status`
- `Undo`
- `Sair`

O menu principal tambem permite rodar apply, dry-run, status e undo por etapa.

## Etapas

1. Shelly: etapa assistida para habilitar Flatpak, AppImage e AUR.
2. Atualizar sistema com `pacman -Syu`.
3. Linux Toys.
4. Suporte AppImage com `fuse2`.
5. Firefox, FirefoxPWA e Bitwarden.
6. WebApps: tenta FirefoxPWA, depois WebApp Manager, e por ultimo fallback `.desktop`.
7. Validacao NVIDIA, Steam e jogos.
8. Git/GitHub e clone de `scripts-linux`.
9. Google Drive com rclone e systemd user service.
10. Montagem de discos via `/etc/fstab`.
11. Gestos KDE.
12. Apps: Steam e Heroic por pacote do sistema; demais via Flatpak; Hydra AppImage com icone customizado; Codex CLI.
13. Num Lock no KDE e na tela de login SDDM.
14. Antigravity IDE, atalho e comando `antigravity-ide`.

## Garantias

- Nao rode como root; o sistema chama `sudo` apenas nos comandos que precisam.
- Logs ficam em `./LOGS/`.
- Dry-run mostra as acoes sem alterar o sistema.
- Arquivos importantes recebem backup antes de alteracoes.
- Operacoes sensiveis, como `fstab`, pedem confirmacao explicita.
- Comandos mostrados sao compativeis com fish; quando for necessario ajustar PATH no fish, o sistema mostra `fish_add_path`.

## Estrutura

- `postformat/`: codigo Python modular.
- `assets/hydra.png`: icone usado no atalho do Hydra Launcher.
- `tests/`: testes unitarios com `pytest`.
- `legacy/`: scripts antigos arquivados para consulta.

## Testes

```fish
python -m py_compile 00-pos-formatacao-cachyos.py postformat/*.py
python -m pytest
```

## Observacoes

- FirefoxPWA pode exigir a extensao do Firefox: <https://addons.mozilla.org/firefox/addon/pwas-for-firefox/>
- O comando `antigravity-ide` e instalado em `~/.local/bin`. Se esse diretorio nao estiver no PATH do fish, rode:

```fish
fish_add_path ~/.local/bin
```
