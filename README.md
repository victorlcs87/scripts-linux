# scripts-linux - sisteminha pos-formatacao CachyOS/KDE

Automacao modular em Python para refazer o ambiente apos formatar o CachyOS/KDE.
O projeto substitui os antigos scripts shell grandes por um CLI Python com etapas reutilizaveis, logs, dry-run, status e undo.

## Uso Rapido

No fish:

```fish
python 00-pos-formatacao-cachyos.py
```

Tambem da para abrir uma etapa especifica pelo wrapper numerado:

```fish
bash 09-instalar-apps-jogos-comunicacao-dev.sh
```

Ou chamar diretamente o modulo Python:

```fish
python -m postformat step 09 dry-run
python -m postformat step 10 status
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

Cada script numerado abre um menu proprio com:

- `Apply`
- `Dry-run`
- `Status`
- `Undo`
- `Sair`

## Ordem Das Etapas

| ID | Etapa | O que faz |
| --- | --- | --- |
| `00` | Shelly | Abre Shelly/CachyOS Hello para habilitar Flatpak, AppImage e AUR. |
| `01` | Atualizar sistema | Instala `pacman-contrib` e roda `sudo pacman -Syu`. |
| `00.2` | Linux Toys | Instala Linux Toys via script oficial. |
| `00.3` | AppImage / fuse2 | Instala `fuse2` para compatibilidade com AppImages. |
| `02` | Navegador | Instala Firefox do sistema, FirefoxPWA e Bitwarden Flatpak. |
| `03` | WebApps | Tenta FirefoxPWA, depois WebApp Manager, depois fallback `.desktop`. |
| `04` | NVIDIA / jogos | Apenas valida GPU, sessao, Steam e Heroic. |
| `05` | Git / GitHub | Instala Git e clona/puxa `scripts-linux` em `/home/repositorios`. |
| `06` | Google Drive | Configura `rclone` e servico systemd de usuario para `~/GoogleDrive`. |
| `07` | fstab | Configura montagens por label para Windows, dados Windows e jogos Linux. |
| `08` | Gestos KDE | Mantem apenas gestos; nao altera splash do KDE. |
| `09` | Apps | Instala Steam/Heroic por pacote, demais apps via Flatpak, Hydra AppImage e Codex CLI. |
| `10` | Num Lock | Configura Num Lock no KDE e na tela de login SDDM. |
| `11` | Antigravity IDE | Instala Antigravity, cria atalho e comando `antigravity-ide`. |

## Comportamento Por Etapa

### WebApps

A etapa `03` tenta criar ChatGPT e GSV Calendar nesta ordem:

1. `firefoxpwa`, usando perfis e manifests quando possivel.
2. `webapp-manager`, abrindo a ferramenta para criacao assistida.
3. Fallback `.desktop` com Firefox, marcado como fallback e nao como PWA real.

O FirefoxPWA pode exigir a extensao do Firefox:

```text
https://addons.mozilla.org/firefox/addon/pwas-for-firefox/
```

### Apps E AppImages

A etapa `09` usa:

- Steam e Heroic com preferencia por pacote do sistema/AUR.
- Discord, TeamSpeak, ZapZap, ONLYOFFICE, Chrome, Minecraft Bedrock Launcher e Bitwarden via Flatpak.
- Hydra via AppImage, com icone em `assets/hydra.png`.
- Codex CLI com:

```fish
sudo pacman -S --needed nodejs npm
sudo npm install -g @openai/codex
```

O suporte a AppImage fica centralizado na etapa `00.3`, que instala `fuse2`. A etapa do Hydra tambem garante `fuse2` antes de baixar o AppImage.

### Num Lock

A etapa `10` configura:

- Sessao KDE: `~/.config/kcminputrc` com `NumLock=0`.
- Login SDDM: `/etc/sddm.conf.d/10-numlock.conf` com `Numlock=on`.

O `status` tambem mostra possiveis arquivos conflitantes em `/etc/sddm.conf.d/`.

### Antigravity IDE

A etapa `11` instala em:

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
- Arquivos importantes recebem backup antes de alteracoes.
- Operacoes sensiveis, como `fstab`, exigem confirmacao digitada.
- Caminhos de usuario usam deteccao por variavel e banco de usuarios, sem hardcode de `/home/victorlcs`.
- Comandos exibidos sao compativeis com fish; quando necessario, a propria etapa mostra o comando fish adequado.

## Estrutura Do Projeto

```text
.
├── 00-pos-formatacao-cachyos.py
├── 01-atualizar-sistema-cachyos.sh
├── ...
├── 11-instalar-antigravity-ide.sh
├── assets/
│   └── hydra.png
├── legacy/
├── postformat/
│   ├── cli.py
│   ├── core.py
│   ├── desktop.py
│   ├── installers.py
│   ├── steps.py
│   └── steps_base.py
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

Se `pytest` nao estiver instalado:

```fish
sudo pacman -S --needed python-pytest
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

## Legado

Scripts antigos foram movidos para `legacy/` apenas para consulta historica. O fluxo mantido e suportado fica no CLI Python e nos wrappers numerados da raiz.
