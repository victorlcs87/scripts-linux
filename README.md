# scripts-linux - sisteminha pos-formatacao CachyOS/KDE

Automacao modular em Python para reconstruir o ambiente apos formatar o CachyOS/KDE.
O projeto substitui os antigos scripts shell grandes por um CLI Python com etapas reutilizaveis, logs, dry-run, status, undo e uma interface colorida para terminal.

## Uso Rapido

No fish:

```fish
python 00-pos-formatacao-cachyos.py
```

Na primeira execucao pelo script principal, o projeto pode instalar automaticamente as dependencias Python internas necessarias, incluindo `pytest`.

Tambem da para abrir uma etapa especifica por wrapper:

```fish
bash scripts/10-instalar-apps-jogos-comunicacao-dev.sh
```

Ou chamar o modulo Python diretamente:

```fish
python -m postformat step 10 dry-run
python -m postformat step 11 status
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

Cada wrapper numerado abre um menu proprio com:

- `Apply`
- `Dry-run`
- `Status`
- `Undo`
- `Sair`

Nos menus interativos, voce pode:

- usar `↑` e `↓` para navegar
- pressionar `Enter` para confirmar
- digitar o numero da opcao como atalho

## Ordem Das Etapas

| ID | Etapa | O que faz |
| --- | --- | --- |
| `00` | Preparar ecossistema | Verifica Shelly, instala/configura Flatpak + flathub, garante helper AUR e instala `fuse2`. |
| `01` | Atualizar sistema | Instala `pacman-contrib` e roda `sudo pacman -Syu`. |
| `02` | Linux Toys | Instala Linux Toys via script oficial. |
| `03` | Navegador | Instala Firefox do sistema, FirefoxPWA e Bitwarden Flatpak. |
| `04` | WebApps | Tenta FirefoxPWA, depois WebApp Manager, depois fallback `.desktop`. |
| `05` | NVIDIA / jogos | Faz um diagnostico amigavel da sessao grafica, GPUs, Steam e Heroic. |
| `06` | Git / GitHub | Instala Git e clona/puxa `scripts-linux` em `/home/repositorios`. |
| `07` | Google Drive | Configura `rclone` e servico systemd de usuario para `~/GoogleDrive`. |
| `08` | fstab | Configura montagens por label para Windows, dados Windows e jogos Linux. |
| `09` | Gestos KDE | Instala e configura gestos com `libinput-gestures`; nao altera splash do KDE. |
| `10` | Apps | Detecta apps ja instalados por sistema, Flatpak ou AppImage antes de instalar Steam/Heroic, demais Flatpaks, Hydra e Codex CLI. |
| `11` | Num Lock | Configura Num Lock no KDE e na tela de login SDDM. |
| `12` | Antigravity IDE | Instala Antigravity, cria atalho e comando `antigravity-ide`. |

## Etapa 00 E Shelly

A etapa `00` nao depende mais da ideia de "abrir o Shelly e ligar toggles" para prosseguir.
Ela prepara o sistema por linha de comando e usa o Shelly apenas como fallback assistido se faltar algo.

Hoje o projeto trata como verificado que o `shelly` atual expoe CLI para:

- `shelly flatpak`
- `shelly appimage`
- `shelly aur`

O fluxo nao depende de um comando documentado para "ativar" a interface grafica do Shelly.
Na pratica, a etapa `00` faz isto:

- garante `flatpak`
- garante o remote `flathub`
- tenta garantir um helper AUR como `paru` ou `yay`
- instala `fuse2` para compatibilidade com AppImages
- usa `shelly-ui` ou `shelly` apenas se ainda faltar algo

## Comportamento Por Etapa

### WebApps

A etapa `04` tenta criar ChatGPT e GSV Calendar nesta ordem:

1. `firefoxpwa`, usando perfis e manifests quando possivel.
2. `webapp-manager`, abrindo a ferramenta para criacao assistida.
3. Fallback `.desktop` com Firefox, marcado como fallback e nao como PWA real.

O FirefoxPWA pode exigir a extensao do Firefox:

```text
https://addons.mozilla.org/firefox/addon/pwas-for-firefox/
```

### Apps E AppImages

A etapa `10` usa:

- Steam e Heroic com preferencia por pacote do sistema/AUR.
- Discord, TeamSpeak, ONLYOFFICE, Chrome, Minecraft Bedrock Launcher e Bitwarden via Flatpak.
- ZapZap com preferencia por pacote nativo/AUR.
- Hydra via AppImage, com icone em `assets/hydra.png`.
- Codex CLI com:

```fish
sudo pacman -S --needed nodejs npm
sudo npm install -g @openai/codex
```

O suporte a AppImage fica centralizado na etapa `00`, que instala `fuse2` e prepara o ambiente.
A etapa do Hydra tambem revalida `fuse2` antes de baixar o AppImage.

### Num Lock

A etapa `11` configura:

- Sessao KDE: `~/.config/kcminputrc` com `NumLock=0`.
- Login SDDM: `/etc/sddm.conf.d/10-numlock.conf` com `Numlock=on`.

O `status` tambem mostra possiveis arquivos conflitantes em `/etc/sddm.conf.d/`.

### Antigravity IDE

A etapa `12` instala em:

```text
~/Antigravity IDE
```

E cria:

```text
~/.local/share/applications/antigravity-ide.desktop
~/.local/bin/antigravity-ide
```

O comando `antigravity-ide` usa `nohup` em background, para o terminal nao ficar preso.

Se `~/.local/bin` nao estiver no PATH do fish:

```fish
fish_add_path ~/.local/bin
```

## Garantias E Seguranca

- Nao execute o sistema como root.
- O CLI chama `sudo` apenas nos comandos que precisam.
- Logs ficam em `./LOGS/`, no diretorio de execucao.
- Dry-run mostra o que seria feito sem aplicar alteracoes.
- As etapas verificam o estado atual antes de agir e pulam instalacoes, arquivos, atalhos ou servicos que ja estejam prontos.
- Arquivos importantes recebem backup antes de alteracoes.
- Operacoes sensiveis, como `fstab`, exigem confirmacao digitada.
- Caminhos de usuario usam deteccao por variavel e banco de usuarios, sem hardcode de `/home/victorlcs`.
- Comandos exibidos sao compativeis com fish; quando necessario, a propria etapa mostra o comando fish adequado.
- O CLI ativa um tema colorido no terminal quando ANSI estiver disponivel. Para desativar, use `NO_COLOR=1`.

## Estrutura Do Projeto

```text
.
├── 00-pos-formatacao-cachyos.py
├── pyproject.toml
├── assets/
│   └── hydra.png
├── postformat/
│   ├── cli.py
│   ├── core.py
│   ├── desktop.py
│   ├── installers.py
│   ├── steps.py
│   └── steps_base.py
├── scripts/
│   ├── 00-preparar-ecossistema-cachyos.sh
│   ├── 01-atualizar-sistema-cachyos.sh
│   ├── 02-instalar-linux-toys.sh
│   ├── ...
│   └── 12-instalar-antigravity-ide.sh
└── tests/
```

## Desenvolvimento

Validar sintaxe Python:

```fish
python -m py_compile 00-pos-formatacao-cachyos.py postformat/*.py
```

Rodar testes:

```fish
python -m pytest
```

## Git E Arquivos Ignorados

O repositorio ignora arquivos gerados localmente:

- `LOGS/`
- `fstab-backups/`
- `__pycache__/`
- `.pytest_cache/`
- `*.pyc`
- `*.log`

Esses arquivos podem existir no disco local, mas nao devem voltar para o GitHub.
