"""Tema visual da GUI: paletas (clara/escura), folha de estilo e cores semanticas.

Fonte unica de verdade do visual. A folha de estilo e montada em Python (em vez
de um arquivo .qss separado) para nao depender de um arquivo de dados que o
empacotador precise carregar junto - o AppImage roda com o tema embutido no
codigo.

Ha uma paleta clara e uma escura (mesmas chaves); `build_stylesheet(dark=...)`
seleciona qual usar e fixa a paleta ativa, que os estilos inline em Python leem
via `palette()`/`compliance()`. O console/terminal permanece escuro nos dois
temas de proposito: e a superficie de saida de comandos, legivel com os badges
neon, como um terminal embutido.
"""

from __future__ import annotations

# --- paletas -----------------------------------------------------------------
LIGHT_PALETTE = {
    "bg": "#eef1f6",  # canvas frio (nao creme)
    "surface": "#ffffff",  # cartoes
    "surface_alt": "#f5f7fb",
    "sidebar": "#e7ebf3",
    "border": "#d3dae4",
    "border_strong": "#bcc6d4",
    "border_input": "#767f8f",  # limite de componente precisa de 3:1 (WCAG 1.4.11)
    "on_disabled": "#2b3444",  # texto sobre o fundo do botao primario desabilitado
    "text": "#1b2432",
    "text_muted": "#586173",
    "text_faint": "#5f6878",  # 5.2:1 no pior fundo (surface_alt); #7b8494 dava 3.5:1
    "primary": "#2f5fd0",
    "primary_hover": "#2650b6",
    "primary_pressed": "#1f4499",
    "primary_soft": "#e7eefc",
    "on_primary": "#ffffff",
    "ember": "#d4661f",  # acento de marca / barra de nav ativa
    "success": "#1c7c3c",
    "success_soft": "#e8f4ec",
    "success_border": "#bfe0cb",
    "pending": "#7d5606",  # 5.8:1 sobre bg; #986a08 dava 4.2:1
    "attention": "#b1471a",
    "error": "#c62828",
    "danger_soft": "#fbeaea",
    "info": "#1f6bb0",
    "console_bg": "#12161f",
    "console_fg": "#d7dbe4",
    # Anel de foco: precisa contrastar com a superficie (branca) E com o azul do
    # botao primario, entao e mais escuro que o primary em vez de ser o primary.
    "focus_ring": "#10214d",
    # Sobre o preenchimento azul do primario o anel escuro so dava 2.7:1 (min 3:1):
    # ali o anel e claro, contrastando com o proprio botao (5.7:1).
    "focus_ring_on_primary": "#ffffff",
    # No tema claro o primary_hover escurece, entao o anel segue branco.
    "focus_ring_hover": "#ffffff",
}

DARK_PALETTE = {
    "bg": "#141821",
    "surface": "#1c212c",
    "surface_alt": "#232935",
    "sidebar": "#171b24",
    "border": "#2b323f",
    "border_strong": "#3a4150",
    "border_input": "#737d8c",
    "on_disabled": "#c3cbd8",
    "text": "#e6e9ef",
    "text_muted": "#a4adbd",
    "text_faint": "#9aa3b2",  # 5.7:1 no pior fundo; o valor antigo era o MESMO da paleta clara
    "primary": "#5b8bf0",
    "primary_hover": "#6f9bf5",
    "primary_pressed": "#4a79db",
    "primary_soft": "#22304d",
    "on_primary": "#0d1017",
    "ember": "#e8823a",
    "success": "#4ccb74",
    "success_soft": "#17301f",
    "success_border": "#2c5a3a",
    "pending": "#d6a94a",
    "attention": "#e0794a",
    "error": "#f2686b",
    "danger_soft": "#3a2226",
    "info": "#5fb0e8",
    "console_bg": "#0d1017",
    "console_fg": "#d7dbe4",
    # No tema escuro o anel e claro pelo mesmo motivo invertido.
    "focus_ring": "#dbe7ff",
    "focus_ring_on_primary": "#ffffff",  # 3.3:1 sobre o primary do tema escuro
    # No tema escuro o primary_hover CLAREIA: o anel branco sumiria (2.73:1).
    "focus_ring_hover": "#0b1730",
}

# Paleta ativa (mutada por build_stylesheet). PALETTE continua exportado (a clara)
# para compatibilidade; codigo novo deve ler palette().
PALETTE = LIGHT_PALETTE
_active = LIGHT_PALETTE


def set_dark(dark: bool) -> None:
    global _active
    _active = DARK_PALETTE if dark else LIGHT_PALETTE


def palette() -> dict:
    """Paleta atualmente ativa (clara ou escura)."""
    return _active


# --- cores semanticas --------------------------------------------------------
_COMPLIANCE_GLYPH = {"aplicado": "✓", "pendente": "●", "atencao": "⚠", "desconhecido": "○"}
_COMPLIANCE_KEY = {"aplicado": "success", "pendente": "pending", "atencao": "attention", "desconhecido": "text_faint"}

# Compat: dict fixo na paleta clara (codigo legado/testes). Prefira compliance().
COMPLIANCE = {state: (_COMPLIANCE_GLYPH[state], LIGHT_PALETTE[_COMPLIANCE_KEY[state]]) for state in _COMPLIANCE_GLYPH}


def compliance(state: str) -> tuple[str, str]:
    """(glifo, cor) do estado de compliance, na paleta ativa."""
    glyph = _COMPLIANCE_GLYPH.get(state, "○")
    color = _active[_COMPLIANCE_KEY.get(state, "text_faint")]
    return glyph, color


# Badges aparecem no console (fundo escuro): tons neon, legiveis no escuro.
BADGE_COLORS = {
    "done": "#3fdd7a",
    "aplicado": "#3fdd7a",
    "pendente": "#f2c14e",
    "atencao": "#ff8a5c",
    "ok": "#3fdd7a",
    "summary": "#ff8fd8",
    "info": "#5fd7ff",
    "action": "#5fd7ff",
    "waiting": "#ffd24a",
    "warning": "#ffd24a",
    "skipped": "#ffd24a",
    "manual": "#ffd24a",
    "aviso": "#ffd24a",
    "dry-run": "#ffb454",
    "rodando": "#5fd7ff",
    "choice": "#8dff8d",
    "failed": "#ff6b6b",
    "blocked": "#ff6b6b",
    "erro": "#ff6b6b",
}

CONSOLE_CMD_COLOR = "#5fe1ff"

# Cor do avatar tipografico (fallback do icone) por categoria de item.
CATEGORY_COLORS = {
    "jogos": "#7c3aed",
    "game": "#7c3aed",
    "comunicacao": "#2f5fd0",
    "escritorio": "#0f766e",
    "navegador": "#c2410c",
    "dev": "#3b4a63",
    "sistema": "#4b5563",
    "system": "#4b5563",
    "utilitarios": "#0369a1",
    "utility": "#0369a1",
    "_default": "#586173",
}

_FONT = '"Inter", "Noto Sans", "Segoe UI", "Cantarell", sans-serif'
_MONO = '"JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace'


def build_stylesheet(dark: bool = False) -> str:
    """Monta a folha de estilo a partir da paleta (clara por padrao, escura se dark)."""
    set_dark(dark)
    p = _active
    return f"""
QMainWindow, QWidget {{
    background: {p["bg"]};
    color: {p["text"]};
    font-family: {_FONT};
    font-size: 13px;
}}

QToolTip {{
    background: {p["text"]};
    color: {p["bg"]};
    border: none;
    border-radius: 6px;
    padding: 6px 8px;
}}

/* --- navegacao lateral -------------------------------------------------- */
#navSidebar {{
    background: {p["sidebar"]};
    border-right: 1px solid {p["border"]};
}}
#brandMark {{
    color: {p["ember"]};
    font-size: 18px;
    font-weight: 800;
    padding: 4px 6px 2px 6px;
}}
#brandSub {{ color: {p["text_muted"]}; font-size: 11px; padding: 0 6px 6px 6px; }}
#navMenu {{
    background: transparent;
    border: none;
    padding-top: 6px;
    /* Suprime o retangulo de foco NATIVO do Qt so aqui: no menu ele desenha uma
       caixa em volta do texto que faz o item parecer um campo editavel. O foco
       do item e indicado pela barra lateral em #navMenu::item:focus. */
    outline: none;
}}
#navMenu::item {{
    color: {p["text_muted"]};
    padding: 11px 14px;
    border-left: 3px solid transparent;
    border-radius: 0;
    margin: 1px 0;
}}
#navMenu::item:hover {{ background: {p["surface_alt"]}; color: {p["text"]}; }}
#navMenu::item:selected {{
    background: {p["primary_soft"]};
    color: {p["primary"]};
    border-left: 3px solid {p["ember"]};
    font-weight: 600;
}}

/* --- paginas ------------------------------------------------------------ */
#pageTitle {{ font-size: 22px; font-weight: 700; color: {p["text"]}; }}
#pageDesc {{ color: {p["text_muted"]}; font-size: 13px; }}
#statusLine {{ color: {p["text_muted"]}; font-size: 12px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* --- cartao de etapa ---------------------------------------------------- */
#stepCard {{
    background: {p["surface"]};
    border: 1px solid {p["border"]};
    border-radius: 10px;
}}
#stepCard:hover {{ border-color: {p["border_strong"]}; }}
#cardTitle {{ font-size: 15px; font-weight: 600; color: {p["text"]}; }}
#cardDesc {{ background: transparent; color: {p["text_muted"]}; font-size: 12px; }}
#cardStatus {{ font-size: 12px; font-weight: 600; }}

/* --- card de item (grade estilo Flathub) -------------------------------- */
#itemCard {{
    background: {p["surface"]};
    border: 1px solid {p["border"]};
    border-radius: 12px;
}}
#itemCard:hover {{ border-color: {p["border_strong"]}; }}
#itemCard[applied="true"] {{ border-color: {p["success"]}; }}
#itemName {{ background: transparent; font-size: 14px; font-weight: 700; color: {p["text"]}; }}
#itemDesc {{ background: transparent; color: {p["text_muted"]}; font-size: 12px; }}
#itemState {{ background: transparent; font-size: 11px; font-weight: 600; color: {p["text_faint"]}; }}
#installedChip {{
    color: {p["success"]};
    background: {p["success_soft"]};
    border: 1px solid {p["success_border"]};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
#errorChip {{
    color: {p["error"]};
    background: {p["danger_soft"]};
    border: 1px solid {p["error"]};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
#busyChip {{
    color: {p["primary"]};
    background: {p["primary_soft"]};
    border: 1px solid {p["primary"]};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}}
#unavailableChip {{
    color: {p["text_faint"]};
    background: {p["surface_alt"]};
    border: 1px solid {p["border"]};
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {p["border_strong"]};
    padding: 6px 12px;
    font-size: 12px;
}}
QPushButton#ghost:hover {{ border-color: {p["primary"]}; color: {p["primary"]}; }}
/* Acoes dentro do card sao compactas para caber chip + Atualizar/Reinstalar +
   Remover na largura do card sem cortar. */
#itemCard QPushButton {{ padding: 5px 9px; font-size: 12px; }}
#backLink {{ color: {p["primary"]}; font-weight: 600; }}
#sectionLabel {{ color: {p["text_faint"]}; font-size: 11px; font-weight: 700; }}
/* Item ja instalado na previa: esmaecido (marcar = reinstalar). */
QCheckBox#installedCheck {{ color: {p["text_faint"]}; }}

/* --- chips de filtro por categoria (busca do catalogo) ------------------ */
QToolButton#filterChip {{
    background: {p["surface_alt"]};
    color: {p["text_muted"]};
    border: 1px solid {p["border"]};
    border-radius: 12px;
    padding: 4px 12px;
    font-size: 12px;
}}
QToolButton#filterChip:checked {{
    background: {p["primary_soft"]};
    color: {p["primary"]};
    border-color: {p["primary"]};
    font-weight: 600;
}}

/* --- botao de preset (Home) --------------------------------------------- */
QPushButton#preset {{
    background: {p["surface"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 10px;
    padding: 10px 16px;
    font-weight: 600;
}}
QPushButton#preset:hover {{ border-color: {p["ember"]}; color: {p["ember"]}; }}

/* --- botoes ------------------------------------------------------------- */
QPushButton {{
    background: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 500;
}}
QPushButton:hover {{ border-color: {p["primary"]}; color: {p["primary"]}; }}
QPushButton:pressed {{ background: {p["surface_alt"]}; }}
QPushButton:disabled {{ color: {p["text_faint"]}; border-color: {p["border"]}; background: {p["surface_alt"]}; }}

QPushButton#primary {{
    background: {p["primary"]};
    color: {p["on_primary"]};
    border: 1px solid {p["primary"]};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {p["primary_hover"]}; border-color: {p["primary_hover"]}; color: {p["on_primary"]}; }}
/* Foco + hover coexistem (mouse parado sobre o botao focado). No tema escuro o
   hover clareia o fundo e o anel branco caia para 2.73:1, sumindo justamente
   ali; sobre o hover o anel usa o tom escuro, que contrasta com o azul claro. */
QPushButton#primary:hover:focus {{ border: 2px solid {p["focus_ring_hover"]}; padding: 7px 13px; }}
#itemCard QPushButton#primary:hover:focus {{ border: 2px solid {p["focus_ring_hover"]}; padding: 4px 8px; }}
QPushButton#primary:pressed {{ background: {p["primary_pressed"]}; }}
QPushButton#primary:disabled {{ background: {p["border_strong"]}; border-color: {p["border_strong"]}; color: {p["on_disabled"]}; }}

QPushButton#destructive {{ color: {p["error"]}; border-color: {p["error"]}; }}
QPushButton#destructive:hover {{ background: {p["danger_soft"]}; color: {p["error"]}; border-color: {p["error"]}; }}
/* Sem esta regra a especificidade do seletor de ID vencia QPushButton:disabled e o
   botao "Parar" ficava vermelho vivo em repouso — sugerindo que algo rodava. */
QPushButton#destructive:disabled {{ color: {p["text_faint"]}; border-color: {p["border"]}; background: {p["surface_alt"]}; }}

/* --- foco visivel (navegacao por teclado) -------------------------------
   O anel e uma borda de 2px com o padding reduzido em 1px, para o widget nao
   mudar de tamanho ao receber foco. Sem estas regras o app fica inoperavel por
   teclado: nao ha como saber onde o foco esta. */
QPushButton:focus {{ border: 2px solid {p["focus_ring"]}; padding: 7px 13px; }}
QPushButton#primary:focus {{ border: 2px solid {p["focus_ring_on_primary"]}; padding: 7px 13px; }}
QPushButton#destructive:focus {{ border: 2px solid {p["focus_ring"]}; padding: 7px 13px; }}
QPushButton#ghost:focus {{ border: 2px solid {p["focus_ring"]}; padding: 5px 11px; }}
QPushButton#preset:focus {{ border: 2px solid {p["focus_ring"]}; padding: 9px 15px; }}
#itemCard QPushButton:focus {{ border: 2px solid {p["focus_ring"]}; padding: 4px 8px; }}
#itemCard QPushButton#primary:focus {{ border: 2px solid {p["focus_ring_on_primary"]}; padding: 4px 8px; }}
QToolButton#filterChip:focus {{ border: 2px solid {p["focus_ring"]}; padding: 3px 11px; }}
/* O item de nav ja reserva 3px de borda a esquerda: colorir nao desloca nada.
   As demais bordas sao zeradas para nao somar com a regra generica de QListWidget
   abaixo (senao o item ganha uma caixa dupla). */
#navMenu::item:focus {{
    border: none;
    border-left: 3px solid {p["focus_ring"]};
    background: {p["surface_alt"]};
}}
/* Sem regra generica de foco para QListWidget::item: o Qt nao suporta :not() em
   QSS (quebra a folha inteira), e a unica lista navegavel do app e o #navMenu,
   ja tratado acima. Uma borda retangular ali fazia o item de menu parecer um
   campo de texto. */
/* Fundo sozinho dava 1.03:1 contra o bg — invisivel. O BatchPreviewDialog e
   feito de checkboxes, entao sem borda a revisao do "Aplicar tudo" e cega ao
   teclado. A borda contra o proprio fundo do foco da 13.4:1 / 10.6:1. */
QCheckBox:focus {{
    background: {p["primary_soft"]};
    border: 2px solid {p["focus_ring"]};
    border-radius: 4px;
}}
/* Alvo de clique: o indicador padrao mede 23px de altura, 1px abaixo do minimo
   de 24x24 (WCAG 2.5.8). */
QCheckBox {{ min-height: 24px; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; }}
/* Lista de escolha multipla (ex.: discos do fstab): navegada por teclado. */
#choiceList::item:focus {{ background: {p["primary_soft"]}; border: 1px solid {p["focus_ring"]}; }}

/* --- campos ------------------------------------------------------------- */
QLineEdit, QComboBox {{
    background: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border_input"]};
    border-radius: 8px;
    padding: 7px 9px;
    selection-background-color: {p["primary"]};
    selection-color: {p["on_primary"]};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {p["primary"]}; }}

QListWidget {{
    background: {p["surface"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {p["primary_soft"]}; color: {p["primary"]}; }}

QGroupBox {{
    border: 1px solid {p["border"]};
    border-radius: 10px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    background: {p["surface"]};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p["text_muted"]}; }}

/* --- console e terminal (superficie escura, proposital) ---------------- */
#console, #terminal {{
    background: {p["console_bg"]};
    color: {p["console_fg"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 10px;
    padding: 10px;
    font-family: {_MONO};
    font-size: 12px;
}}

/* --- barra de progresso ------------------------------------------------- */
QProgressBar#progress {{
    border: 1px solid {p["border"]};
    border-radius: 8px;
    background: {p["surface"]};
    text-align: center;
    color: {p["text"]};
    height: 22px;
}}
QProgressBar#progress::chunk {{ background: {p["primary"]}; border-radius: 7px; }}

/* --- faixa de conclusao -------------------------------------------------- */
#resultBanner {{
    background: {p["surface"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 10px;
}}
#resultText {{ background: transparent; font-size: 13px; }}

/* --- dialogos ----------------------------------------------------------- */
QDialog {{ background: {p["bg"]}; }}
QDialog QLabel {{ color: {p["text"]}; }}
#aviso {{ color: {p["error"]}; font-weight: 600; }}

/* --- scrollbars --------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p["border_strong"]}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p["text_faint"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p["border_strong"]}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {p["text_faint"]}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""
