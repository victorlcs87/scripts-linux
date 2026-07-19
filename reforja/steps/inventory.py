from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import hardware
from ..core import (
    Color,
    badge,
    command_exists,
)
from ..platform import (
    install_system_or_aur,
    install_system_package,
)
from ..steps_base import Step, StepTask
from ._common import header


class HardwareStep(Step):
    id = "14"
    title = "Inventario de Hardware"
    description = (
        "Coleta um relatorio de hardware (CPU, RAM, GPUs, discos, PCI/USB, dmidecode/inxi) e salva "
        "em arquivo, para suporte e para outras etapas consultarem. Nao altera o sistema."
    )

    # O veredito e "existe relatorio recente?", nao a soma das tarefas.
    compliance_from_plan = False

    @property
    def report_file(self) -> Path:
        return hardware.report_path(self.ctx.user)

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="ferramentas",
                label="Instalar as ferramentas de coleta (dmidecode, inxi)",
                short_description="Instala dmidecode e inxi",
                description=(
                    "Instala dmidecode e inxi, que enriquecem o relatorio com dados da placa-mae, BIOS "
                    "e sensores. Sem elas o relatorio sai mais pobre, mas ainda funciona."
                ),
                detect=lambda: command_exists("dmidecode") and command_exists("inxi"),
                run=self._ensure_tools,
            ),
            StepTask(
                key="relatorio",
                label="Gerar o relatorio de hardware",
                short_description="Coleta CPU, RAM, GPU, discos e mais",
                description=(
                    f"Coleta CPU, RAM, GPUs, discos, PCI e USB e salva em {self.report_file}. "
                    "Nao altera nada no sistema; serve para suporte e para as outras etapas consultarem."
                ),
                stateless=True,
                detail="regera o relatorio",
                run=self._collect,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "Coletando informacoes de hardware e salvando relatorio")
        super().apply()

    def _collect(self) -> None:
        destino = hardware.collect_report(self.ctx)
        if self.ctx.runner.dry_run:
            self.mark_done("Coleta simulada (dry-run); nenhum relatorio gravado.")
            return
        facts = hardware.read_facts()
        self.ctx.logger.write("")
        for line in hardware.render_summary(facts):
            self.ctx.logger.write(line)
        self.ctx.logger.write("")
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} relatorio salvo em {destino}")
        self.add_hint(f"Compartilhe o arquivo quando precisar de suporte: {destino}")
        self.mark_done(f"Inventario coletado e salvo em {destino}.")
        self.mark_applied("Inventario de hardware coletado.", items=hardware.facts_summary(facts))

    def _ensure_tools(self) -> None:
        # Ferramentas opcionais que enriquecem o relatorio; nao travam se faltarem.
        if not command_exists("dmidecode"):
            install_system_package("dmidecode", self.ctx.runner)
        if not command_exists("inxi"):
            install_system_or_aur("inxi", "inxi", self.ctx.runner)

    def status(self) -> None:
        header(self, self.title, "Verificando ultimo inventario de hardware coletado")
        destino = self.report_file
        if not destino.exists():
            self.ctx.logger.write(f"Nenhum inventario coletado ainda em {destino}")
            self.mark_pending("Nenhum inventario de hardware foi coletado ainda.", missing=[str(destino)])
            return
        mtime = datetime.fromtimestamp(destino.stat().st_mtime)
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} relatorio em {destino}")
        self.ctx.logger.write(f"Ultima coleta: {mtime:%Y-%m-%d %H:%M:%S}")
        facts = hardware.read_facts()
        for line in hardware.facts_summary(facts):
            self.ctx.logger.write(f"  - {line}")
        self.mark_applied(f"Inventario disponivel (coletado em {mtime:%Y-%m-%d %H:%M}).")

    def undo(self) -> None:
        destino = self.report_file
        if self.ctx.runner.dry_run:
            self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {destino}")
            return
        if destino.exists():
            self.ctx.runner.run(
                ["rm", "-f", str(destino)], check=False, action="Removendo inventario de hardware", show_progress=False
            )
        else:
            self.ctx.logger.write(f"Nada para remover; {destino} nao existe.")
