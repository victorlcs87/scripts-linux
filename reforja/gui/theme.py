"""Tema visual da GUI: paleta, folha de estilo e cores semanticas.

Fonte unica de verdade do visual. A folha de estilo e montada em Python (em vez
de um arquivo .qss separado) para nao depender de um arquivo de dados que o
empacotador precise carregar junto - o AppImage roda com o tema embutido no
codigo. As cores de compliance (cartoes) e de badges (console) vivem aqui para
que mudar a paleta seja mexer num lugar so.

Tema claro, neutro-frio, com um azul indigo de acento e um toque ember (o "R"
de Reforja) reservado ao indicador de navegacao ativo. O console/terminal
permanece escuro de proposito: e a superficie de saida de comandos, legivel com
os badges neon, como um terminal embutido num app claro.
"""

from __future__ import annotations

# --- paleta ------------------------------------------------------------------
PALETTE = {
    "bg": "#eef1f6",  # canvas frio (nao creme)
    "surface": "#ffffff",  # cartoes
    "surface_alt": "#f5f7fb",
    "sidebar": "#e7ebf3",
    "border": "#d3dae4",
    "border_strong": "#bcc6d4",
    "text": "#1b2432",
    "text_muted": "#586173",
    "text_faint": "#7b8494",
    "primary": "#2f5fd0",
    "primary_hover": "#2650b6",
    "primary_pressed": "#1f4499",
    "primary_soft": "#e7eefc",
    "on_primary": "#ffffff",
    "ember": "#d4661f",  # acento de marca / barra de nav ativa
    "success": "#1c7c3c",
    "pending": "#986a08",
    "attention": "#b1471a",
    "error": "#c62828",
    "danger_soft": "#fbeaea",
    "info": "#1f6bb0",
    "console_bg": "#12161f",
    "console_fg": "#d7dbe4",
}

# --- cores semanticas --------------------------------------------------------
# Compliance aparece nos cartoes (fundo claro): tons escuros o bastante para AA.
COMPLIANCE = {
    "aplicado": ("✓", PALETTE["success"]),
    "pendente": ("●", PALETTE["pending"]),
    "atencao": ("⚠", PALETTE["attention"]),
    "desconhecido": ("○", PALETTE["text_faint"]),
}

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

_FONT = '"Inter", "Noto Sans", "Segoe UI", "Cantarell", sans-serif'
_MONO = '"JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace'


def build_stylesheet() -> str:
    """Monta a folha de estilo completa a partir da paleta."""
    p = PALETTE
    return f"""
* {{ outline: none; }}

QMainWindow, QWidget {{
    background: {p["bg"]};
    color: {p["text"]};
    font-family: {_FONT};
    font-size: 13px;
}}

QToolTip {{
    background: {p["text"]};
    color: #ffffff;
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
#cardDesc {{ color: {p["text_muted"]}; font-size: 12px; }}
#cardStatus {{ font-size: 12px; font-weight: 600; }}

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
QPushButton#primary:pressed {{ background: {p["primary_pressed"]}; }}
QPushButton#primary:disabled {{ background: {p["border_strong"]}; border-color: {p["border_strong"]}; color: {p["surface"]}; }}

QPushButton#destructive {{ color: {p["error"]}; border-color: {p["error"]}; }}
QPushButton#destructive:hover {{ background: {p["danger_soft"]}; color: {p["error"]}; border-color: {p["error"]}; }}

/* --- campos ------------------------------------------------------------- */
QLineEdit, QComboBox {{
    background: {p["surface"]};
    color: {p["text"]};
    border: 1px solid {p["border_strong"]};
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
