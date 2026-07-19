# Container de teste do Reforja

Um Arch limpo (alvo primario: CachyOS/Arch) com Python + Qt6/PySide6, rodando
como usuario nao-root com sudo — o mesmo shape em que o Reforja roda de verdade.
Serve para exercitar a aplicacao inteira **pela GUI** contra um sistema real,
alem do gate (ruff + pytest), pegando bugs que os testes stubados nao pegam.

## Uso

```fish
# 1. Constroi a imagem (provisiona o ambiente; o codigo NAO entra na imagem)
docker build -t reforja-test packaging/test-container

# 2. Roda a bateria completa montando o repo em /work
docker run --rm -v "$PWD":/work reforja-test bash packaging/test-container/run-all.sh

# Shell interativo dentro do container (para depurar)
docker run --rm -it -v "$PWD":/work reforja-test bash
```

## O que a bateria roda (`run-all.sh`)

1. `ruff check` + `ruff format --check`
2. `py_compile` de todo o codigo
3. `pytest` (motor compartilhado) + `pytest tests/test_gui.py` (GUI offscreen)
4. `gui_drive.py` — **driver funcional da GUI ponta a ponta** contra o sistema
   real do container: constroi a janela, navega por todas as secoes, abre a
   pagina de cada etapa e monta os `ItemCard`s, sonda o estado real, monta a
   previa consolidada (`BatchPreviewDialog` + `BatchProbeWorker`), e roda cada
   etapa pelo mesmo `StepWorker` (thread + sinais) que a interface usa — em
   `status` (real) e `apply` (dry-run), mais `undo` (dry-run) e a verificacao do
   modelo Flathub (instalado nao reinstala; `force_keys` reinstala).

O driver roda offscreen (`QT_QPA_PLATFORM=offscreen`), stuba a rede (releases do
GitHub) e nunca instala nada de verdade (execucao em dry-run). Sai != 0 com um
relatorio se qualquer fase falhar.
