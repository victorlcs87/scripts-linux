# Kit pós-formatação — CachyOS KDE / Alienware 16 Aurora

Este pacote organiza as etapas pós-formatação do notebook em uma ordem lógica.

## Como usar

```bash
unzip kit-pos-formatacao-cachyos-completo-v2.zip
cd kit-pos-formatacao-cachyos-completo
python scripts/00-pos-formatacao-cachyos.py
```

Ou execute scripts individualmente:

```bash
bash scripts/01-atualizar-sistema-cachyos.sh
bash scripts/09-instalar-apps-jogos-comunicacao-dev.sh
```

## Ordem sugerida

1. Abrir o Shelly e habilitar Flatpak, AppImage e repositórios/AUR.
2. Atualizar o sistema.
3. Instalar Linux Toys.
4. Instalar Firefox Flatpak, Bitwarden, FirefoxPWA e preparar extensões.
5. Criar WebApps: ChatGPT e GSV Calendar.
6. Validar NVIDIA, Steam e jogos.
7. Configurar Git/GitHub e clonar `scripts-linux` em `/home/repositorios`.
8. Configurar Google Drive/rclone.
9. Configurar montagem de discos via fstab.
10. Ajustar KDE, gestos e aparência.
11. Instalar apps: Steam, Heroic, Hydra Launcher, Discord, TeamSpeak, ZapZap, Bitwarden, ONLYOFFICE, Google Chrome, Minecraft Bedrock Launcher e Google Antigravity.
12. Fixar Num Lock.

## Padrão aplicado nos scripts

- Menu interativo.
- Opções: Apply, Dry-run, Status, Undo e Sair quando aplicável.
- Logs em `./LOGS` dentro do diretório onde o script foi executado.
- Nada executado como root diretamente; usa `sudo` apenas quando necessário.
- Detecta o usuário real com `SUDO_USER` quando aplicável.
- Usa home por variável, sem hardcode de `/home/victorlcs`.
- Colorido.
- Backups antes de alterações sensíveis.

## Observação sobre Shelly

O Shelly é tratado como etapa manual assistida. O script abre o Shelly quando encontrado e pausa para você habilitar Flatpak, AppImage e AUR antes de continuar.
