"""Teste de instalacao REAL (opt-in): instala e remove um pacote leve de verdade.

Diferente do gui_drive.py (que roda em dry-run), este exercita o caminho real do
motor — `Runner.run(sudo=True)`, `install_system_package`, deteccao e
`remove_system_packages` — contra o gerenciador de pacotes da distro. Precisa de
REDE e de sudo (o container tem sudo sem senha). Fora do gate por isso.

Alvo: um pacote pequeno e inofensivo (`tree`), presente nas tres familias.
Sai != 0 se instalar/detectar/remover falhar.
"""

from __future__ import annotations

import sys
from pathlib import Path

from reforja.core import Logger, Runner, detect_user
from reforja.platform import detect_distro, install_system_package, remove_system_packages, system_installed

PKG = "tree"


def main() -> int:
    distro = detect_distro()
    print(f"== Instalacao real em {distro.id} (family={distro.family}) ==")
    if distro.immutable:
        print("sistema imutavel: pulando (instalacao nativa nao se aplica).")
        return 0

    logger = Logger(Path("/tmp/reforja-real"), "real-install")
    runner = Runner(logger, dry_run=False)
    _user = detect_user()

    ja_estava = system_installed(PKG)
    print(f"1. estado inicial de '{PKG}': {'instalado' if ja_estava else 'ausente'}")

    print(f"2. instalando '{PKG}' de verdade...")
    install_system_package(PKG, runner)
    if not system_installed(PKG):
        print(f"FALHOU: '{PKG}' nao foi detectado como instalado apos install_system_package")
        return 1
    print(f"   OK: '{PKG}' instalado e detectado")

    # So remove se fomos nos que instalamos (nao mexe no que ja existia).
    if not ja_estava:
        print(f"3. removendo '{PKG}' (limpeza)...")
        remove_system_packages([PKG], runner)
        if system_installed(PKG):
            print(f"FALHOU: '{PKG}' ainda detectado apos remove_system_packages")
            return 1
        print(f"   OK: '{PKG}' removido e nao mais detectado")
    else:
        print(f"3. '{PKG}' ja existia antes; deixando como estava.")

    print("== INSTALACAO REAL OK ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
