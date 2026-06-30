# Reforja

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![KDE](https://img.shields.io/badge/Desktop-KDE-1D99F3?logo=kde&logoColor=white)](https://kde.org/)
[![Tests](https://img.shields.io/badge/tests-pytest-0A7F3F)](https://docs.pytest.org/)

Automacao pos-formatacao para reconstruir um ambiente Linux/KDE com consistencia, logs e etapas auditaveis.
O projeto substitui scripts shell longos por um CLI Python modular, com `apply`, `dry-run`, `status` e `undo` por etapa.

## Destaques

- Suporte a Arch/CachyOS/SteamOS, Debian/Ubuntu e Fedora/Bazzite.
- Sistemas imutaveis (Bazzite/SteamOS) sao detectados automaticamente: pacotes nativos sao priorizados via Flatpak e os passos que dependeriam deles viram aviso/manual.
- Preparacao de Flatpak, Flathub, AppImage/FUSE, RPM Fusion (Fedora) e helper AUR quando aplicavel.
- Instalacao/configuracao de apps, webapps, Git/GitHub, rclone, fstab, NVIDIA/jogos, Sunshine/Moonlight, gestos KDE e Num Lock.
- Integracao desktop para AppImages, incluindo Hydra Launcher com `StartupWMClass` correto no KDE Wayland.
- Gestos KDE com `libinput-gestures` para abrir o Overview com swipe de 3 dedos para cima ou para baixo.
- Execucao segura com dry-run, backups, confirmacoes para operacoes sensiveis e logs locais.

## Requisitos

- Python 3.11 ou superior.
- Sessao Linux de usuario normal, sem executar o projeto como root.
- `sudo` configurado para comandos que precisam de privilegio.
- KDE recomendado, principalmente para as etapas de gestos, Num Lock e atalhos desktop.

O bootstrap instala dependencias Python internas quando necessario, incluindo `InquirerPy` e `pytest`.

## Interface grafica (GUI)

Alem do CLI, ha uma interface grafica moderna em PySide6/Qt6 que reaproveita o
mesmo motor (mesmos steps, mesmos `apply`/`dry-run`/`status`/`undo`):

```fish
python 00-pos-formatacao-cachyos.py --gui   # bootstrap do PySide6 + abre a GUI
python -m reforja.gui                     # se o PySide6 ja estiver instalado
```

A janela traz uma barra lateral com as etapas (com indicador de conformidade),
botoes de acao por etapa e globais ("Aplicar tudo", "Dry-run tudo", "Status
geral"), console com saida em streaming e barra de progresso. Comandos com
`sudo` abrem um dialogo grafico de senha (askpass) e comandos interativos
(pacman/apt) rodam num terminal embutido. A GUI checa atualizacoes no GitHub
Releases ao abrir.

### Executavel (AppImage)

O app e distribuido como **AppImage** (arquivo unico, sem instalacao). Cada push
na branch `main` gera automaticamente um novo release com o executavel anexado:

```fish
chmod +x Reforja-*-x86_64.AppImage
./Reforja-*-x86_64.AppImage
```

O AppImage embute informacao de auto-update (zsync); ferramentas como
`AppImageUpdate` aplicam atualizacoes incrementais a partir dos Releases.

Para construir localmente:

```fish
pip install -e .[gui] pyinstaller
bash packaging/build-appimage.sh   # gera dist/Reforja-*-x86_64.AppImage
```

## Uso Rapido

Executar o fluxo interativo principal:

```fish
python 00-pos-formatacao-cachyos.py
```

Executar uma etapa por wrapper:

```fish
bash scripts/10-instalar-apps-jogos-comunicacao-dev.sh
```

Executar pelo modulo Python:

```fish
python -m reforja step 10 dry-run
python -m reforja step 11 status
python -m reforja step 13 apply
```

## Menus

O menu principal oferece:

- `Apply completo`
- `Dry-run completo`
- `Status completo`
- `Apply por etapa`
- `Dry-run por etapa`
- `Undo por etapa`
- `Sair`

Cada wrapper numerado abre um menu da propria etapa com:

- `Apply`
- `Dry-run`
- `Status`
- `Undo`
- `Sair`

Nos menus interativos, use as setas para navegar, `Enter` para confirmar ou digite o numero da opcao.

## Etapas

| ID | Etapa | Objetivo |
| --- | --- | --- |
| `00` | Preparar ecossistema | Detecta a distro, prepara Flatpak/Flathub, suporte AppImage/FUSE e helper AUR quando aplicavel. |
| `01` | Atualizar sistema | Atualiza pacotes via `pacman` ou `apt`. |
| `02` | Linux Toys | Instala Linux Toys pelo script oficial. |
| `03` | Navegador | Instala Firefox, FirefoxPWA e Bitwarden. |
| `04` | WebApps | Cria ChatGPT e GSV Calendar via FirefoxPWA, WebApp Manager ou fallback `.desktop`. |
| `05` | NVIDIA / jogos | Diagnostica sessao grafica, GPUs, Steam e Heroic. |
| `06` | Git / GitHub | Instala Git, clona/atualiza o repositorio base e configura uma ou varias contas GitHub com chave SSH dedicada (alias no ~/.ssh/config + ssh-agent + orientacao de cadastro). |
| `07` | Google Drive | Configura `rclone` e servico systemd de usuario para `~/GoogleDrive`. |
| `08` | fstab | Configura montagens por label (`WINDOWS`, `DADOS WINDOWS`, `JOGOS LINUX`, `BACKUP`) com backup e confirmacao; labels ausentes na maquina sao ignoradas. |
| `09` | Gestos KDE | Configura `libinput-gestures` para Overview com swipe 3 dedos; pulada automaticamente em maquinas sem touchpad (desktops). |
| `10` | Apps | Instala Steam/Heroic, Flatpaks, Hydra AppImage, auto-cpufreq e Codex CLI. |
| `11` | Num Lock | Configura Num Lock no KDE e no SDDM. |
| `12` | Antigravity IDE | Instala Antigravity, atalho `.desktop` e comando `antigravity-ide`. |
| `13` | Sunshine / Moonlight | Instala Sunshine, configura permissoes, autostart KDE, UFW e launcher quando necessario. |
| `14` | Inventario de Hardware | Coleta CPU, RAM, GPUs, discos, PCI/USB e dmidecode/inxi e salva um relatorio estavel para suporte e para outras etapas consultarem. |

## Detalhes Importantes

### Gestos KDE

A etapa `09` so se aplica a maquinas com touchpad (notebooks). Em desktops sem touchpad ela e pulada automaticamente, sem instalar nada.

Quando aplicavel, usa `libinput-gestures` e cria:

```text
~/.config/libinput-gestures.conf
~/.local/bin/kde-gnome-like-overview
```

O usuario precisa pertencer ao grupo `input` para que o servico consiga ler o touchpad:

```fish
sudo gpasswd -a $USER input
```

Depois de alterar o grupo, faca logout/login ou reinicie.

### Hydra AppImage

A etapa `10` instala o Hydra como AppImage em:

```text
~/AppImages/HydraLauncher-latest.AppImage
```

E cria o atalho canonico:

```text
~/.local/share/applications/hydralauncher.desktop
```

O atalho usa `StartupWMClass=hydralauncher`, necessario para agrupamento correto no KDE Wayland.

### AppImages E FUSE

A etapa `00` centraliza o suporte AppImage:

- Arch/CachyOS: `fuse2`.
- Fedora/Bazzite: primeira opcao disponivel entre `fuse` e `fuse-libs`.
- Debian/Ubuntu: primeira opcao disponivel entre `libfuse2t64`, `libfuse2` e `fuse`.
- Em sistemas imutaveis o pacote nativo nao e instalado; o suporte vem da imagem base ou de Flatpak.

### Sunshine / Moonlight

A etapa `13` instala e configura o Sunshine para streaming local com Moonlight:

```fish
bash scripts/13-instalar-configurar-sunshine-cachyos.sh
python -m reforja step 13 status
```

Ela cria o autostart KDE em:

```text
~/.config/autostart/sunshine.desktop
```

O lancador no menu de aplicativos so e criado como fallback em:

```text
~/.local/share/applications/sunshine.desktop
```

Se o pacote do Sunshine ja fornecer um `.desktop`, o sistema preserva o lancador existente. A interface local fica em:

```text
https://localhost:47990
```

Quando o UFW estiver instalado e ativo, a etapa libera as portas do Sunshine/Moonlight:

```text
TCP 47984:47990
TCP 48010
UDP 47998:48000
```

O usuario precisa pertencer ao grupo `input`; se a etapa adicionar o grupo agora, faca logout/login ou reinicie antes de validar controles, mouse e gamepad.

### Inventario de Hardware

A etapa `14` coleta um retrato do hardware (CPU, RAM, GPUs, discos, PCI/USB e, quando disponiveis, `dmidecode` e `inxi`) e grava num caminho estavel:

```text
~/.cache/scripts-linux/hardware/hardware-info.txt
```

Esse mesmo modulo (`reforja/hardware.py`) e a fonte unica de deteccao de hardware do sistema: a etapa de gestos (`09`) e a validacao de GPU (`05`) consultam-no para decidir touchpad e GPUs. O `dry-run` apenas lista o que seria coletado, e o `undo` remove o relatorio salvo.

## Seguranca E Confiabilidade

- Nao execute como root.
- `sudo` e usado apenas nos comandos que precisam de privilegio.
- `dry-run` mostra a intencao antes de aplicar mudancas.
- Arquivos importantes recebem backup antes de alteracao.
- Operacoes sensiveis, como `fstab`, exigem confirmacao digitada.
- Logs ficam em `./LOGS/` e nao devem ser versionados.
- Caminhos usam o usuario detectado em runtime, sem hardcode de home.

## Desenvolvimento

Validar sintaxe:

```fish
python -m py_compile 00-pos-formatacao-cachyos.py reforja/*.py
```

Rodar testes:

```fish
python -m pytest
```

## Estrutura

```text
.
├── 00-pos-formatacao-cachyos.py
├── assets/
│   └── hydra.png
├── reforja/
│   ├── cli.py
│   ├── core.py
│   ├── desktop.py
│   ├── installers.py
│   ├── platform.py
│   ├── steps.py
│   └── steps_base.py
├── scripts/
│   ├── 00-preparar-ecossistema-cachyos.sh
│   ├── ...
│   └── 13-instalar-configurar-sunshine-cachyos.sh
├── tests/
└── pyproject.toml
```

## Arquivos Ignorados

O repositorio ignora artefatos locais:

- `LOGS/`
- `fstab-backups/`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- `*.log`

Esses arquivos podem existir no ambiente local, mas nao devem ser enviados ao GitHub.
