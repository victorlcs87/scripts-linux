# Documentacao Legada

Este arquivo foi preservado apenas como referencia historica da versao antiga do kit pos-formatacao.

O fluxo atual nao usa mais a estrutura `scripts/` descrita anteriormente. A automacao principal agora fica na raiz do repositorio e usa o pacote Python `postformat/`.

## Como Usar A Versao Atual

No fish, a partir da raiz do repositorio:

```fish
python 00-pos-formatacao-cachyos.py
```

Ou execute uma etapa especifica:

```fish
bash 09-instalar-apps-jogos-comunicacao-dev.sh
python -m postformat step 09 dry-run
```

## Onde Esta A Documentacao Atual

Leia o README principal:

```text
../README.md
```

Ele descreve:

- menu principal;
- scripts numerados;
- ordem das etapas;
- WebApps via FirefoxPWA/WebApp Manager/fallback;
- suporte AppImage com `fuse2`;
- instalacao de apps e Codex CLI;
- Num Lock no KDE e SDDM;
- Antigravity IDE;
- testes e arquivos ignorados.

## Scripts Arquivados

Os scripts neste diretorio nao devem ser usados como fluxo principal. Eles foram mantidos para consulta, comparacao e recuperacao manual de alguma logica antiga.
