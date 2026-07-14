# Reforja

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![KDE](https://img.shields.io/badge/Desktop-KDE-1D99F3?logo=kde&logoColor=white)](https://kde.org/)
[![Tests](https://img.shields.io/badge/tests-pytest-0A7F3F)](https://docs.pytest.org/)

Automacao pos-formatacao para reconstruir um ambiente Linux/KDE com consistencia, logs e etapas auditaveis.
O projeto substitui scripts shell longos por um CLI Python modular, com `apply`, `status` e `undo` por etapa.

## Destaques

- Suporte a Arch/CachyOS/SteamOS, Debian/Ubuntu e Fedora/Bazzite.
- Sistemas imutaveis (Bazzite/SteamOS) sao detectados automaticamente: pacotes nativos sao priorizados via Flatpak e os passos que dependeriam deles viram aviso/manual.
- Preparacao de Flatpak, Flathub, AppImage/FUSE, RPM Fusion (Fedora) e helper AUR quando aplicavel.
- Instalacao/configuracao de apps, webapps, Git/GitHub, rclone, fstab, drivers de GPU (AMD/NVIDIA)/jogos, Sunshine/Moonlight, gestos KDE e Num Lock.
- Integracao desktop para AppImages, incluindo Hydra Launcher com `StartupWMClass` correto no KDE Wayland.
- Gestos KDE com `libinput-gestures` para abrir o Overview com swipe de 3 dedos para cima ou para baixo.
- Execucao segura com dry-run, backups, confirmacoes para operacoes sensiveis e logs locais.

## Requisitos

- Python 3.11 ou superior.
- Sessao Linux de usuario normal, sem executar o projeto como root.
- `sudo` configurado para comandos que precisam de privilegio.
- KDE recomendado, principalmente para as etapas de gestos, Num Lock e atalhos desktop.

O bootstrap instala a dependencia Python interna (`InquirerPy`) quando necessario; `pytest`/`ruff` vem de `pip install -e .[dev]`.

## Interface grafica (GUI)

Alem do CLI, ha uma interface grafica moderna em PySide6/Qt6 que reaproveita o
mesmo motor (mesmos steps, mesmos `apply`/`status`/`undo`):

```fish
python 00-pos-formatacao-cachyos.py --gui   # bootstrap do PySide6 + abre a GUI
python -m reforja.gui                     # se o PySide6 ja estiver instalado
```

A janela traz uma barra lateral com as etapas (com indicador de conformidade),
botoes de acao (`Aplicar` / `Status` / `Undo`) sobre as etapas marcadas, console com saida em
streaming e barra de progresso. Comandos com
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
python -m reforja step 10 status
python -m reforja step 13 apply
```

## Menus

O menu principal e plano e direto:

- `Aplicar tudo`
- `Status geral`
- `Executar etapas...`
- `Instalar GUI do Reforja no sistema`
- `Sair`

Em `Executar etapas...` voce ve a lista de etapas (so pelo titulo) e **marca uma ou varias** por
checkbox; em seguida escolhe a acao **uma unica vez**: `Aplicar`, `Status` ou `Undo`. Para atualizar
apenas os AppImages, por exemplo, marque so "Atualizar AppImages" e escolha `Aplicar` — nada mais roda.

`Instalar GUI do Reforja no sistema` e um atalho: baixa o AppImage mais recente do Reforja das
GitHub Releases e cria o atalho no menu de aplicativos (mesmo mecanismo do passo 15, so o Reforja).

Cada wrapper (`scripts/NN-*.sh`) continua abrindo o menu da propria etapa, e
`python -m reforja step <id> <acao>` segue funcionando (o `id` interno e preservado para scripting).

Na **GUI**, a barra lateral lista as etapas (com checkbox, agrupadas por categoria apenas como
organizacao visual). Uma unica barra de acoes (`Aplicar` / `Status` / `Undo`) opera nas etapas
marcadas — ou na etapa destacada quando nenhuma esta marcada.

Nos menus interativos, use as setas para navegar e `espaco`/`Enter` para marcar e confirmar.

## Etapas

| ID | Etapa | Objetivo |
| --- | --- | --- |
| `00` | Atualizar e preparar o sistema | Primeiro atualiza os pacotes (pacman/apt/dnf) e depois prepara a base: Flatpak/Flathub, suporte AppImage/FUSE e helper AUR quando aplicavel. |
| `03` | Navegador e WebApps | Menu unico: instala Firefox + FirefoxPWA e cria os WebApps (ChatGPT, GSV Calendar) via FirefoxPWA, WebApp Manager ou fallback `.desktop`. |
| `05` | Configurar GPU / drivers | Detecta o fabricante (AMD/NVIDIA), instala os drivers certos (AMD: Vulkan RADV + VAAPI/VDPAU; NVIDIA: proprietario) e valida sessao grafica, OpenGL e Vulkan. Em desktop de GPU unica ainda remove os residuos do fabricante ausente (com confirmacao e backup); em laptop/hibrido nunca remove driver. (Steam/Heroic e demais apps sao do passo 10.) |
| `06` | Git / GitHub | Menu simples: instala Git + GitHub CLI (gh); conecta a conta pelo navegador e no mesmo passo ja cria o host alias SSH dedicado dela (chave + bloco Host, ideal para separar 2+ contas via git@<alias>:owner/repo.git); e adiciona repositorios (clona em ~/repositorios, escolhendo da sua lista ou por alias, e ja configura o autor dos commits). Guarda o que foi configurado em ~/.config/reforja/git.json. |
| `07` | Google Drive / rclone | Configura `rclone` e servico systemd de usuario para `~/GoogleDrive`. |
| `08` | fstab | Le os discos conectados (`lsblk`), deixa escolher quais montar no boot e em qual pasta, e grava o bloco no `/etc/fstab` com backup, preview e confirmacao digitada. Discos externos/USB usam `nofail` + `x-systemd.automount`: o boot nao espera nem quebra quando eles estao desconectados. |
| `09` | Ajustes KDE | Menu unico: gestos de 3 dedos (`libinput-gestures`, pulado em maquinas sem touchpad) e Num Lock fixo no KDE e no SDDM. |
| `10` | Apps / jogos / comunicacao / dev | Instala Steam/Heroic, comunicacao (Discord/ZapZap/TeamSpeak), Solaar/LocalSend/Flatseal/Bitwarden, ONLYOFFICE, Linux Toys, auto-cpufreq e Codex CLI. |
| `12` | Antigravity IDE | Instala e **atualiza** o Antigravity: tarball oficial com auto-update por versao (Arch/imutaveis, com atalho `.desktop` e comando `antigravity-ide`) ou repositorio nativo apt/dnf (Debian/Fedora). |
| `13` | Sunshine / Moonlight | Instala Sunshine, configura permissoes, autostart KDE, UFW e launcher quando necessario. |
| `14` | Inventario de Hardware | Coleta CPU, RAM, GPUs, discos, PCI/USB e dmidecode/inxi e salva um relatorio estavel para suporte e para outras etapas consultarem. |
| `15` | Atualizar AppImages | Instala/atualiza os AppImages geridos (Hydra Launcher e o proprio Reforja) a partir dos GitHub Releases, com atalho e icone. |
| `16` | Backup e restore de configuracoes | Faz backup so das **configuracoes** (nao dos apps) dos programas que o Reforja instala num `.tar.gz` (grava em `~/GoogleDrive/reforja-backups` quando o Drive esta montado, senao em `~/reforja-backups`; exclui caches, saves de jogos e perfis pesados). O restore pergunta o caminho do backup (util quando o rclone ainda nao esta configurado), mostra o conteudo, pede confirmacao digitada e faz copia de seguranca das configs atuais antes de sobrescrever. |

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

A etapa `15` instala o Hydra como AppImage em:

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

Esse mesmo modulo (`reforja/hardware.py`) e a fonte unica de deteccao de hardware do sistema: a etapa de gestos (`09`) e a configuracao de GPU (`05`) consultam-no para decidir touchpad e o fabricante da GPU (AMD/NVIDIA/Intel via `gpu_vendors`). O `dry-run` apenas lista o que seria coletado, e o `undo` remove o relatorio salvo.

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
