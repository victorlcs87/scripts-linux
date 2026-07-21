from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from ..core import (
    Color,
    PromptInterruptedError,
    backup_existing,
    badge,
    capture,
    command_exists,
    ensure_owner,
    paint,
    print_lines,
    prompt_user,
    select_many,
    write_text,
)
from ..desktop import DesktopEntry, install_desktop_entry
from ..installers import fetch_json
from ..platform import (
    current_distro,
    ensure_antigravity_repo,
    ensure_github_cli,
    install_system_package,
    remove_system_packages,
    system_installed,
)
from ..steps_base import Step, StepTask
from ._common import header


class GitStep(Step):
    id = "06"
    title = "Git / GitHub"
    description = (
        "Instala o Git e o GitHub CLI (gh), conecta sua conta pelo navegador (o gh cria/envia a chave SSH) "
        "e adiciona repositorios: clona em ~/repositorios e ja configura o autor dos commits."
    )

    @property
    def ssh_dir(self) -> Path:
        return self.ctx.user.home / ".ssh"

    @property
    def config_file(self) -> Path:
        return self.ssh_dir / "config"

    def _key_path(self, alias: str) -> Path:
        return self.ssh_dir / f"id_ed25519_{alias}"

    # Rotulos do menu principal (a ordem define a ordem de execucao).
    _ACTIONS = (
        "Instalar Git + GitHub CLI (gh)",
        "Conectar uma conta GitHub (login + alias SSH da conta)",
        "Adicionar um repositorio (clonar + configurar autor)",
        "Gerenciar contas e aliases SSH (adicionar, renomear, remover)",
    )

    # O veredito vem do status() proprio (ferramentas + contas + repos registrados).
    compliance_from_plan = False

    def tasks(self) -> list[StepTask]:
        return [
            StepTask(
                key="ferramentas",
                label=self._ACTIONS[0],
                short_description="Instala git e GitHub CLI (gh)",
                description=(
                    "Instala o git e o GitHub CLI (gh), que e quem faz o login pelo navegador e cuida "
                    "da chave SSH por voce."
                ),
                detect=lambda: command_exists("git") and command_exists("gh"),
                run=self._install_tooling,
            ),
            StepTask(
                key="conta",
                label=self._ACTIONS[1],
                short_description="Login no GitHub + chave SSH",
                description=(
                    "Faz o login no GitHub pelo navegador, gera uma chave SSH dedicada para a conta, "
                    "envia a chave para o GitHub e cria um alias no ~/.ssh/config (permite ter mais de "
                    "uma conta na mesma maquina)."
                ),
                detect=self._accounts_detail,
                run=self._login_account,
            ),
            StepTask(
                key="repos",
                label=self._ACTIONS[2],
                short_description="Clona um repo e configura o autor",
                description=(
                    "Clona um repositorio da sua conta em ~/repositorios e ja configura nome e e-mail "
                    "do autor dos commits naquele repositorio. Pode repetir para varios."
                ),
                stateless=True,
                detail="adiciona mais um repositorio",
                run=self._add_repos,
            ),
            StepTask(
                key="gerenciar",
                label=self._ACTIONS[3],
                short_description="Varias contas/alias na mesma maquina",
                description=(
                    "Abre um menu para gerenciar suas contas GitHub e aliases SSH: adicionar uma "
                    "segunda conta (ex: pessoal e trabalho), trocar a conta ativa do gh, renomear "
                    "ou remover um alias SSH ja criado."
                ),
                stateless=True,
                detail="adicionar/renomear/remover contas e alias",
                run=self._manage_accounts,
            ),
        ]

    def apply(self) -> None:
        header(self, self.title, "GitHub facil: instalar o gh, conectar a conta e clonar repositorios")
        super().apply()
        # O compliance vem do status() completo (contas logadas + repos registrados).
        self._evaluate_compliance()

    def _accounts_detail(self) -> str | bool:
        contas = self._logged_accounts()
        return f"conectado: {', '.join(contas)}" if contas else False

    def _report_failure(self, acao: str, exc: Exception) -> None:
        self.ctx.logger.write(f"{badge('erro', Color.ERROR)} ao {acao}: {type(exc).__name__}: {exc}")
        self.add_hint(f"Falha ao {acao}: {exc}")

    # ------------------------------------------------------------------ gh helpers

    def _gh_query(self, args: list[str], *, merge_stderr: bool = False) -> str:
        """Roda um comando `gh` de leitura pura e devolve a saida (ou "").

        Usa capture() (fora do Runner) porque e leitura, roda mesmo em dry-run.
        `gh auth status` escreve em stderr em algumas versoes, dai o merge opcional.
        """
        if not command_exists("gh"):
            return ""
        proc = capture(["gh", *args])
        out = proc.stdout or ""
        if merge_stderr:
            out = out + "\n" + (proc.stderr or "")
        elif proc.returncode != 0:
            return ""
        return out.strip()

    def _gh_user_field(self, field: str) -> str:
        return self._gh_query(["api", "user", "--jq", f'.{field} // ""'])

    def _logged_accounts(self) -> list[str]:
        """Contas GitHub logadas no gh, extraidas de `gh auth status`."""
        text = self._gh_query(["auth", "status"], merge_stderr=True)
        if not text:
            return []
        nomes: list[str] = []
        # Formato novo: "Logged in to github.com account <nome>"; antigo: "... as <nome>".
        for m in re.finditer(r"account (\S+)", text):
            nomes.append(m.group(1).strip("()"))
        if not nomes:
            for m in re.finditer(r"Logged in to \S+ as (\S+)", text):
                nomes.append(m.group(1).strip("()"))
        vistos: set[str] = set()
        unicos: list[str] = []
        for n in nomes:
            if n not in vistos:
                vistos.add(n)
                unicos.append(n)
        return unicos

    # ------------------------------------------------------------------ acoes

    def _install_tooling(self) -> None:
        install_system_package("git", self.ctx.runner)
        if not ensure_github_cli(self.ctx.runner):
            self.add_hint("GitHub CLI nao instalado automaticamente; veja as instrucoes acima.")

    def _login_account(self) -> str:
        if not command_exists("gh") and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} gh nao encontrado. Rode antes 'Instalar Git + GitHub CLI'."
            )
            self.add_hint("Instale o GitHub CLI antes de conectar a conta.")
            return ""
        self.ctx.logger.write(
            paint(
                "O gh vai mostrar um codigo de uso unico e abrir o navegador para autorizar. "
                "A chave SSH desta conta e criada e enviada logo depois, automaticamente.",
                Color.MUTED,
            )
        )
        # Passamos as respostas por flag para NAO cair nos menus de setas do gh
        # ("Where do you use GitHub?", protocolo, etc.), que travam no terminal.
        # --skip-ssh-key porque a chave/alias sao criados por nos em _ensure_account_alias.
        self.ctx.runner.run(
            ["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "ssh", "--skip-ssh-key", "--web"],
            check=False,
            interactive=True,
            interactive_tty=True,
            manual_message="Login pelo navegador: copie o codigo, tecle Enter para abrir o navegador e autorize. Nao e travamento.",
        )
        # Configura o git para usar o gh como credential helper (clones HTTPS).
        self.ctx.runner.run(["gh", "auth", "setup-git"], check=False, action="Configurando git para usar o gh")
        if self.ctx.runner.dry_run:
            return "Login do GitHub (dry-run)."
        conta = self._gh_user_field("login")
        if not conta:
            return "Login do GitHub executado."
        self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} conta ativa: {conta}")
        # No mesmo fluxo, ja cria o alias SSH dedicado dessa conta (chave + bloco Host).
        alias = self._ensure_account_alias(conta)
        if alias:
            return f"Conta '{conta}' conectada (alias SSH '{alias}')."
        return f"Conta '{conta}' conectada."

    def _add_repos(self) -> list[str]:
        adicionados: list[str] = []
        aliases = self._configured_aliases()
        while True:
            alias = self._maybe_pick_alias(aliases)
            if not alias:
                self._maybe_switch_account()
            spec = self._choose_repo_spec()
            if not spec:
                break
            target = self._clone_repo(spec, alias=alias)
            if target is not None:
                owner, repo = self._parse_owner_repo(spec)
                name, email = self._configure_author(target)
                self._record_repo(target, owner, repo, name, email, account=alias)
                adicionados.append(target.name)
            try:
                mais = (
                    prompt_user(
                        "Adicionar outro repositorio? (s/N)",
                        self.ctx.logger,
                        prompt_label="Outro",
                        allow_empty=True,
                    )
                    .strip()
                    .lower()
                )
            except PromptInterruptedError:
                break
            if mais not in ("s", "sim", "y", "yes"):
                break
        return adicionados

    # ------------------------------------------------------------------ gerenciar contas/alias

    def _manage_accounts(self) -> str:
        if not command_exists("gh") and not self.ctx.runner.dry_run:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} gh nao encontrado. Rode antes 'Instalar Git + GitHub CLI'."
            )
            self.add_hint("Instale o GitHub CLI antes de gerenciar contas.")
            return ""
        feito: list[str] = []
        while True:
            contas = self._logged_accounts()
            aliases = self._configured_aliases()
            ativa = self._gh_user_field("login")
            self._print_accounts_overview(contas, ativa, aliases)

            opcoes: list[str] = ["Adicionar outra conta GitHub (login + alias SSH)"]
            acoes: list[tuple[str, str]] = [("add", "")]
            if len(contas) > 1:
                opcoes.append("Trocar a conta ativa do gh")
                acoes.append(("switch", ""))
            for a in aliases:
                opcoes.append(f"Renomear alias SSH: {a}")
                acoes.append(("rename", a))
            for a in aliases:
                opcoes.append(f"Remover alias SSH (chave + bloco no ~/.ssh/config): {a}")
                acoes.append(("remove", a))

            idx = select_many(
                "O que voce quer fazer?",
                opcoes,
                self.ctx.logger,
                detail="Marque UMA acao. Nada marcado = encerrar.",
            )
            if not idx:
                break
            kind, val = acoes[idx[0]]
            if kind == "add":
                if self._login_account():
                    feito.append("conta adicionada")
            elif kind == "switch":
                self._maybe_switch_account()
                feito.append("conta ativa trocada")
            elif kind == "rename":
                novo = self._rename_alias(val)
                if novo:
                    feito.append(f"alias {val} -> {novo}")
            elif kind == "remove":
                if self._confirm_remove_alias(val) and self._remove_account(val):
                    feito.append(f"alias {val} removido")
            try:
                mais = (
                    prompt_user(
                        "Fazer outra alteracao nas contas/aliases? (s/N)",
                        self.ctx.logger,
                        prompt_label="Continuar",
                        allow_empty=True,
                    )
                    .strip()
                    .lower()
                )
            except PromptInterruptedError:
                break
            if mais not in ("s", "sim", "y", "yes"):
                break
        if feito:
            return "Gerenciamento: " + "; ".join(feito) + "."
        return "Nenhuma alteracao nas contas/aliases."

    def _print_accounts_overview(self, contas: list[str], ativa: str, aliases: list[str]) -> None:
        linhas = ["", paint("===== CONTAS E ALIASES =====", Color.ACCENT)]
        if contas:
            for c in contas:
                marca = "  (ativa)" if c == ativa else ""
                linhas.append(f"{badge('conta', Color.SUCCESS)} {c}{marca}")
        else:
            linhas.append("Nenhuma conta GitHub conectada ainda.")
        if aliases:
            for a in aliases:
                linhas.append(f"{badge('alias', Color.INFO)} {a}  (git@{a}:owner/repo.git)")
        else:
            linhas.append("Nenhum alias SSH configurado ainda.")
        print_lines(self.ctx.logger, linhas)

    def _confirm_remove_alias(self, alias: str) -> bool:
        try:
            resp = (
                prompt_user(
                    f"Remover o alias '{alias}'? Apaga a chave SSH e o bloco Host (s/N)",
                    self.ctx.logger,
                    detail="Repositorios ja clonados com este alias precisarao de 'git remote set-url'.",
                    prompt_label="Confirmar",
                    allow_empty=True,
                )
                .strip()
                .lower()
            )
        except PromptInterruptedError:
            return False
        return resp in ("s", "sim", "y", "yes")

    def _rename_alias(self, old: str) -> str:
        """Renomeia um alias SSH: chaves id_ed25519_<old> -> <new> e bloco Host."""
        try:
            new = prompt_user(
                f"Novo nome para o alias '{old}'",
                self.ctx.logger,
                detail="Ex: github-trabalho. Vazio = cancelar.",
                prompt_label="Novo alias",
                allow_empty=True,
            ).strip()
        except PromptInterruptedError:
            return ""
        if not new or new == old:
            return ""
        new_key = self._key_path(new)
        existing_config = self.config_file.read_text(encoding="utf-8") if self.config_file.exists() else ""
        if new_key.exists() or f"Host {new}" in existing_config:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} ja existe um alias '{new}'. Escolha outro nome.")
            return ""
        old_key = self._key_path(old)
        for suffix in ("", ".pub"):
            src = Path(str(old_key) + suffix)
            dst = Path(str(new_key) + suffix)
            if src.exists():
                self.ctx.runner.run(
                    ["mv", str(src), str(dst)],
                    action=f"Renomeando {src.name} -> {dst.name}",
                    show_progress=False,
                )
        # Reescreve o ~/.ssh/config: remove o bloco antigo e cria o novo apontando p/ a chave renomeada.
        if existing_config:
            backup_existing(self.config_file, self.ctx.runner)
            write_text(self.config_file, self._strip_host_block(existing_config, old), self.ctx.runner, mode=0o600)
        self._write_host_block(new, new_key)
        # Atualiza os repos registrados que usavam o alias antigo.
        state = self._load_state()
        mudou = False
        for r in state.get("repos", []):
            if r.get("account") == old:
                r["account"] = new
                mudou = True
        if mudou:
            self._save_state(state)
        self.ctx.logger.write(
            paint(
                f"Alias renomeado. Repos ja clonados com o alias antigo: "
                f"git remote set-url origin git@{new}:OWNER/REPO.git",
                Color.MUTED,
            )
        )
        return new

    def _maybe_switch_account(self) -> None:
        contas = self._logged_accounts()
        if len(contas) <= 1:
            return
        idx = select_many(
            "Qual conta GitHub usar para este repositorio?",
            contas,
            self.ctx.logger,
            detail="Marque UMA conta. Ela vira a conta ativa para o clone.",
        )
        if idx:
            escolhida = contas[idx[0]]
            self.ctx.runner.run(
                ["gh", "auth", "switch", "--hostname", "github.com", "--user", escolhida],
                check=False,
                action=f"Ativando a conta {escolhida}",
            )

    def _choose_repo_spec(self) -> str:
        """Deixa o usuario escolher um repo da lista do gh ou digitar owner/repo/URL."""
        repos = self._list_own_repos()
        if repos:
            labels = [*repos, "Outro (digitar owner/repo ou URL)"]
            idx = select_many(
                "Qual repositorio clonar?",
                labels,
                self.ctx.logger,
                detail="Marque UM. A ultima opcao permite digitar manualmente. Nada marcado = encerrar.",
            )
            if not idx:
                return ""
            escolha = idx[0]
            if escolha < len(repos):
                return repos[escolha]
        try:
            spec = prompt_user(
                "Repositorio (owner/repo ou URL, vazio para encerrar)",
                self.ctx.logger,
                detail="Ex: victorlcs87/gsv-calendar ou git@github.com:owner/repo.git",
                prompt_label="Repo",
                allow_empty=True,
            ).strip()
        except PromptInterruptedError:
            return ""
        return spec

    def _list_own_repos(self) -> list[str]:
        raw = self._gh_query(["repo", "list", "--limit", "100", "--json", "nameWithOwner"])
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [item["nameWithOwner"] for item in data if isinstance(item, dict) and item.get("nameWithOwner")]

    def _clone_repo(self, spec: str, *, alias: str = "") -> Path | None:
        base = self.ctx.user.home / "repositorios"
        if not base.exists():
            self.ctx.runner.run(["mkdir", "-p", str(base)], action=f"Criando diretorio {base}", show_progress=False)
        owner, repo = self._parse_owner_repo(spec)
        target = base / (repo or self._repo_dir_name(spec))
        if (target / ".git").exists():
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} {target} ja existe; atualizando")
            self.ctx.runner.run(
                ["git", "-C", str(target), "pull", "--ff-only"],
                check=False,
                action=f"Atualizando {target.name}",
            )
            return target
        # Com alias SSH: clona via git@<alias>:owner/repo.git para fixar a conta certa.
        # Sem alias: usa o gh (conta ativa) resolvendo owner/repo ou URL.
        if alias and owner and repo:
            ssh_url = f"git@{alias}:{owner}/{repo}.git"
            self.ctx.runner.run(
                ["git", "clone", ssh_url, str(target)],
                check=False,
                action=f"Clonando {ssh_url}",
            )
        else:
            self.ctx.runner.run(
                ["gh", "repo", "clone", spec, str(target)],
                check=False,
                action=f"Clonando {spec}",
            )
        if self.ctx.runner.dry_run:
            return target
        return target if (target / ".git").exists() else None

    def _configured_aliases(self) -> list[str]:
        """Aliases SSH ja criados (a partir das chaves id_ed25519_<alias>)."""
        if not self.ssh_dir.exists():
            return []
        return sorted(pub.name[len("id_ed25519_") : -len(".pub")] for pub in self.ssh_dir.glob("id_ed25519_*.pub"))

    def _maybe_pick_alias(self, aliases: list[str]) -> str:
        if not aliases:
            return ""
        labels = [f"{a}  (git@{a}:owner/repo.git)" for a in aliases]
        labels.append("Nenhum (usar a conta ativa do gh)")
        idx = select_many(
            "Clonar usando qual alias SSH?",
            labels,
            self.ctx.logger,
            detail="Aliases separam contas: cada um usa a chave certa. Marque UM ou 'Nenhum'.",
        )
        if not idx:
            return ""
        escolha = idx[0]
        return aliases[escolha] if escolha < len(aliases) else ""

    @staticmethod
    def _repo_dir_name(spec: str) -> str:
        _, repo = GitStep._parse_owner_repo(spec)
        return repo or spec.strip().replace("/", "-")

    @staticmethod
    def _parse_owner_repo(spec: str) -> tuple[str, str]:
        s = spec.strip().rstrip("/")
        if s.endswith(".git"):
            s = s[:-4]
        for prefix in ("git@github.com:", "https://github.com/", "http://github.com/", "ssh://git@github.com/"):
            if s.startswith(prefix):
                s = s[len(prefix) :]
                break
        parts = [p for p in s.replace(":", "/").split("/") if p]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return "", parts[-1] if parts else ""

    def _configure_author(self, target: Path) -> tuple[str, str]:
        nome_default = self._gh_user_field("name") or self._gh_user_field("login")
        email_default = self._gh_user_field("email")
        try:
            nome = (
                prompt_user(
                    "Nome para os commits deste repo",
                    self.ctx.logger,
                    detail=f"Enter para usar: {nome_default or '(vazio)'}",
                    prompt_label="Nome",
                    allow_empty=True,
                ).strip()
                or nome_default
            )
            email = (
                prompt_user(
                    "Email para os commits deste repo",
                    self.ctx.logger,
                    detail=f"Enter para usar: {email_default or '(vazio)'}",
                    prompt_label="Email",
                    allow_empty=True,
                ).strip()
                or email_default
            )
        except PromptInterruptedError:
            self.add_hint(f"Autor do commit nao configurado para {target.name}.")
            return "", ""
        if nome:
            self.ctx.runner.run(
                ["git", "-C", str(target), "config", "user.name", nome],
                check=False,
                action=f"Configurando user.name em {target.name}",
                show_progress=False,
            )
        if email:
            self.ctx.runner.run(
                ["git", "-C", str(target), "config", "user.email", email],
                check=False,
                action=f"Configurando user.email em {target.name}",
                show_progress=False,
            )
        return nome, email

    # ------------------------------------------------------------------ alias SSH por conta

    def _ensure_ssh_dir(self) -> None:
        if self.ctx.runner.dry_run:
            return
        self.ssh_dir.mkdir(parents=True, exist_ok=True)
        self.ssh_dir.chmod(0o700)

    def _account_block(self, alias: str, key: Path) -> str:
        return (
            f"\nHost {alias}\n    HostName github.com\n    User git\n    IdentityFile {key}\n    IdentitiesOnly yes\n"
        )

    def _write_host_block(self, alias: str, key: Path) -> None:
        existing = self.config_file.read_text(encoding="utf-8") if self.config_file.exists() else ""
        if f"Host {alias}" in existing:
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} bloco 'Host {alias}' ja existe no ~/.ssh/config")
            return
        backup_existing(self.config_file, self.ctx.runner)
        write_text(self.config_file, existing + self._account_block(alias, key), self.ctx.runner, mode=0o600)

    def _upload_key_via_gh(self, alias: str, key: Path, conta: str) -> bool:
        if not conta or not command_exists("gh"):
            return False
        pub = key.with_suffix(".pub")
        if not pub.exists() and not self.ctx.runner.dry_run:
            return False
        res = self.ctx.runner.run(
            ["gh", "ssh-key", "add", str(pub), "--title", f"reforja-{alias}"],
            check=False,
            action=f"Enviando a chave de '{alias}' para a conta {conta}",
        )
        if self.ctx.runner.dry_run:
            return True
        return bool(res) and getattr(res, "returncode", 1) == 0

    def _print_pubkey_instructions(self, alias: str, key: Path) -> None:
        pub = key.with_suffix(".pub")
        conteudo = pub.read_text(encoding="utf-8").strip() if pub.exists() else "(chave publica nao encontrada)"
        print_lines(
            self.ctx.logger,
            [
                "",
                paint("Envie esta chave publica para o GitHub da conta certa:", Color.SUCCESS),
                "",
                conteudo,
                "",
                "  1) Acesse https://github.com/settings/keys e clique em 'New SSH Key'.",
                "  2) Cole a chave acima e salve.",
                f"  3) Teste a conexao: ssh -T git@{alias}",
                "",
            ],
        )

    def _ensure_account_alias(self, login: str) -> str:
        """Cria (ou reaproveita) o alias SSH dedicado da conta recem-conectada.

        Chamado logo apos o login: a conta `login` ja esta ativa no gh, entao a
        chave e enviada direto para ela. Retorna o nome do alias configurado.
        """
        alias_default = f"github-{login}"
        try:
            alias = (
                prompt_user(
                    "Nome do alias SSH desta conta",
                    self.ctx.logger,
                    detail=f"Enter para usar: {alias_default} (ou digite um nome memoravel, ex: github-trabalho)",
                    prompt_label="Alias",
                    allow_empty=True,
                ).strip()
                or alias_default
            )
        except PromptInterruptedError:
            self.add_hint("Alias SSH nao configurado (entrada interrompida).")
            return ""
        self._ensure_ssh_dir()
        key = self._key_path(alias)
        if key.exists():
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} chave {key.name} ja existe; reaproveitando")
        else:
            email = self._gh_user_field("email")
            if not email:
                try:
                    email = prompt_user(
                        "Email da conta (comentario da chave)",
                        self.ctx.logger,
                        prompt_label="Email",
                        allow_empty=True,
                    ).strip()
                except PromptInterruptedError:
                    email = ""
            self.ctx.runner.run(
                ["ssh-keygen", "-t", "ed25519", "-C", email or alias, "-f", str(key), "-N", ""],
                action=f"Gerando chave SSH ed25519 para '{alias}'",
            )
        self._write_host_block(alias, key)
        self.ctx.runner.run(
            ["ssh-add", str(key)],
            check=False,
            action=f"Adicionando a chave de '{alias}' ao ssh-agent",
        )
        # A conta `login` ja esta ativa apos o `gh auth login`, entao a chave vai para ela.
        if not self._upload_key_via_gh(alias, key, login):
            self._print_pubkey_instructions(alias, key)
        self.ctx.logger.write(
            paint(f"Pronto! Para clonar com esta conta: git clone git@{alias}:OWNER/REPO.git", Color.ACCENT)
        )
        return alias

    def _remove_account(self, alias: str) -> bool:
        key = self._key_path(alias)
        pub = key.with_suffix(".pub")
        removeu = False
        for path in (key, pub):
            if path.exists():
                self.ctx.runner.run(["rm", "-f", str(path)], action=f"Removendo {path.name}", show_progress=False)
                removeu = True
        if self.config_file.exists():
            content = self.config_file.read_text(encoding="utf-8")
            new_content = self._strip_host_block(content, alias)
            if new_content != content:
                backup_existing(self.config_file, self.ctx.runner)
                write_text(self.config_file, new_content, self.ctx.runner, mode=0o600)
                removeu = True
        return removeu

    @staticmethod
    def _strip_host_block(content: str, alias: str) -> str:
        lines = content.splitlines(keepends=True)
        result: list[str] = []
        skip = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Host "):
                hosts = stripped.split()[1:]
                skip = alias in hosts
                if skip:
                    continue
            elif skip and stripped == "":
                continue
            elif skip and not line.startswith((" ", "\t")):
                skip = False
            if not skip:
                result.append(line)
        return "".join(result)

    # ------------------------------------------------------------------ estado

    def _state_path(self) -> Path:
        return self.ctx.user.home / ".config" / "reforja" / "git.json"

    def _load_state(self) -> dict:
        path = self._state_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("repos", [])
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"repos": []}

    def _save_state(self, state: dict) -> None:
        write_text(
            self._state_path(),
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            self.ctx.runner,
        )

    def _record_repo(self, target: Path, owner: str, repo: str, name: str, email: str, *, account: str = "") -> None:
        state = self._load_state()
        entry = {
            "path": str(target),
            "owner": owner,
            "repo": repo or target.name,
            "name": name,
            "email": email,
            "account": account,
        }
        repos = [r for r in state.get("repos", []) if r.get("path") != entry["path"]]
        repos.append(entry)
        state["repos"] = repos
        self._save_state(state)

    def _forget_repo(self, path: str) -> None:
        state = self._load_state()
        state["repos"] = [r for r in state.get("repos", []) if r.get("path") != path]
        self._save_state(state)

    # ------------------------------------------------------------------ status / undo

    def status(self) -> None:
        header(self, self.title, "GitHub CLI, contas conectadas e repositorios configurados")
        self.ctx.runner.run(["git", "--version"], check=False)
        gh_ok = command_exists("gh")
        if gh_ok:
            self.ctx.runner.run(["gh", "--version"], check=False, show_progress=False)
            self.ctx.runner.run(["gh", "auth", "status"], check=False, action="Contas GitHub conectadas")
        else:
            self.ctx.logger.write(f"{badge('aviso', Color.WARNING)} GitHub CLI (gh) nao instalado.")

        repos = self._load_state().get("repos", [])
        self.ctx.logger.write("")
        self.ctx.logger.write(paint("===== REPOSITORIOS CONFIGURADOS =====", Color.ACCENT))
        repos_ok: list[dict] = []
        if repos:
            for r in repos:
                path = Path(r.get("path", ""))
                existe = (path / ".git").exists()
                tone = Color.SUCCESS if existe else Color.WARNING
                sufixo = "" if existe else "  (pasta ausente)"
                autor = r.get("email") or r.get("name") or ""
                self.ctx.logger.write(f"{badge(r.get('owner') or '?', tone)} {path} <{autor}>{sufixo}")
                if existe:
                    repos_ok.append(r)
        else:
            self.ctx.logger.write("Nenhum repositorio configurado ainda.")

        self._status_legacy_ssh()
        self._evaluate_compliance()

    def _evaluate_compliance(self) -> None:
        gh_ok = command_exists("gh")
        contas = self._logged_accounts() if gh_ok else []
        repos_ok = [r for r in self._load_state().get("repos", []) if (Path(r.get("path", "")) / ".git").exists()]
        if gh_ok and contas and repos_ok:
            self.mark_applied(
                f"gh instalado, {len(contas)} conta(s) e {len(repos_ok)} repo(s) configurados.",
                items=[r.get("repo", "?") for r in repos_ok],
            )
        elif gh_ok and contas:
            self.mark_applied("gh instalado e conta conectada; adicione repositorios quando quiser.")
        elif gh_ok:
            self.mark_pending("gh instalado, mas nenhuma conta GitHub conectada.", missing=["conta GitHub"])
        else:
            self.mark_pending(
                "GitHub CLI nao instalado e nenhuma conta conectada.",
                missing=["GitHub CLI (gh)", "conta GitHub"],
            )

    def _status_legacy_ssh(self) -> None:
        keys = sorted(self.ssh_dir.glob("id_ed25519_*.pub")) if self.ssh_dir.exists() else []
        if not keys and not self.config_file.exists():
            return
        self.ctx.logger.write("")
        self.ctx.logger.write(paint("===== LEGADO (chaves SSH do fluxo antigo) =====", Color.MUTED))
        if self.config_file.exists():
            print_lines(self.ctx.logger, self.config_file.read_text(encoding="utf-8").splitlines())
        for pub in keys:
            alias = pub.name[len("id_ed25519_") : -len(".pub")]
            self.ctx.logger.write(f"{badge(alias, Color.INFO)} {pub}")

    def undo(self) -> None:
        header(self, self.title, "Desconectar conta, esquecer repositorio ou remover chave antiga")
        contas = self._logged_accounts() if command_exists("gh") else []
        repos = self._load_state().get("repos", [])
        legacy = sorted(self.ssh_dir.glob("id_ed25519_*.pub")) if self.ssh_dir.exists() else []

        opcoes: list[str] = []
        acoes: list[tuple[str, str]] = []
        for c in contas:
            opcoes.append(f"Desconectar conta GitHub: {c}")
            acoes.append(("logout", c))
        for r in repos:
            opcoes.append(f"Esquecer repositorio (mantem os arquivos): {r.get('path')}")
            acoes.append(("forget", str(r.get("path"))))
        for pub in legacy:
            alias = pub.name[len("id_ed25519_") : -len(".pub")]
            opcoes.append(f"Remover chave SSH antiga: {alias}")
            acoes.append(("legacy", alias))

        if not opcoes:
            self.mark_skipped("Nada configurado para desfazer.")
            return
        idx = select_many(
            "O que voce quer remover?",
            opcoes,
            self.ctx.logger,
            detail="Marque um ou mais. Nada marcado = cancelar. 'Esquecer' nao apaga os arquivos do repo.",
        )
        if not idx:
            self.mark_skipped("Nada selecionado.")
            return
        feito: list[str] = []
        for i in idx:
            kind, val = acoes[i]
            if kind == "logout":
                self.ctx.runner.run(
                    ["gh", "auth", "logout", "--hostname", "github.com", "--user", val],
                    check=False,
                    action=f"Desconectando a conta {val}",
                )
                feito.append(f"conta {val}")
            elif kind == "forget":
                self._forget_repo(val)
                feito.append(f"repo {Path(val).name}")
            elif kind == "legacy" and self._remove_account(val):
                feito.append(f"chave {val}")
        if feito:
            self.mark_done("Removido: " + ", ".join(feito))
        else:
            self.mark_skipped("Nada removido.")


class AntigravityStep(Step):
    id = "12"
    title = "Google Antigravity IDE"
    description = (
        "Instala e mantem atualizado o Google Antigravity IDE: tarball oficial com auto-update "
        "(Arch/imutaveis) ou repositorio nativo apt/dnf (Debian/Fedora)."
    )
    # Endpoint oficial de auto-update (protocolo estilo VS Code): informa a ultima
    # versao estavel do IDE, a URL do tarball e o sha256 para verificacao de integridade.
    update_api = "https://antigravity-auto-updater-974169037036.us-central1.run.app/api/update/linux-x64/stable/latest"
    # Fallback usado apenas se a API estiver indisponivel.
    url = "https://storage.googleapis.com/antigravity-public/antigravity-hub/2.0.6-5413878570549248/linux-x64/Antigravity.tar.gz"
    version = "2.0.6"
    package = "antigravity"

    def _use_native(self) -> bool:
        distro = current_distro()
        return distro.family in ("debian", "fedora") and not distro.immutable

    def tasks(self) -> list[StepTask]:
        if self._use_native():
            mgr = "apt" if current_distro().is_debian else "dnf"
            description = (
                f"Adiciona o repositorio oficial do Google e instala/atualiza o pacote antigravity pelo {mgr}."
            )
        else:
            description = (
                "Baixa o tarball oficial do Antigravity (checando o sha256), instala em ~/Antigravity IDE, "
                "cria o atalho no menu de aplicativos e o comando 'antigravity-ide' no terminal. "
                "Rodar de novo atualiza para a ultima versao."
            )
        return [
            StepTask(
                key="antigravity",
                label="Instalar/atualizar o Antigravity IDE",
                short_description="IDE do Google Antigravity (tarball oficial)",
                description=description,
                detect=self._detect_installed,
                run=self._install,
                remove=self._remove,
            )
        ]

    def _remove(self) -> None:
        """Remove o Antigravity: pacote nativo, ou o dir de instalacao + atalho + wrapper."""
        header(self, "Remover Antigravity IDE")
        if self._use_native():
            if system_installed(self.package):
                remove_system_packages([self.package], self.ctx.runner)
            return
        home = self.ctx.user.home
        targets = [
            self._install_dir,
            home / ".local/share/applications/antigravity-ide.desktop",
            home / ".local/bin/antigravity-ide",
        ]
        for path in targets:
            flag = "-rf" if path == self._install_dir else "-f"
            self.ctx.runner.run(
                ["rm", flag, str(path)],
                check=False,
                show_progress=False,
                action=f"Removendo {path}",
            )

    def _detect_installed(self) -> str | bool:
        if self._use_native():
            return "instalado via pacote nativo" if system_installed(self.package) else False
        installed = self._installed_version()
        if installed:
            return f"instalado (versao {installed})"
        # Sem marcador de versao, mas a instalacao pode existir (versao antiga do
        # reforja, instalacao manual, ou update que nao chegou a gravar o marcador).
        # Checamos o executavel real e o wrapper na home do usuario REAL (nao via
        # PATH: sob sudo o ~/.local/bin do usuario nem estaria no PATH).
        wrapper = self.ctx.user.home / ".local/bin/antigravity-ide"
        if wrapper.exists() or (self._install_dir.exists() and self._find_executable(self._install_dir)):
            return "instalado (versao desconhecida)"
        return False

    def _install(self) -> None:
        if self._use_native():
            self._apply_native()
        else:
            self._apply_tarball()

    # ---- caminho nativo (Debian/Fedora mutaveis) -----------------------------

    def _apply_native(self) -> None:
        distro = current_distro()
        mgr = "apt" if distro.is_debian else "dnf"
        header(self, self.title, f"Instalando/atualizando via repositorio nativo ({mgr})")
        ensure_antigravity_repo(self.ctx.runner)
        if distro.is_debian:
            self.ctx.runner.run(
                ["apt-get", "update"],
                sudo=True,
                action="Atualizando indice de pacotes apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo.",
            )
            self.ctx.runner.run(
                ["apt-get", "install", "-y", self.package],
                sudo=True,
                action="Instalando/atualizando Antigravity via apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo e confirmacoes.",
            )
        else:
            self.ctx.runner.run(
                ["dnf", "install", "-y", self.package],
                sudo=True,
                action="Instalando/atualizando Antigravity via dnf",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o dnf pode pedir senha do sudo e confirmacoes.",
            )
        self.mark_done(f"Antigravity instalado/atualizado via {mgr}.")

    # ---- caminho tarball (Arch / imutaveis) ----------------------------------

    @property
    def _install_dir(self) -> Path:
        return self.ctx.user.home / "Antigravity IDE"

    @property
    def _marker(self) -> Path:
        return self._install_dir / ".antigravity-version"

    def _installed_version(self) -> str | None:
        marker = self._marker
        if marker.exists():
            return marker.read_text(encoding="utf-8", errors="ignore").strip() or None
        return None

    def _write_marker(self, version: str) -> None:
        write_text(self._marker, version + "\n", self.ctx.runner)

    def _fetch_latest(self) -> dict | None:
        """Consulta o endpoint de auto-update. Retorna {name, url, sha256} ou None.

        Leitura pura (roda mesmo em dry-run). Qualquer falha de rede resulta em
        None, para o chamador cair no fallback (self.version / self.url).
        """
        data = fetch_json(self.update_api)
        if not isinstance(data, dict):
            return None
        name = data.get("productVersion") or data.get("name")
        url = data.get("url")
        if not name or not url:
            return None
        return {"name": str(name), "url": str(url), "sha256": data.get("sha256hash")}

    def _sha256_ok(self, path: Path, expected: str | None) -> bool:
        if not expected:
            return True
        if not path.exists():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == expected.lower()

    # Pacote -> um binario que ele fornece no PATH. Se o binario ja existe, pulamos
    # o install_system_package (que dispararia sudo/pacman a toa). Cobre o caso em
    # que a query de pacote nao resolve mas a ferramenta esta presente (todos base).
    _tool_binaries = {
        "curl": "curl",
        "tar": "tar",
        "desktop-file-utils": "update-desktop-database",
        "findutils": "find",
        "coreutils": "cp",
    }

    def _apply_tarball(self) -> None:
        header(self, self.title, "Baixando/atualizando IDE, integrando desktop e comando de terminal")
        for pkg, binary in self._tool_binaries.items():
            if command_exists(binary):
                continue
            install_system_package(pkg, self.ctx.runner)

        latest = self._fetch_latest()
        if latest is None:
            self.ctx.logger.write(
                f"{badge('aviso', Color.WARNING)} nao consegui consultar a ultima versao; "
                f"usando fallback {self.version}."
            )
            latest = {"name": self.version, "url": self.url, "sha256": None}
        target_version = latest["name"]

        cache = self.ctx.user.home / ".cache/antigravity-ide"
        tarball = cache / f"Antigravity-IDE-{target_version}.tar.gz"
        install_dir = self._install_dir
        existing_exe = self._find_executable(install_dir) if install_dir.exists() else None
        installed_version = self._installed_version()
        if (
            existing_exe
            and installed_version == target_version
            and self._desktop_ready(existing_exe)
            and self._wrapper_ready(existing_exe)
        ):
            self.ctx.logger.write(
                f"{badge('ok', Color.SUCCESS)} Antigravity IDE ja esta na versao mais recente ({target_version})"
            )
            self._path_hint()
            self.mark_skipped(f"Antigravity IDE ja estava atualizado ({target_version}).")
            return

        if installed_version and installed_version != target_version:
            self.ctx.logger.write(f"Atualizando Antigravity IDE: {installed_version} -> {target_version}")

        if not self.ctx.runner.dry_run:
            cache.mkdir(parents=True, exist_ok=True)
        # Nao fazemos backup do diretorio inteiro do IDE (seriam ~centenas de MB
        # duplicados a cada update): a instalacao faz replace atomico e o tarball
        # e re-baixavel/verificado por sha256.
        if tarball.exists() and tarball.stat().st_size > 1024 * 1024 and self._sha256_ok(tarball, latest["sha256"]):
            self.ctx.logger.write(f"{badge('ok', Color.SUCCESS)} pacote Antigravity ja esta em cache: {tarball}")
        else:
            self.ctx.runner.run(
                ["curl", "-L", "--fail", "-o", str(tarball), latest["url"]],
                action=f"Baixando Antigravity IDE {target_version}",
            )
            if not self.ctx.runner.dry_run and not self._sha256_ok(tarball, latest["sha256"]):
                raise RuntimeError("sha256 do pacote baixado do Antigravity nao confere")
        tmp = cache / "extract"
        if not self.ctx.runner.dry_run:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
        self.ctx.runner.run(["tar", "-xzf", str(tarball), "-C", str(tmp)], action="Extraindo pacote do Antigravity IDE")
        if self.ctx.runner.dry_run:
            exe = install_dir / "antigravity-ide"
            icon = install_dir / "resources/app/resources/linux/code.png"
        else:
            extracted = next((p for p in tmp.iterdir() if p.is_dir()), None)
            if not extracted:
                raise RuntimeError("nao encontrei diretorio extraido do Antigravity")
            shutil.rmtree(install_dir, ignore_errors=True)
            shutil.copytree(extracted, install_dir)
            exe = self._find_executable(install_dir)
            icon = self._find_icon(install_dir) or exe
        if not exe:
            raise RuntimeError("nao encontrei executavel antigravity-ide")
        self._write_desktop(exe, icon)
        self._write_terminal_wrapper(exe)
        self._write_marker(target_version)
        ensure_owner(install_dir, self.ctx.user, self.ctx.runner, recursive=True)
        self._path_hint()
        self.mark_done(f"Antigravity IDE instalado/atualizado ({target_version}).")

    def _find_executable(self, install_dir: Path) -> Path | None:
        for name in ("antigravity-ide", "antigravity", "code"):
            matches = list(install_dir.rglob(name))
            for match in matches:
                if match.is_file() and match.stat().st_mode & 0o111:
                    return match
        return None

    def _find_icon(self, install_dir: Path) -> Path | None:
        candidates = list(install_dir.rglob("code.png")) + list(install_dir.rglob("antigravity*.png"))
        return candidates[0] if candidates else None

    def _write_desktop(self, exe: Path, icon: Path) -> None:
        entry = DesktopEntry(
            name="Antigravity IDE",
            comment="Google Antigravity IDE",
            exec_line=f'"{exe}" %U',
            icon=str(icon),
            categories=("Development", "IDE"),
            mime_types=("text/plain", "inode/directory"),
            startup_wm_class="antigravity-ide",
        )
        install_desktop_entry(
            self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop", entry, self.ctx.runner
        )

    def _write_terminal_wrapper(self, exe: Path) -> None:
        wrapper = self.ctx.user.home / ".local/bin/antigravity-ide"
        content = self._wrapper_content(exe)
        write_text(wrapper, content, self.ctx.runner, mode=0o755)

    def _wrapper_content(self, exe: Path) -> str:
        return f"""#!/usr/bin/env bash
nohup "{exe}" "$@" >/dev/null 2>&1 &
disown 2>/dev/null || true
"""

    def _desktop_ready(self, exe: Path) -> bool:
        desktop_file = self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop"
        return desktop_file.exists() and str(exe) in desktop_file.read_text(encoding="utf-8", errors="ignore")

    def _wrapper_ready(self, exe: Path) -> bool:
        wrapper = self.ctx.user.home / ".local/bin/antigravity-ide"
        return wrapper.exists() and wrapper.read_text(encoding="utf-8", errors="ignore") == self._wrapper_content(exe)

    def _path_hint(self) -> None:
        local_bin = str(self.ctx.user.home / ".local/bin")
        if local_bin not in os.environ.get("PATH", "").split(":"):
            self.ctx.logger.write("Comando para fish, se ~/.local/bin nao estiver no PATH:")
            self.ctx.logger.write(f"fish_add_path {local_bin}")

    def status(self) -> None:
        if self._use_native():
            self._status_native()
        else:
            self._status_tarball()

    def _status_tarball(self) -> None:
        header(self, self.title)
        install_dir = self._install_dir
        desktop_file = self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop"
        wrapper_file = self.ctx.user.home / ".local/bin/antigravity-ide"
        local_bin = str(self.ctx.user.home / ".local/bin")
        path_ready = local_bin in os.environ.get("PATH", "").split(":")
        installed_version = self._installed_version()
        print_lines(
            self.ctx.logger,
            [
                f"{badge('instalacao', Color.INFO)} {'OK' if install_dir.exists() else 'ausente'} - {install_dir}",
                f"{badge('versao', Color.INFO)} {installed_version or 'desconhecida'}",
                f"{badge('desktop', Color.INFO)} {'OK' if desktop_file.exists() else 'ausente'} - {desktop_file}",
                f"{badge('wrapper', Color.INFO)} {'OK' if wrapper_file.exists() else 'ausente'} - {wrapper_file}",
                f"{badge('path', Color.SUCCESS if path_ready else Color.WARNING)} {'OK' if path_ready else 'ausente'} - {local_bin}",
            ],
        )
        latest = self._fetch_latest()
        if latest and installed_version and latest["name"] != installed_version:
            self.add_hint(f"ha versao nova disponivel: {latest['name']} (rode Aplicar para atualizar).")
        if install_dir.exists() and desktop_file.exists() and wrapper_file.exists() and path_ready:
            self.mark_applied("Antigravity IDE, desktop, wrapper e PATH estao aplicados.")
        elif wrapper_file.exists() and not path_ready:
            self.mark_attention("Antigravity esta instalado, mas ~/.local/bin ainda nao esta no PATH.")
        else:
            missing = []
            if not install_dir.exists():
                missing.append("instalacao")
            if not desktop_file.exists():
                missing.append("desktop")
            if not wrapper_file.exists():
                missing.append("wrapper")
            self.mark_pending(f"Antigravity ainda nao esta completo: {', '.join(missing)}.", missing=missing)

    def _status_native(self) -> None:
        header(self, self.title)
        distro = current_distro()
        mgr = "apt" if distro.is_debian else "dnf"
        installed = system_installed(self.package)
        print_lines(
            self.ctx.logger,
            [f"{badge('pacote', Color.INFO)} {'instalado' if installed else 'ausente'} - {self.package} ({mgr})"],
        )
        if not installed:
            self.mark_pending(f"Antigravity ainda nao esta instalado (repositorio {mgr}).", missing=["pacote"])
            return
        if self._native_update_available(distro):
            self.add_hint(f"ha atualizacao do Antigravity disponivel via {mgr} (rode Aplicar para atualizar).")
        self.mark_applied(f"Antigravity instalado via {mgr}.")

    def _native_update_available(self, distro) -> bool:
        if distro.is_debian:
            cmd = ["apt", "list", "--upgradable", self.package]
        else:
            cmd = ["dnf", "check-update", self.package]
        proc = capture(cmd)
        if distro.is_debian:
            return self.package in proc.stdout and "upgradable" in proc.stdout
        # dnf check-update: codigo 100 = ha atualizacoes; 0 = nada a fazer.
        return proc.returncode == 100 and self.package in proc.stdout

    def undo(self) -> None:
        if self._use_native():
            self._undo_native()
        else:
            self._undo_tarball()

    def _undo_tarball(self) -> None:
        for path in (
            self.ctx.user.home / ".local/share/applications/antigravity-ide.desktop",
            self.ctx.user.home / ".local/bin/antigravity-ide",
            self._install_dir,
        ):
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {path}")
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

    def _undo_native(self) -> None:
        distro = current_distro()
        if distro.is_debian:
            self.ctx.runner.run(
                ["apt-get", "remove", "-y", self.package],
                sudo=True,
                check=False,
                action="Removendo Antigravity via apt",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o apt pode pedir senha do sudo.",
            )
            repo_files = (
                "/etc/apt/sources.list.d/antigravity.list",
                "/etc/apt/keyrings/antigravity-repo-key.gpg",
            )
        else:
            self.ctx.runner.run(
                ["dnf", "remove", "-y", self.package],
                sudo=True,
                check=False,
                action="Removendo Antigravity via dnf",
                interactive=True,
                interactive_tty=True,
                manual_message="Comando interativo: o dnf pode pedir senha do sudo.",
            )
            repo_files = ("/etc/yum.repos.d/antigravity.repo",)
        for path in repo_files:
            if self.ctx.runner.dry_run:
                self.ctx.logger.write(f"{badge('dry-run', Color.DRY_RUN)} removeria {path}")
            else:
                self.ctx.runner.run(
                    ["rm", "-f", path],
                    sudo=True,
                    check=False,
                    action=f"Removendo {path}",
                    show_progress=False,
                )
