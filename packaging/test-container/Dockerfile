# Container de teste do Reforja: um Arch limpo (alvo primario: CachyOS/Arch) com
# Python, Qt6/PySide6 e as dependencias de dev, rodando como usuario nao-root com
# sudo — o mesmo shape em que o Reforja roda de verdade.
#
# O codigo NAO e copiado para a imagem: monta-se o repo em /work na hora de rodar,
# para iterar sem rebuildar. A imagem so provisiona o ambiente.
#
#   docker build -t reforja-test packaging/test-container
#   docker run --rm -v "$PWD":/work reforja-test bash packaging/test-container/run-all.sh
FROM archlinux:latest

# Camada de sistema: Python, git/sudo e as libs compartilhadas que o PySide6
# (wheel do PyPI, que embute o Qt6) carrega em runtime, mesmo offscreen.
RUN pacman -Syu --noconfirm --needed \
        python python-pip git sudo which \
        mesa libglvnd libxkbcommon libxkbcommon-x11 \
        fontconfig freetype2 glib2 dbus \
        libxcb xcb-util-cursor \
        noto-fonts \
    && pacman -Scc --noconfirm

# Deps Python: PySide6 (GUI) + runtime (InquirerPy) + dev (pytest/ruff).
# --break-system-packages porque o Arch marca o Python do sistema como
# externally-managed; e um container descartavel.
RUN pip install --break-system-packages --no-cache-dir \
        PySide6 InquirerPy pytest pytest-cov ruff certifi

# Usuario nao-root com sudo sem senha: o Reforja roda como usuario comum e escala
# via sudo. Reproduz esse ambiente (rodar como root mascara bugs de permissao).
RUN useradd -m -s /bin/bash tester \
    && echo 'tester ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/tester \
    && chmod 0440 /etc/sudoers.d/tester

USER tester
WORKDIR /work
# GUI headless e sem checagem de atualizacao (nao abrir thread de rede nos testes).
ENV QT_QPA_PLATFORM=offscreen \
    REFORJA_NO_UPDATE_CHECK=1 \
    PYTHONUNBUFFERED=1
CMD ["bash"]
