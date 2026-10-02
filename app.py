"""Interface principal do Modelador ER - Notação de Chen & Diagramação de Tabelas.

Recursos implementados:
- Layout estilo Excel / Office 365 (Faixa de Opções Ribbon, abas, barra de título temática, barra de status inferior).
- Paleta lateral de formas estilo Lucidchart / Task Pane com inspetor rápido de propriedades.
- Alternância de primeira classe entre Notação de Chen (Conceitual - Navathe) e Diagramação por Tabelas (Relacional).
- Ajuste e personalização de atributos: arraste atributos diretamente no canvas, controle de raio/espaçamento com slider e presets, auto-organização em leque.
- Navegação completa por mouse: Zoom In/Out com Ctrl+Scroll e botões, Pan arrastando o canvas.
"""
import math
import ctypes
import json
import os
import sys
import tempfile
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from models import Project, Entity, Relationship, Attribute, Specialization, new_id
from ddl_generator import generate_ddl, generate_kimball_ddl
import report_export
import validator
import barker_router as router
import datetime
from oracle_dialog import OracleDialog
import oracle_tools
from dialogs import (EntityDialog, CardinalityDialog, RelationshipDialog, DDLWindow,
                     DomainManagerDialog, SpecializationDialog, ValidationWindow)

ENTITY_W = 160
ENTITY_H_CH = 46
# Notação de Barker (visão lógica)
BK_HEAD = 28        # altura do cabeçalho (nome da entidade)
BK_ROW = 19         # altura de cada linha de atributo
BK_PAD = 8          # margem interna (subtipos aninhados)
BK_MIN_W = 190
SPEC_R = 13         # raio do círculo de especialização (d / o / u)
SNAP = 20           # passo do "encaixar na grade"
APP_DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "MdsChenNotation"
AUTOSAVE_DIR = APP_DATA_DIR / "autosave"
SESSION_FILE = APP_DATA_DIR / "session.json"
MAX_UNDO_STEPS = 60


def _enable_windows_dpi_awareness():
    """Pede ao Windows para desenhar a interface na resolução real do monitor."""
    if sys.platform != "win32":
        return

    try:
        # Windows 10+: acompanha a densidade do monitor e evita bitmap scaling.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass

    try:
        # Fallback para versões do Windows sem Per-Monitor V2.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


class Tooltip:
    """Dica flutuante rápida e responsiva (aparece após 220 ms sobre o widget)."""

    def __init__(self, widget, text, delay=220):
        self.widget, self.text, self.tip, self._job = widget, text, None, None
        self.delay = delay
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self.tip = tk.Toplevel(self.widget)
            self.tip.wm_overrideredirect(True)
            self.tip.wm_geometry(f"+{x}+{y}")
            tk.Label(self.tip, text=self.text, bg="#25304A", fg="#ffffff", font=("Segoe UI", 8),
                     padx=8, pady=3, justify="left").pack()
        except tk.TclError:
            self.tip = None

    def _hide(self, _e=None):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


class App(tk.Tk):
    def __init__(self):
        _enable_windows_dpi_awareness()
        super().__init__()
        self.title("Modelador ER — Notação de Chen & Tabelas")
        self.geometry("1340x860")
        self.minsize(980, 640)

        # Projetos abertos, com cópia automática para recuperação entre execuções.
        self.documents = []
        self.active_document = None
        self._restore_documents()
        if not self.documents:
            starter = Path(__file__).with_name("empresa_navathe.json")
            if starter.exists():
                try:
                    self._add_document(Project.load(starter), starter.stem, path=None, is_autosave=True)
                except Exception:
                    self._add_document(Project.create_navathe_company_example(), "Exemplo Empresa")
            else:
                self._add_document(Project.create_navathe_company_example(), "Exemplo Empresa")
        self.active_document = self.documents[min(self._restored_active_index, len(self.documents) - 1)]
        self.project = self.active_document["project"]
        self.notation = "chen"  # "chen" ou "barker"
        self.link_mode = False
        self.link_src = None
        self.link_identifying = False
        self.nary_mode = False          # criação de relacionamento n-ário (3+ entidades)
        self.nary_sel = []
        self.snap_on = True
        self.show_legend = True
        self._render_job = None
        self._hover_key = None
        self._rc_start = None           # posição do botão direito (menu de contexto x pan)
        self._rc_moved = False
        self._last_nudge = 0.0
        self._bk_cache = None
        self._bk_route_cache = None
        self._fast_routing = False
        self._mouse_model = (0, 0)
        self.oracle = oracle_tools.OracleSession()   # conexão Oracle: vive enquanto o app estiver aberto
        self._oracle_dlg = None
        self._search_key, self._search_idx = None, 0
        self.ribbon_visible = True
        self.global_attr_spacing = 90.0
        self.show_grid = True
        self.sidebar_visible = True

        # Navegação (Zoom e Pan)
        self.zoom = 1.0  # 1.0 = 100%
        self.pan_x = 40.0
        self.pan_y = 40.0

        # Interação do mouse
        self.is_panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.pan_start_px = 0.0
        self.pan_start_py = 0.0

        self.drag_target = None  # ("entity", e.id), ("rel", r.id), ("attr", e.id, a.id), etc.
        self.drag_offset_x = 0.0
        self.drag_offset_y = 0.0
        self._drag_prestate = None
        self._drag_moved = False
        self.selected_item = None
        self._measure_fonts = {}

        # Modo "Zoom em Seleção": arraste uma área do canvas para dar zoom só nela.
        self.zoom_select_mode = False
        self._marquee_start = None
        self._marquee_rect_id = None

        # Ribbon Tabs state
        self.active_ribbon_tab = "home"
        self.ribbon_panels = {}

        self.notation = getattr(self.project, 'notation', 'chen')

        self._configure_styles()
        # _build_office_header removido conforme solicitação do usuário para ganho de espaço visual
        self._build_office_ribbon()
        self._build_quick_toolbar()
        self._build_document_tabs()
        self._build_main_area()
        self._build_office_statusbar()
        self._build_menubar()

        self.protocol("WM_DELETE_WINDOW", self.close_application)
        self._refresh_document_tabs()
        self._update_window_title()
        self._persist_session()
        self._bind_shortcuts()
        self.after(60, self.zoom_fit)

    def _bind_shortcuts(self):
        """Atalhos globais (ignorados enquanto se digita em campos ou em janelas de diálogo)."""
        def kb(fn):
            def handler(event):
                if self._typing(event):
                    return None
                fn()
                return "break"
            return handler

        binds = {
            "<Control-z>": self.undo, "<Control-Z>": self.undo,
            "<Control-y>": self.redo, "<Control-Y>": self.redo,
            "<Control-Shift-Z>": self.redo, "<Control-Shift-z>": self.redo,
            "<Control-s>": self.save_project, "<Control-S>": self.save_project_as,
            "<Control-o>": self.load_project, "<Control-n>": self.new_project,
            "<Control-i>": self.open_import_dialog, "<Control-I>": self.open_import_dialog,
            "<Control-h>": self.align_rel_horizontal, "<Control-H>": self.align_rel_horizontal,
            "<Control-Shift-H>": self.align_rel_vertical, "<Control-Shift-h>": self.align_rel_vertical,
            "<Control-d>": self.duplicate_selected, "<Control-D>": self.duplicate_selected,
            "<Control-0>": self.zoom_fit, "<Control-1>": self.zoom_reset,
            "<Control-plus>": self.zoom_in, "<Control-equal>": self.zoom_in, "<Control-minus>": self.zoom_out,
            "<Delete>": self.delete_selected, "<F2>": self.edit_selected_from_inspector,
            "<Return>": self._on_return_key, "<Control-l>": self.toggle_link, "<Control-f>": self.focus_search,
            "<Control-g>": self.toggle_grid_key, "<F5>": self.validate_model,
            "<F9>": self.show_ddl, "<F1>": self.show_help,
        }
        for seq, fn in binds.items():
            self.bind_all(seq, kb(fn))
        for key, (dx, dy) in {"<Left>": (-1, 0), "<Right>": (1, 0), "<Up>": (0, -1), "<Down>": (0, 1)}.items():
            self.bind_all(key, kb(lambda dx=dx, dy=dy: self.nudge_selected(dx * SNAP // 2, dy * SNAP // 2)))
            self.bind_all("<Shift-" + key[1:], kb(lambda dx=dx, dy=dy: self.nudge_selected(dx * SNAP * 2, dy * SNAP * 2)))
        self.bind_all("<Escape>", self._on_escape_key)

    def _typing(self, event):
        """True se o foco está num campo de texto/lista ou em outra janela (diálogo)."""
        w = event.widget
        try:
            if w.winfo_toplevel() is not self:
                return True
        except tk.TclError:
            return True
        return isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, tk.Listbox, ttk.Entry, ttk.Combobox, ttk.Scale))

    def _on_return_key(self):
        if self.nary_mode:
            self.finish_nary()
        else:
            self.edit_selected_from_inspector()

    def toggle_grid_key(self):
        self.grid_var.set(not self.grid_var.get())
        self.toggle_grid()

    def _on_escape_key(self, event=None):
        if event is not None and event.widget.winfo_toplevel() is not self:
            return
        if self.zoom_select_mode:
            self.zoom_select_mode = False
            if self._marquee_rect_id:
                self.canvas.delete(self._marquee_rect_id)
                self._marquee_rect_id = None
            self._marquee_start = None
            if hasattr(self, 'zoom_select_btn'):
                self.zoom_select_btn.state(["!pressed"])
        self._cancel_link_modes()
        self.canvas.config(cursor="arrow")
        if hasattr(self, 'status_msg'):
            self.status_msg.config(text="Pronto")
        self.render()

    def _cancel_link_modes(self):
        self.link_mode = False
        self.link_src = None
        self.link_identifying = False
        self.nary_mode = False
        self.nary_sel = []
        if hasattr(self, "link_btn"):
            self.link_btn.state(["!pressed"])

    def _add_document(self, project, name=None, path=None, storage_path=None, is_autosave=None, dirty=False):
        """`storage_path` é sempre um rascunho de recuperação em AUTOSAVE_DIR; o arquivo do usuário
        (`path`) só é gravado por Salvar / Salvar como."""
        project_path = Path(path) if path else None
        snapshot_path = Path(storage_path) if storage_path else AUTOSAVE_DIR / f"project_{new_id('p')}.json"
        document = {
            "project": project,
            "name": name or (project_path.stem if project_path else "Projeto sem título"),
            "path": str(project_path) if project_path else None,
            "storage_path": str(snapshot_path),
            "is_autosave": (project_path is None) if is_autosave is None else is_autosave,
            "dirty": bool(dirty),
            "autosave_job": None,
            "undo_stack": [],
            "redo_stack": [],
        }
        self.documents.append(document)
        return document

    def _restore_documents(self):
        """Reabre os projetos da execução anterior (rascunhos de recuperação incluídos)."""
        self._restored_active_index = 0
        try:
            with SESSION_FILE.open("r", encoding="utf-8") as session_file:
                session = json.load(session_file)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        for item in session.get("documents", []):
            try:
                snapshot = Path(item.get("storage_path", ""))
                path = item.get("path")
                dirty = bool(item.get("dirty", False))
                if snapshot.is_file() and (dirty or not path or not Path(path).is_file()):
                    project = Project.load(snapshot)
                elif path and Path(path).is_file():
                    project, dirty = Project.load(path), False
                else:
                    continue
                self._add_document(project, name=item.get("name"), path=path,
                                   storage_path=snapshot, is_autosave=item.get("is_autosave", False), dirty=dirty)
            except (OSError, ValueError, TypeError):
                continue
        if self.documents:
            try:
                self._restored_active_index = min(max(0, int(session.get("active_index", 0))), len(self.documents) - 1)
            except (ValueError, TypeError):
                self._restored_active_index = 0

    def _write_json_atomically(self, path, writer):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=str(path.parent))
        os.close(fd)
        try:
            writer(temporary)
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def _persist_session(self):
        if not self.documents:
            return
        session = {
            "active_index": self.documents.index(self.active_document) if self.active_document in self.documents else 0,
            "documents": [{key: document[key] for key in ("name", "path", "storage_path", "is_autosave", "dirty")}
                          for document in self.documents],
        }
        try:
            self._write_json_atomically(
                SESSION_FILE,
                lambda temporary: Path(temporary).write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8"),
            )
        except OSError:
            pass

    def _write_document_snapshot(self, document):
        self._write_json_atomically(
            Path(document["storage_path"]),
            lambda temporary: document["project"].save(temporary),
        )

    def _mark_active_dirty(self):
        document = self.active_document
        if not document:
            return
        self._bk_cache = None
        newly_dirty = not document["dirty"]
        document["dirty"] = True
        if document.get("autosave_job"):
            self.after_cancel(document["autosave_job"])
        document["autosave_job"] = self.after(650, lambda doc=document: self._autosave_document(doc))
        if newly_dirty and hasattr(self, "document_tabs"):
            self._refresh_document_tabs()

    def _autosave_document(self, document):
        """Grava só o rascunho de recuperação. Projetos com arquivo continuam 'não salvos' (•)
        até Ctrl+S; projetos sem nome ficam sempre recuperáveis."""
        document["autosave_job"] = None
        try:
            self._write_document_snapshot(document)
            if document["path"] is None:
                document["dirty"] = False
            if hasattr(self, "status_msg") and document is self.active_document:
                self.status_msg.config(text="Rascunho salvo automaticamente" if document["path"] is None
                                       else "Rascunho salvo — Ctrl+S grava no arquivo")
            self._persist_session()
            self._refresh_document_tabs()
        except OSError as ex:
            if hasattr(self, "status_msg") and document is self.active_document:
                self.status_msg.config(text=f"Falha ao salvar rascunho: {ex}")

    def _flush_autosaves(self):
        for document in self.documents:
            if document.get("autosave_job"):
                self.after_cancel(document["autosave_job"])
                document["autosave_job"] = None
            try:
                self._write_document_snapshot(document)
                if document["path"] is None:
                    document["dirty"] = False
            except OSError:
                pass
        self._persist_session()

    def _mark_model_changed(self):
        self._mark_active_dirty()
        self.render()

    # ---------------- Desfazer / Refazer (Undo / Redo) ----------------
    def _push_undo(self, snapshot=None):
        """Tira uma 'foto' do estado atual do projeto antes de uma alteração.

        Chamado sempre antes de qualquer operação que modifique o modelo
        (criar/editar/excluir entidades, relacionamentos ou atributos),
        permitindo desfazer com Ctrl+Z / botão 'Desfazer'. Se `snapshot` for
        informado, usa esse estado (ex.: capturado antes de um arraste) em vez
        do estado atual.
        """
        document = self.active_document
        if not document:
            return
        try:
            snap = snapshot if snapshot is not None else self.project.to_dict()
        except Exception:
            return
        document["undo_stack"].append(snap)
        if len(document["undo_stack"]) > MAX_UNDO_STEPS:
            document["undo_stack"].pop(0)
        document["redo_stack"].clear()
        self._update_undo_redo_buttons()

    def _restore_project_snapshot(self, snapshot):
        document = self.active_document
        self.project = Project.from_dict(snapshot)
        document["project"] = self.project
        self.notation = getattr(self.project, "notation", self.notation)
        self.selected_item = None
        self._cancel_link_modes()
        self._sync_notation_buttons()
        self._update_inspector(None)
        self._mark_active_dirty()
        self.render()
        self._update_undo_redo_buttons()

    def undo(self):
        document = self.active_document
        if not document or not document["undo_stack"]:
            if hasattr(self, "status_msg"):
                self.status_msg.config(text="Nada para desfazer.")
            return
        document["redo_stack"].append(self.project.to_dict())
        snapshot = document["undo_stack"].pop()
        self._restore_project_snapshot(snapshot)
        if hasattr(self, "status_msg"):
            self.status_msg.config(text="↶ Alteração desfeita.")

    def redo(self):
        document = self.active_document
        if not document or not document["redo_stack"]:
            if hasattr(self, "status_msg"):
                self.status_msg.config(text="Nada para refazer.")
            return
        document["undo_stack"].append(self.project.to_dict())
        snapshot = document["redo_stack"].pop()
        self._restore_project_snapshot(snapshot)
        if hasattr(self, "status_msg"):
            self.status_msg.config(text="↷ Alteração refeita.")

    def _update_undo_redo_buttons(self):
        document = self.active_document
        can_undo = bool(document and document["undo_stack"])
        can_redo = bool(document and document["redo_stack"])
        if hasattr(self, "undo_btn"):
            self.undo_btn.state(["!disabled"] if can_undo else ["disabled"])
        if hasattr(self, "redo_btn"):
            self.redo_btn.state(["!disabled"] if can_redo else ["disabled"])

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Tema claro com acentos índigo e superfícies suaves.
        style.configure("TFrame", background="#F4F6FC")
        style.configure("TLabel", background="#F4F6FC", foreground="#25304A", font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(9, 5),
                        background="#FFFFFF", foreground="#34415F", bordercolor="#DCE3F1")
        style.map("TButton", background=[("active", "#EEEAFE"), ("pressed", "#DDD6FE")],
                  foreground=[("active", "#4938D0")])
        style.configure("TCombobox", padding=4, fieldbackground="#FFFFFF", foreground="#25304A",
                        arrowcolor="#5948E8")
        style.configure("TEntry", padding=4, fieldbackground="#FFFFFF", foreground="#25304A")
        style.configure("TCheckbutton", background="#F4F6FC", foreground="#34415F")
        style.map("TCheckbutton", background=[("active", "#F4F6FC")],
                  foreground=[("active", "#4938D0")])

        # Estilo específico Ribbon
        style.configure("Ribbon.TFrame", background="#ffffff")
        style.configure("RibbonGroup.TFrame", background="#ffffff")
        style.configure("RibbonGroupTitle.TLabel", background="#ffffff", foreground="#73809B", font=("Segoe UI", 7, "bold"))
        style.configure("RibbonBtn.TButton", font=("Segoe UI", 8), padding=(4, 2))
        style.configure("Side.TButton", font=("Segoe UI", 8, "bold"), padding=(6, 2))
        style.configure("Tool.TButton", font=("Segoe UI", 9), padding=(7, 3), background="#FFFFFF")
        style.configure("Tool.Toolbutton", font=("Segoe UI", 9), padding=(7, 3), background="#FFFFFF")
        style.map("Tool.Toolbutton", background=[("selected", "#DDD6FE"), ("active", "#EEEAFE")],
                  foreground=[("selected", "#3E2FAF")])

    # ---------------- Barra Superior Office 365 (Header) ----------------
    def _build_office_header(self):
        """Removido para maximizar a área de trabalho do canvas."""
        pass

    # ---------------- Faixa de Opções (Ribbon) Office 365 ----------------
    def _build_office_ribbon(self):
        ribbon_wrapper = tk.Frame(self, bg="#ffffff", bd=0)
        ribbon_wrapper.pack(side="top", fill="x")

        # Abas da Faixa de Opções (Tabs Bar)
        tab_bar = tk.Frame(ribbon_wrapper, bg="#F4F6FC", height=34)
        tab_bar.pack(side="top", fill="x")

        self.tab_buttons = {}
        tabs = [
            ("home", "Página Inicial"),
            ("insert", "Inserir Elementos"),
            ("view", "Exibir & Layout"),
            ("database", "DDL & Banco de Dados"),
            ("export", "Exportar Relatórios"),
        ]

        for tab_id, tab_label in tabs:
            btn = tk.Button(
                tab_bar,
                text=tab_label,
                relief="flat",
                bd=0,
                bg="#F4F6FC",
                fg="#42506B",
                activebackground="#ffffff",
                activeforeground="#18233D",
                font=("Segoe UI", 9, "bold" if tab_id == "home" else "normal"),
                padx=14,
                pady=4,
                command=lambda tid=tab_id: self.switch_ribbon_tab(tid)
            )
            btn.pack(side="left", padx=1)
            self.tab_buttons[tab_id] = btn

        self.ribbon_toggle_btn = tk.Button(tab_bar, text="▲", relief="flat", bd=0, bg="#F4F6FC", fg="#42506B",
                                           font=("Segoe UI", 9), padx=10, command=self.toggle_ribbon, cursor="hand2")
        self.ribbon_toggle_btn.pack(side="right", padx=6)
        self._tip(self.ribbon_toggle_btn, "Recolher/expandir a faixa de opções (mais espaço para o diagrama) · Ctrl+F1")
        for b in self.tab_buttons.values():
            b.bind("<Double-Button-1>", lambda _e: self.toggle_ribbon())

        # Container dos Painéis do Ribbon
        self.ribbon_container = tk.Frame(ribbon_wrapper, bg="#ffffff", height=84, bd=0)
        self.ribbon_container.pack(side="top", fill="x")
        self.ribbon_container.pack_propagate(False)

        # Linha divisória sutil
        self.ribbon_sep = tk.Frame(ribbon_wrapper, bg="#D9DFEC", height=1)
        self.ribbon_sep.pack(side="top", fill="x")

        # Constrói os painéis de cada aba
        self._build_tab_home()
        self._build_tab_insert()
        self._build_tab_view()
        self._build_tab_database()
        self._build_tab_export()

        self.switch_ribbon_tab("home")

    def switch_ribbon_tab(self, tab_id):
        if not self.ribbon_visible:
            self.toggle_ribbon()
        self.active_ribbon_tab = tab_id
        for tid, btn in self.tab_buttons.items():
            if tid == tab_id:
                btn.config(bg="#ffffff", fg="#5948E8", font=("Segoe UI", 9, "bold"))
            else:
                btn.config(bg="#F4F6FC", fg="#42506B", font=("Segoe UI", 9, "normal"))

        for tid, panel in self.ribbon_panels.items():
            if tid == tab_id:
                panel.pack(fill="both", expand=True)
            else:
                panel.pack_forget()

    def toggle_ribbon(self):
        """Recolhe/expande os botões da faixa de opções (as abas continuam visíveis)."""
        if self.ribbon_visible:
            self.ribbon_container.pack_forget()
            self.ribbon_toggle_btn.config(text="▼")
        else:
            self.ribbon_container.pack(side="top", fill="x", before=self.ribbon_sep)
            self.ribbon_toggle_btn.config(text="▲")
        self.ribbon_visible = not self.ribbon_visible
        if hasattr(self, "ribbon_var"):
            self.ribbon_var.set(self.ribbon_visible)

    def _build_document_tabs(self):
        self.document_tabs = tk.Frame(self, bg="#E9ECF6", height=36)
        self.document_tabs.pack(side="top", fill="x")
        self.document_tabs.pack_propagate(False)
        self.document_tab_items = tk.Frame(self.document_tabs, bg="#E9ECF6")
        self.document_tab_items.pack(side="left", fill="y")

    def _refresh_document_tabs(self):
        if not hasattr(self, "document_tab_items"):
            return
        for child in self.document_tab_items.winfo_children():
            child.destroy()
        for index, document in enumerate(self.documents):
            active = document is self.active_document
            bg = "#FFFFFF" if active else "#E9ECF6"
            tab = tk.Frame(self.document_tab_items, bg=bg,
                           highlightbackground="#D8DDEF", highlightthickness=1 if active else 0)
            tab.pack(side="left", fill="y", padx=(2, 0), pady=(4, 0))
            marker = " •" if document["dirty"] else ""
            tk.Button(tab, text=f"{document['name']}{marker}", relief="flat", bd=0,
                      bg=bg, fg="#4938D0" if active else "#42506B",
                      font=("Segoe UI", 9, "bold" if active else "normal"),
                      padx=12, command=lambda i=index: self._switch_document(i)).pack(side="left", fill="y")
            tk.Button(tab, text="×", relief="flat", bd=0, bg=bg, fg="#73809B",
                      font=("Segoe UI", 11), padx=8,
                      command=lambda i=index: self._close_document(i)).pack(side="left", fill="y")

    def _update_window_title(self):
        name = self.active_document["name"] if self.active_document else "Projeto sem título"
        self.title(f"{name} — Modelador ER")

    def _switch_document(self, index):
        if index < 0 or index >= len(self.documents):
            return
        previous = self.active_document
        if previous and previous.get("dirty"):
            self._autosave_document(previous)
        self.active_document = self.documents[index]
        self.project = self.active_document["project"]
        self.notation = getattr(self.project, "notation", "chen")
        self.selected_item = None
        self.link_mode = False
        self.link_src = None
        if hasattr(self, "link_btn"):
            self.link_btn.state(["!pressed"])
        self._update_inspector(None)
        self.set_notation(self.notation, mark_dirty=False)
        self._refresh_document_tabs()
        self._update_window_title()
        self._persist_session()
        self.zoom_fit()

    def new_project(self):
        existing = {document["name"] for document in self.documents}
        name = "Projeto sem título"
        suffix = 2
        while name in existing:
            name = f"Projeto sem título {suffix}"
            suffix += 1
        document = self._add_document(Project(), name)
        self.active_document = document
        self.project = document["project"]
        self.notation = "chen"
        self.selected_item = None
        self.set_notation("chen", mark_dirty=False)
        self._refresh_document_tabs()
        self._update_window_title()
        self._persist_session()
        self._write_document_snapshot(document)
        self.zoom_fit()

    def _activate_document(self, document):
        self.active_document = document
        self.project = document["project"]
        self.notation = getattr(self.project, "notation", "chen")
        self.selected_item = None
        self.link_mode = False
        self.link_src = None
        if hasattr(self, "link_btn"):
            self.link_btn.state(["!pressed"])
        self.set_notation(self.notation, mark_dirty=False)
        self._update_inspector(None)
        self._refresh_document_tabs()
        self._update_window_title()
        self._persist_session()
        self._update_undo_redo_buttons()
        self.zoom_fit()

    def _close_document(self, index):
        if index < 0 or index >= len(self.documents):
            return
        document = self.documents[index]
        has_model_data = bool(document["project"].entities or document["project"].rels)
        if document["is_autosave"] and has_model_data:
            answer = messagebox.askyesnocancel(
                "Fechar projeto sem nome",
                f"Deseja salvar '{document['name']}' em um arquivo antes de fechar?"
            )
            if answer is None:
                return
            if answer:
                self._switch_document(index)
                self.save_project_as()
                if document["is_autosave"]:
                    return
        elif document["dirty"] and document["path"]:
            answer = messagebox.askyesnocancel("Salvar alterações", f"Salvar as alterações em '{document['name']}' antes de fechar?")
            if answer is None:
                return
            if answer:
                self._write_json_atomically(document["path"], lambda t: document["project"].save(t))
                document["dirty"] = False
        if document.get("autosave_job"):
            self.after_cancel(document["autosave_job"])
        self.documents.remove(document)
        if document["is_autosave"] or not document["dirty"]:
            try:
                snapshot = Path(document["storage_path"]).resolve()
                if AUTOSAVE_DIR.resolve() in snapshot.parents:
                    snapshot.unlink(missing_ok=True)
            except OSError:
                pass
        was_active = document is self.active_document
        if not self.documents:
            self.new_project()
            return
        next_document = (self.documents[min(index, len(self.documents) - 1)]
                         if was_active else self.active_document)
        self._activate_document(next_document)

    def close_application(self):
        pending = [d for d in self.documents if d["path"] and d["dirty"]]
        for d in pending:
            answer = messagebox.askyesnocancel("Salvar alterações", f"Salvar as alterações em '{d['name']}' antes de sair?")
            if answer is None:
                return
            if answer:
                self._write_json_atomically(d["path"], lambda t, doc=d: doc["project"].save(t))
                d["dirty"] = False
            # "Não": o rascunho de recuperação continua disponível na próxima abertura
        self._flush_autosaves()
        try:
            self.oracle.close()
        except Exception:
            pass
        self.destroy()

    def _build_tab_home(self):
        panel = tk.Frame(self.ribbon_container, bg="#ffffff")
        self.ribbon_panels["home"] = panel

        g_model = self._create_ribbon_group(panel, "Modelagem")
        self.link_btn = ttk.Button(g_model, text="🔗 Relacionar", command=self.toggle_link)
        self.link_btn.pack(side="left", padx=2, pady=4)
        ttk.Button(g_model, text="✏ Editar", command=self.edit_selected_from_inspector).pack(side="left", padx=2, pady=4)
        ttk.Button(g_model, text="⧉ Duplicar", command=self.duplicate_selected).pack(side="left", padx=2, pady=4)
        ttk.Button(g_model, text="🗑 Excluir", command=self.delete_selected).pack(side="left", padx=2, pady=4)

        g_schemas = self._create_ribbon_group(panel, "Esquemas de exemplo")
        ttk.Button(g_schemas, text="🏛 Navathe", command=self.load_navathe_example).pack(side="left", padx=2, pady=4)
        ttk.Button(g_schemas, text="🧬 EER", command=self.load_eer_example).pack(side="left", padx=2, pady=4)

        g_layout = self._create_ribbon_group(panel, "Organização")
        ttk.Button(g_layout, text="🔄 Reorganizar atributos", command=self.auto_organize_attributes).pack(side="left", padx=2, pady=4)

    def _build_tab_insert(self):
        """Todos os elementos da notação de Chen / EER (Navathe) num só lugar."""
        panel = tk.Frame(self.ribbon_container, bg="#ffffff")
        self.ribbon_panels["insert"] = panel
        g_rel = self._create_ribbon_group(panel, "Relacionamentos")
        ttk.Button(g_rel, text="◇ Binário", command=self.toggle_link).pack(side="left", padx=2, pady=4)
        ttk.Button(g_rel, text="◈ Identificador", command=lambda: self.toggle_link(identifying=True)).pack(side="left", padx=2, pady=4)
        ttk.Button(g_rel, text="⬡ N-ário", command=self.toggle_nary).pack(side="left", padx=2, pady=4)
        g_eer = self._create_ribbon_group(panel, "EER (Navathe cap. 8)")
        ttk.Button(g_eer, text="△ Especialização", command=lambda: self.new_specialization("specialization")).pack(side="left", padx=2, pady=4)
        ttk.Button(g_eer, text="▽ Generalização", command=lambda: self.new_specialization("generalization")).pack(side="left", padx=2, pady=4)
        ttk.Button(g_eer, text="⊍ Categoria (União)", command=lambda: self.new_specialization("union")).pack(side="left", padx=2, pady=4)
        g_attr = self._create_ribbon_group(panel, "Atributos e Domínios")
        ttk.Button(g_attr, text="○ Atributo", command=self.add_attribute_to_selected).pack(side="left", padx=2, pady=4)
        ttk.Button(g_attr, text="📚 Domínios…", command=self.open_domains).pack(side="left", padx=2, pady=4)

    def _build_tab_view(self):
        panel = tk.Frame(self.ribbon_container, bg="#ffffff")
        self.ribbon_panels["view"] = panel

        g_spacing = self._create_ribbon_group(panel, "Espaçamento de Atributos (Chen)")
        ttk.Label(g_spacing, text="Distância:", background="#ffffff", font=("Segoe UI", 8)).pack(side="left", padx=(0, 2))
        self.spacing_var = tk.DoubleVar(value=self.global_attr_spacing)
        ttk.Scale(g_spacing, from_=55, to=170, variable=self.spacing_var, orient="horizontal", length=110,
                  command=self.on_spacing_slider).pack(side="left", padx=4, pady=4)
        for txt, val in (("Compacto", 65), ("Normal", 90), ("Amplo", 135)):
            ttk.Button(g_spacing, text=txt, width=8, command=lambda v=val: self.set_spacing_preset(v)).pack(side="left", padx=1, pady=4)

        g_viewopt = self._create_ribbon_group(panel, "Exibição")
        self.grid_var = tk.BooleanVar(value=self.show_grid)
        ttk.Checkbutton(g_viewopt, text="Grade", variable=self.grid_var, command=self.toggle_grid).grid(row=0, column=0, sticky="w", padx=4)
        self.snap_var = tk.BooleanVar(value=self.snap_on)
        ttk.Checkbutton(g_viewopt, text="Encaixar na grade", variable=self.snap_var, command=self.toggle_snap).grid(row=0, column=1, sticky="w", padx=4)
        self.sidebar_var = tk.BooleanVar(value=self.sidebar_visible)
        ttk.Checkbutton(g_viewopt, text="Paleta lateral", variable=self.sidebar_var, command=self.toggle_sidebar).grid(row=1, column=0, sticky="w", padx=4)
        self.legend_var = tk.BooleanVar(value=self.show_legend)
        ttk.Checkbutton(g_viewopt, text="Legenda", variable=self.legend_var, command=self.toggle_legend).grid(row=1, column=1, sticky="w", padx=4)

        g_card = self._create_ribbon_group(panel, "Cardinalidade (Chen)")
        self.card_style_var = tk.StringVar(value=self.project.card_style)
        ttk.Radiobutton(g_card, text="Razão 1 : N", variable=self.card_style_var, value="ratio", command=self.set_card_style).pack(anchor="w", padx=4)
        ttk.Radiobutton(g_card, text="(min, max)", variable=self.card_style_var, value="minmax", command=self.set_card_style).pack(anchor="w", padx=4)

    def _build_tab_database(self):
        panel = tk.Frame(self.ribbon_container, bg="#ffffff")
        self.ribbon_panels["database"] = panel

        g_dialect = self._create_ribbon_group(panel, "Dialeto SQL")
        ttk.Label(g_dialect, text="SGBD:", background="#ffffff").pack(side="left", padx=(0, 4))
        self.dialect_var = tk.StringVar(value="postgres")
        ttk.Combobox(g_dialect, textvariable=self.dialect_var, width=11, state="readonly",
                     values=["postgres", "mysql", "oracle"]).pack(side="left", padx=2, pady=4)

        g_ddl = self._create_ribbon_group(panel, "Ver DDL")
        ttk.Button(g_ddl, text="⚙ Transacional (F9)", command=self.show_ddl).pack(side="left", padx=3, pady=4)
        ttk.Button(g_ddl, text="⭐ Dimensional", command=self.show_kimball_ddl).pack(side="left", padx=3, pady=4)

        g_save = self._create_ribbon_group(panel, "Gravar DDL (.sql)")
        ttk.Button(g_save, text="💾 Transacional…", command=lambda: self.save_ddl("transacional")).pack(side="left", padx=3, pady=4)
        ttk.Button(g_save, text="💾 Dimensional…", command=lambda: self.save_ddl("dimensional")).pack(side="left", padx=3, pady=4)
        ttk.Button(g_save, text="💾 Ambos…", command=lambda: self.save_ddl("ambos")).pack(side="left", padx=3, pady=4)

        g_ora = self._create_ribbon_group(panel, "Oracle")
        ttk.Button(g_ora, text="🔌 Verificar e criar no Oracle…", command=self.open_oracle).pack(side="left", padx=3, pady=4)

        g_dom = self._create_ribbon_group(panel, "Qualidade do Modelo")
        ttk.Button(g_dom, text="📚 Domínios…", command=self.open_domains).pack(side="left", padx=3, pady=4)
        ttk.Button(g_dom, text="✔ Validar (F5)", command=self.validate_model).pack(side="left", padx=3, pady=4)

    def _build_tab_export(self):
        panel = tk.Frame(self.ribbon_container, bg="#ffffff")
        self.ribbon_panels["export"] = panel

        # Grupo: Relatórios
        g_rep = self._create_ribbon_group(panel, "Exportação de Relatórios")
        ttk.Button(g_rep, text="🌐 Relatório HTML Clean", command=self.export_html).pack(side="left", padx=4, pady=4)
        ttk.Button(g_rep, text="🖨 Documento PDF", command=self.export_pdf).pack(side="left", padx=4, pady=4)

    def _create_ribbon_group(self, parent, title):
        """Cria um grupo delimitado na Faixa de Opções no estilo clássico do Microsoft Office."""
        group = tk.Frame(parent, bg="#ffffff")
        group.pack(side="left", fill="y", padx=4, pady=2)

        content = tk.Frame(group, bg="#ffffff")
        content.pack(side="top", fill="both", expand=True)

        lbl = tk.Label(group, text=title, bg="#ffffff", fg="#73809B", font=("Segoe UI", 7, "bold"))
        lbl.pack(side="bottom", pady=(2, 2))

        # Divisor vertical entre grupos
        sep = tk.Frame(parent, bg="#DCE3F1", width=1)
        sep.pack(side="left", fill="y", pady=6)

        return content

    # ---------------- Área Principal (Sidebar Lucidchart + Canvas) ----------------
    def _build_main_area(self):
        self.main_container = tk.Frame(self, bg="#ffffff")
        self.main_container.pack(fill="both", expand=True)

        # Sidebar Esquerda (Paleta de Formas e Inspetor)
        self.sidebar = tk.Frame(self.main_container, bg="#F8F9FE", width=232, bd=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # Borda divisória da sidebar
        sep_side = tk.Frame(self.main_container, bg="#DCE3F1", width=1)
        sep_side.pack(side="left", fill="y")

        self._build_sidebar_content()

        # Canvas Central
        self.canvas_frame = tk.Frame(self.main_container, bg="#ffffff")
        self.canvas_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(self.canvas_frame, bg="#FBFCFF", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Eventos do mouse
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_double_click)

        # Pan com botão do meio ou botão direito
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.do_pan)
        self.canvas.bind("<ButtonRelease-2>", self.end_pan)
        self.canvas.bind("<ButtonPress-3>", self.on_right_press)
        self.canvas.bind("<B3-Motion>", self.on_right_drag)
        self.canvas.bind("<ButtonRelease-3>", self.on_right_release)
        self.canvas.bind("<Motion>", self.on_mouse_move)

        # Zoom com roda do mouse (Windows/macOS e Linux)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_mouse_wheel)
        self.canvas.bind("<Button-5>", self.on_mouse_wheel)

        # Redimensionamento
        self.canvas.bind("<Configure>", lambda e: self._schedule_render())

    def _build_sidebar_content(self):
        bg = "#F8F9FE"
        p_title = tk.Frame(self.sidebar, bg="#EEF1FA", height=32)
        p_title.pack(side="top", fill="x")
        tk.Label(p_title, text="🔷 Paleta de Formas", bg="#EEF1FA", fg="#34415F",
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=10, pady=5)

        def group(title, buttons, tips=None, open_=True):
            """Grupo recolhível: clique no título para abrir/fechar."""
            wrap = tk.Frame(self.sidebar, bg=bg)
            wrap.pack(side="top", fill="x", padx=6, pady=(3, 0))
            body = tk.Frame(wrap, bg=bg)
            head = tk.Label(wrap, text=("▾ " if open_ else "▸ ") + title, bg=bg, fg="#73809B", anchor="w",
                            font=("Segoe UI", 8, "bold"), cursor="hand2")
            head.pack(fill="x")
            if open_:
                body.pack(fill="x")

            def toggle(_e=None):
                if body.winfo_manager():
                    body.pack_forget()
                    head.config(text="▸ " + title)
                else:
                    body.pack(fill="x")
                    head.config(text="▾ " + title)
            head.bind("<Button-1>", toggle)
            for i, (txt, cmd) in enumerate(buttons):
                b = ttk.Button(body, text=txt, command=cmd, style="Side.TButton")
                b.pack(fill="x", pady=1)
                if tips and tips[i]:
                    self._tip(b, tips[i])

        group("ENTIDADES", [("▢ Entidade Forte", lambda: self.add_entity_custom(is_weak=False)),
                            ("⧉ Entidade Fraca", lambda: self.add_entity_custom(is_weak=True))],
              ["Cria uma entidade forte (duplo-clique no fundo também cria)",
               "Cria uma entidade fraca (retângulo duplo, chave parcial)"])
        group("RELACIONAMENTOS", [("◇ Relacionamento", self.toggle_link),
                                  ("◈ Identificador (duplo)", lambda: self.toggle_link(identifying=True)),
                                  ("⬡ N-ário (3+ entidades)", self.toggle_nary)],
              ["Clique em duas entidades (a mesma duas vezes = auto-relacionamento) · Ctrl+L",
               "Relacionamento que identifica uma entidade fraca: comece pela proprietária",
               "Clique em 3 ou mais entidades e tecle Enter"])
        group("EER", [("△ Especialização", lambda: self.new_specialization("specialization")),
                      ("▽ Generalização", lambda: self.new_specialization("generalization")),
                      ("⊍ Categoria (União)", lambda: self.new_specialization("union"))])
        group("ATRIBUTOS", [("○ Atributo na seleção", self.add_attribute_to_selected),
                            ("📚 Domínios…", self.open_domains)],
              ["Adiciona um atributo à entidade/relacionamento selecionado", "Listas de valores (ex.: 1=Atualizado…)"])

        p_insp = tk.Frame(self.sidebar, bg="#EEF1FA", height=28)
        p_insp.pack(side="top", fill="x", pady=(8, 0))
        tk.Label(p_insp, text="⚙ Inspetor de Seleção", bg="#EEF1FA", fg="#34415F",
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=10, pady=4)
        self.inspector_box = tk.Frame(self.sidebar, bg=bg, padx=8, pady=6)
        self.inspector_box.pack(side="top", fill="both", expand=True)
        self.lbl_insp_name = tk.Label(self.inspector_box, text="Nenhum selecionado", bg=bg, fg="#73809B",
                                      font=("Segoe UI", 9, "bold"), wraplength=200, justify="left")
        self.lbl_insp_name.pack(anchor="w")
        self.lbl_insp_info = tk.Label(self.inspector_box, text="Clique num elemento para inspecionar; duplo-clique para editar.",
                                      bg=bg, fg="#98A4BB", font=("Segoe UI", 8), wraplength=200, justify="left")
        self.lbl_insp_info.pack(anchor="w", pady=(3, 6))
        self.btn_insp_edit = ttk.Button(self.inspector_box, text="✏ Editar (F2)", command=self.edit_selected_from_inspector, style="Side.TButton")
        self.btn_insp_edit.pack(fill="x")

    def _build_office_statusbar(self):
        status = tk.Frame(self, bg="#302779", height=28)
        status.pack(side="bottom", fill="x")
        status.pack_propagate(False)
        tk.Label(status, text="Pronto", bg="#302779", fg="#ffffff", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(10, 8))
        self.status_msg = tk.Label(status, text="Notação ativa: Chen / EER (Navathe)", bg="#302779", fg="#DCD7FF", font=("Segoe UI", 8))
        self.status_msg.pack(side="left")

        right_box = tk.Frame(status, bg="#302779")
        right_box.pack(side="right", padx=10)
        self.oracle_btn = tk.Button(right_box, text="⚪ Oracle: desconectado", bg="#302779", fg="#DCD7FF",
                                    activebackground="#4938D0", activeforeground="#ffffff", relief="flat", bd=0,
                                    font=("Segoe UI", 8, "bold"), padx=8, cursor="hand2", command=self.open_oracle)
        self.oracle_btn.pack(side="left", padx=(0, 12))
        self._tip(self.oracle_btn, "Abrir a janela do Oracle (a conexão fica aberta enquanto o app estiver aberto)")
        self.status_counts = tk.Label(right_box, text="", bg="#302779", fg="#AFA6F7", font=("Segoe UI", 8))
        self.status_counts.pack(side="left", padx=(0, 12))
        tk.Label(right_box, text="Zoom", bg="#302779", fg="#AFA6F7", font=("Segoe UI", 8)).pack(side="left")
        self.status_zoom_lbl = tk.Label(right_box, text="100%", bg="#302779", fg="#ffffff", font=("Segoe UI", 8, "bold"), width=5)
        self.status_zoom_lbl.pack(side="left")

    def _refresh_oracle_status(self):
        if not hasattr(self, "oracle_btn"):
            return
        if self.oracle.connected:
            self.oracle_btn.config(text=f"🟢 {self.oracle.label()}", fg="#BBF7D0")
        else:
            self.oracle_btn.config(text="⚪ Oracle: desconectado", fg="#DCD7FF")

    # ---------------- Alternância e Layout ----------------
    def set_notation(self, mode, mark_dirty=True):
        if mode == "table":
            mode = "barker"
        changed = getattr(self, "notation", None) != mode
        self.notation = mode
        self.project.notation = mode
        self._sync_notation_buttons()
        lbl = "Chen / EER (Navathe)" if mode == "chen" else "Barker (Lógico)"
        self.status_msg.config(text=f"Notação ativa: {lbl}")
        if changed and mark_dirty:
            self._mark_active_dirty()
        if changed:
            self.zoom_fit()
        else:
            self.render()

    def _sync_notation_buttons(self):
        if not (hasattr(self, 'btn_chen_mode') and hasattr(self, 'btn_table_mode')):
            return
        for btn, mode in ((self.btn_chen_mode, "chen"), (self.btn_table_mode, "barker")):
            on = self.notation == mode
            btn.config(bg="#EEEAFE" if on else "#f1f5f9", fg="#3E2FAF" if on else "#42506B",
                       font=("Segoe UI", 8, "bold" if on else "normal"))
        if hasattr(self, "card_style_var"):
            self.card_style_var.set(self.project.card_style)

    def toggle_snap(self):
        self.snap_on = self.snap_var.get()

    def toggle_legend(self):
        self.show_legend = self.legend_var.get()
        self.render()

    def set_card_style(self):
        self.project.card_style = self.card_style_var.get()
        self._mark_active_dirty()
        self.render()

    def _snap(self, v):
        return round(v / SNAP) * SNAP if self.snap_on else v

    def _schedule_render(self):
        """Agrupa pedidos de redesenho (arraste/rolagem) num único render por ciclo ocioso."""
        if self._render_job is None:
            self._render_job = self.after_idle(self._do_scheduled_render)

    def _do_scheduled_render(self):
        self._render_job = None
        self.render()

    def toggle_notation(self):
        self.set_notation("table" if self.notation == "chen" else "chen")

    def toggle_grid(self):
        self.show_grid = self.grid_var.get()
        self.render()

    def toggle_sidebar(self):
        self.sidebar_visible = self.sidebar_var.get()
        if self.sidebar_visible:
            self.sidebar.pack(side="left", fill="y", before=self.canvas_frame)
        else:
            self.sidebar.pack_forget()

    # ---------------- Ajuste de Espaçamento dos Atributos ----------------
    def on_spacing_slider(self, val):
        self.global_attr_spacing = float(val)
        self._schedule_render()

    def set_spacing_preset(self, val):
        self.global_attr_spacing = float(val)
        self.spacing_var.set(val)
        self.render()

    def auto_organize_attributes(self):
        """Redefine os deslocamentos personalizados para que todos os atributos se organizem automaticamente."""
        self._push_undo()
        for e in self.project.entities:
            for a in e.attrs:
                a.offset_x = None
                a.offset_y = None
        for r in self.project.rels:
            for a in r.attrs:
                a.offset_x = None
                a.offset_y = None
        self._mark_active_dirty()
        self.render()
        if hasattr(self, 'status_msg'):
            self.status_msg.config(text="Posições dos atributos reorganizadas automaticamente.")

    # ---------------- Transformações de Coordenadas (Model <-> Screen) ----------------
    def to_screen(self, mx, my):
        return (mx + self.pan_x) * self.zoom, (my + self.pan_y) * self.zoom

    def to_model(self, sx, sy):
        return (sx / self.zoom) - self.pan_x, (sy / self.zoom) - self.pan_y

    def font_scaled(self, base_family, base_size, *styles):
        scaled = max(6, int(round(base_size * self.zoom)))
        if styles:
            return (base_family, scaled, *styles)
        return (base_family, scaled)

    def _text_metrics(self, text, font_spec):
        """Mede texto em pixels com a mesma fonte e DPI usados pelo Canvas."""
        font = self._measure_fonts.get(font_spec)
        if font is None:
            font = tkfont.Font(root=self, font=font_spec)
            self._measure_fonts[font_spec] = font
        return font.measure(text), font.metrics("linespace")

    def _fit_canvas_font(self, text, base_size, max_width, *styles):
        """Reduz a fonte apenas quando necessário para manter o texto dentro da forma."""
        font = self.font_scaled("Segoe UI", base_size, *styles)
        measured, _ = self._text_metrics(text, font)
        if measured <= max_width:
            return font

        size = max(3, int(font[1] * max_width / max(1, measured)))
        candidate = (font[0], size, *font[2:])
        while size > 3 and self._text_metrics(text, candidate)[0] > max_width:
            size -= 1
            candidate = (font[0], size, *font[2:])
        return candidate

    def _ellipse_size(self, text, base_size=8.5):
        font = self.font_scaled("Segoe UI", base_size)
        text_w, text_h = self._text_metrics(text, font)
        return (max(66.0, text_w / self.zoom + 18),
                max(25.0, text_h / self.zoom + 8))

    def _relationship_bounds(self, name):
        font = self.font_scaled("Segoe UI", 8.2, "bold")
        text_w, text_h = self._text_metrics(name, font)
        return (max(46.0, text_w / self.zoom / 2 + 16),
                max(26.0, text_h / self.zoom / 2 + 10))

    # ---------------- Zoom & Pan Controls ----------------
    def set_zoom(self, new_zoom, center_sx=None, center_sy=None):
        new_zoom = max(0.25, min(3.0, new_zoom))
        if center_sx is None or center_sy is None:
            center_sx = (self.canvas.winfo_width() or 1200) / 2
            center_sy = (self.canvas.winfo_height() or 800) / 2

        mx, my = self.to_model(center_sx, center_sy)
        self.zoom = new_zoom
        self.pan_x = center_sx / self.zoom - mx
        self.pan_y = center_sy / self.zoom - my

        self._update_zoom_label()
        self.render()

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.15)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.15)

    def zoom_reset(self):
        self.set_zoom(1.0)

    def zoom_fit(self):
        """Centraliza e ajusta o zoom para enquadrar TODO o modelo (entidades, atributos e
        relacionamentos) na tela. Isso só move a câmera (zoom/pan) — nunca altera posições
        de entidades, atributos ou relacionamentos."""
        self._bk_cache = None
        bounds = self._full_model_bounds()
        if not bounds:
            self.zoom = 1.0
            self.pan_x = 40.0
            self.pan_y = 40.0
            self._update_zoom_label()
            self.render()
            return

        min_x, min_y, max_x, max_y = bounds
        margin = 60
        self._fit_to_bounds(min_x - margin, min_y - margin, max_x + margin, max_y + margin,
                             min_zoom=0.35, max_zoom=1.8, margin=0)

    def _full_model_bounds(self):
        """Limites (coordenadas de modelo) de tudo que é desenhado. Somente leitura."""
        if not self.project.entities and not self.project.rels:
            return None
        self._bk_cache = None
        xs, ys = [], []

        def add_box(cx, cy, w, h):
            xs.extend((cx - w / 2, cx + w / 2))
            ys.extend((cy - h / 2, cy + h / 2))

        if self.notation == "barker":
            for e in self.project.entities:
                x, y, w, h = self._ent_box(e)
                xs.extend((x, x + w))
                ys.extend((y, y + h))
            for r in self.project.rels:
                g = self._bk_rel_geom(r)
                if g:
                    for half in g["halves"]:
                        for px, py in half["pts"]:
                            xs.append(px)
                            ys.append(py)
                    xs.append(g["label"][0] + 30)
                    xs.append(g["label"][0] - 30)
            for sp in self.project.specs:
                if sp.is_union:
                    sx, sy = self._spec_pos(sp)
                    add_box(sx, sy, SPEC_R * 2, SPEC_R * 2)
        else:
            for e in self.project.entities:
                w, h = ENTITY_W, ENTITY_H_CH
                add_box(e.x + w / 2, e.y + h / 2, w, h)
                cx0, cy0 = e.x + w / 2, e.y + h / 2
                n = len(e.attrs)
                spacing = getattr(e, 'attr_spacing', self.global_attr_spacing)
                for i, a in enumerate(e.attrs):
                    ox, oy = self._calc_attr_pos(e, i, n, cx0, cy0, w, h, a, spacing)
                    tw, th = self._ellipse_size(a.name)
                    add_box(ox, oy, tw, th)
                    if a.is_composite and a.sub_attrs:
                        sub_count = len(a.sub_attrs)
                        for j, sub in enumerate(a.sub_attrs):
                            if sub.offset_x is not None and sub.offset_y is not None:
                                sx, sy = cx0 + sub.offset_x, cy0 + sub.offset_y
                            else:
                                sx, sy = ox + (j - (sub_count - 1) / 2) * 74, oy - 48
                            sub_w, sub_h = self._ellipse_size(sub.name, 7.8)
                            add_box(sx, sy, sub_w, sub_h)
            for r in self.project.rels:
                mx, my = self._rel_pos(r)
                dw, dh = self._relationship_bounds(r.name)
                add_box(mx, my, dw * 2, dh * 2)
                for k, ra in enumerate(r.attrs):
                    if ra.offset_x is not None and ra.offset_y is not None:
                        rox, roy = mx + ra.offset_x, my + ra.offset_y
                    else:
                        rox, roy = mx + (k - (len(r.attrs) - 1) / 2) * 85, my - 54
                    tw, th = self._ellipse_size(ra.name)
                    add_box(rox, roy, tw, th)
            for sp in self.project.specs:
                sx, sy = self._spec_pos(sp)
                add_box(sx, sy, SPEC_R * 2, SPEC_R * 2)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def _fit_to_bounds(self, min_x, min_y, max_x, max_y, min_zoom=0.25, max_zoom=3.0, margin=24):
        """Ajusta zoom/pan (somente câmera) para enquadrar e centralizar a caixa de
        coordenadas de modelo informada. Usado por 'Ajustar Tudo' e por 'Zoom em Seleção'."""
        min_x -= margin
        min_y -= margin
        max_x += margin
        max_y += margin

        box_w = max(40, max_x - min_x)
        box_h = max(40, max_y - min_y)

        actual_w = self.canvas.winfo_width()
        actual_h = self.canvas.winfo_height()
        win_w = actual_w if actual_w > 100 else 1240
        win_h = actual_h if actual_h > 100 else 740

        fit_zoom = min(win_w / box_w, win_h / box_h) * 0.92
        self.zoom = max(min_zoom, min(max_zoom, fit_zoom))

        self.pan_x = (win_w / self.zoom - box_w) / 2 - min_x
        self.pan_y = (win_h / self.zoom - box_h) / 2 - min_y

        self._update_zoom_label()
        self.render()

    def toggle_zoom_select_mode(self):
        """Ativa/desativa o modo 'Zoom em Seleção': arraste uma área do canvas e, ao
        soltar o botão do mouse, a câmera centraliza e amplia exatamente aquela área."""
        self.zoom_select_mode = not self.zoom_select_mode
        if self.zoom_select_mode:
            self.link_mode = False
            self.link_src = None
            if hasattr(self, 'link_btn'):
                self.link_btn.state(["!pressed"])
        if hasattr(self, 'zoom_select_btn'):
            self.zoom_select_btn.state(["pressed"] if self.zoom_select_mode else ["!pressed"])
        self.canvas.config(cursor="tcross" if self.zoom_select_mode else "arrow")
        if hasattr(self, 'status_msg'):
            if self.zoom_select_mode:
                self.status_msg.config(text="🔲 Arraste uma área do diagrama para dar zoom nela.")
            else:
                self.status_msg.config(text="Pronto")

    def _update_zoom_label(self):
        pct = f"{int(round(self.zoom * 100))}%"
        if hasattr(self, 'zoom_var'):
            self.zoom_var.set(pct)
        if hasattr(self, 'status_zoom_lbl'):
            self.status_zoom_lbl.config(text=pct)

    # ---------------- Eventos do Mouse e Interações ----------------
    def on_mouse_wheel(self, event):
        num = getattr(event, "num", None)
        delta = 120 if num == 4 else (-120 if num == 5 else event.delta)
        if (event.state & 0x4) != 0:
            factor = 1.12 if delta > 0 else (1.0 / 1.12)
            self.set_zoom(self.zoom * factor, center_sx=event.x, center_sy=event.y)
        else:
            step = 45.0 / self.zoom
            if (event.state & 0x1) != 0:
                self.pan_x += (step if delta > 0 else -step)
            else:
                self.pan_y += (step if delta > 0 else -step)
            self._schedule_render()

    def start_pan(self, event):
        self.is_panning = True
        self.pan_start_x = event.x
        self.pan_start_y = event.y
        self.pan_start_px = self.pan_x
        self.pan_start_py = self.pan_y
        self.canvas.config(cursor="fleur")

    def do_pan(self, event):
        if not self.is_panning:
            return
        dx = event.x - self.pan_start_x
        dy = event.y - self.pan_start_y
        self.pan_x = self.pan_start_px + dx / self.zoom
        self.pan_y = self.pan_start_py + dy / self.zoom
        self._schedule_render()

    def end_pan(self, event):
        self.is_panning = False
        self.canvas.config(cursor="")

    def on_mouse_down(self, event):
        self.canvas.focus_set()
        if self.zoom_select_mode:
            self._marquee_start = (event.x, event.y)
            self._marquee_rect_id = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y, outline="#DC2626", width=2, dash=(6, 3))
            return

        mx, my = self.to_model(event.x, event.y)
        self._mouse_model = (mx, my)
        self._press_screen = (event.x, event.y)
        self._drag_started = False

        # Modo relacionamento n-ário: clique alterna entidades; Enter conclui.
        if self.nary_mode:
            e = self.entity_at(mx, my)
            if e:
                if e.id in self.nary_sel:
                    self.nary_sel.remove(e.id)
                else:
                    self.nary_sel.append(e.id)
                self.status_msg.config(text=f"N-ário: {len(self.nary_sel)} entidade(s) escolhida(s) — "
                                            f"clique nas demais, Enter conclui (mín. 3), Esc cancela.")
                self.render()
            return

        # Modo ligar entidades (relacionamento binário)
        if self.link_mode:
            e = self.entity_at(mx, my)
            if e:
                if self.link_src is None:
                    self.link_src = e.id
                    self.status_msg.config(text=f"Origem: {e.name} — clique na entidade de destino (Esc cancela).")
                    self.render()
                else:
                    src_id, tgt_id, ident = self.link_src, e.id, self.link_identifying
                    self._cancel_link_modes()
                    self.canvas.config(cursor="")
                    dlg = CardinalityDialog(self, src_id, tgt_id, self._finish_link)
                    if ident:
                        dlg.identifying_var.set(True)
                        dlg.part2_var.set("Total (Linha dupla)")
                    self.render()
            return

        hit = self._hit_test(mx, my)
        self.drag_target = None
        if hit:
            kind = hit[0]
            if kind == "attr":
                _, ent, attr, ox, oy, is_sub, parent_attr = hit
                self.selected_item = ("attr", ent.id, attr.id)
                self.drag_target = ("attr", ent.id, attr.id, is_sub, parent_attr.id if parent_attr else None)
                self.drag_offset_x, self.drag_offset_y = mx - ox, my - oy
                self._update_inspector("attr", attr, ent)
            elif kind == "rel_attr":
                _, rel, attr, rox, roy = hit
                self.selected_item = ("rel_attr", rel.id, attr.id)
                self.drag_target = ("rel_attr", rel.id, attr.id)
                self.drag_offset_x, self.drag_offset_y = mx - rox, my - roy
                self._update_inspector("rel_attr", attr, rel)
            elif kind == "spec":
                sp = hit[1]
                px, py = self._spec_pos(sp)
                self.selected_item = ("spec", sp.id)
                self.drag_target = ("spec", sp.id)
                self.drag_offset_x, self.drag_offset_y = mx - px, my - py
                self._update_inspector("spec", sp)
            elif kind == "rel":
                r = hit[1]
                self.selected_item = ("rel", r.id)
                if self.notation == "chen" or r.is_nary:
                    self.drag_target = ("rel", r.id)
                    rmx, rmy = self._rel_pos(r)
                    self.drag_offset_x, self.drag_offset_y = mx - rmx, my - rmy
                self._update_inspector("rel", r)
            elif kind == "bk_attr":
                _, e, attr, is_sub, parent = hit
                self.selected_item = ("attr", e.id, attr.id)
                if self._bk_layout().get(e.id, {}).get("top", True):
                    self.drag_target = ("entity", e.id)
                    self.drag_offset_x, self.drag_offset_y = mx - e.x, my - e.y
                self._update_inspector("attr", attr, e)
            else:  # entity
                e = hit[1]
                self.selected_item = ("entity", e.id)
                if self.notation == "chen" or self._bk_layout().get(e.id, {}).get("top", True):
                    self.drag_target = ("entity", e.id)
                    self.drag_offset_x, self.drag_offset_y = mx - e.x, my - e.y
                else:
                    self.status_msg.config(text="Subtipos ficam dentro do supertipo (Barker): arraste o supertipo.")
                self._update_inspector("entity", e)
            if self.drag_target:
                self._begin_drag_snapshot()
            self.render()
            return

        # Fundo do canvas -> desmarca e inicia pan
        self.selected_item = None
        self._update_inspector(None)
        self.start_pan(event)
        self.render()

    def _begin_drag_snapshot(self):
        """Guarda o estado do projeto antes de um arraste, para permitir desfazer (Ctrl+Z)
        apenas se o arraste de fato mover algo (evita lotar o histórico com simples cliques)."""
        try:
            self._drag_prestate = self.project.to_dict()
        except Exception:
            self._drag_prestate = None
        self._drag_moved = False

    def on_mouse_drag(self, event):
        if self.zoom_select_mode:
            if self._marquee_start and self._marquee_rect_id:
                sx0, sy0 = self._marquee_start
                self.canvas.coords(self._marquee_rect_id, sx0, sy0, event.x, event.y)
            return
        if self.is_panning:
            self.do_pan(event)
            return
        if not self.drag_target:
            return
        # Só começa a arrastar depois de 4 px (evita mover sem querer ao clicar)
        if not getattr(self, "_drag_started", False):
            px, py = getattr(self, "_press_screen", (event.x, event.y))
            if math.hypot(event.x - px, event.y - py) < 4:
                return
            self._drag_started = True

        mx, my = self.to_model(event.x, event.y)
        target_type = self.drag_target[0]

        if target_type == "entity":
            e = self.project.find_entity(self.drag_target[1])
            if e:
                e.x = self._snap(mx - self.drag_offset_x)
                e.y = self._snap(my - self.drag_offset_y)
        elif target_type == "rel":
            r = self.project.find_rel(self.drag_target[1])
            if r:
                r.pos_x = self._snap(mx - self.drag_offset_x)
                r.pos_y = self._snap(my - self.drag_offset_y)
        elif target_type == "spec":
            sp = self.project.find_spec(self.drag_target[1])
            if sp:
                sp.pos_x = self._snap(mx - self.drag_offset_x)
                sp.pos_y = self._snap(my - self.drag_offset_y)
        elif target_type == "attr":
            eid, aid, is_sub, parent_id = self.drag_target[1:5]
            e = self.project.find_entity(eid)
            if e:
                cx0, cy0 = e.x + ENTITY_W / 2, e.y + ENTITY_H_CH / 2
                target = None
                if is_sub and parent_id:
                    parent_attr = next((a for a in e.attrs if a.id == parent_id), None)
                    target = next((s for s in parent_attr.sub_attrs if s.id == aid), None) if parent_attr else None
                else:
                    target = next((a for a in e.attrs if a.id == aid), None)
                if target:
                    target.offset_x = (mx - self.drag_offset_x) - cx0
                    target.offset_y = (my - self.drag_offset_y) - cy0
        elif target_type == "rel_attr":
            r = self.project.find_rel(self.drag_target[1])
            if r:
                rmx, rmy = self._rel_pos(r)
                attr = next((a for a in r.attrs if a.id == self.drag_target[2]), None)
                if attr:
                    attr.offset_x = (mx - self.drag_offset_x) - rmx
                    attr.offset_y = (my - self.drag_offset_y) - rmy
        self._drag_moved = True
        if self.notation == "barker" and len(self.project.rels) > 8:
            self._fast_routing = True      # cotovelos simples durante o arrasto; rota completa ao soltar
        self._mark_active_dirty()
        self._schedule_render()

    def on_mouse_up(self, event):
        # Finaliza o modo "Zoom em Seleção": calcula a área e aplica o zoom/centralização,
        # sem alterar nenhuma posição do modelo (apenas a câmera pan/zoom).
        if self.zoom_select_mode:
            sx0, sy0 = self._marquee_start or (event.x, event.y)
            sx1, sy1 = event.x, event.y
            if self._marquee_rect_id:
                self.canvas.delete(self._marquee_rect_id)
                self._marquee_rect_id = None
            self._marquee_start = None

            if abs(sx1 - sx0) > 8 and abs(sy1 - sy0) > 8:
                mx0, my0 = self.to_model(min(sx0, sx1), min(sy0, sy1))
                mx1, my1 = self.to_model(max(sx0, sx1), max(sy0, sy1))
                self._fit_to_bounds(mx0, my0, mx1, my1, min_zoom=0.25, max_zoom=3.0, margin=24)
                if hasattr(self, 'status_msg'):
                    self.status_msg.config(text="🔍 Zoom aplicado à área selecionada.")

            self.zoom_select_mode = False
            if hasattr(self, 'zoom_select_btn'):
                self.zoom_select_btn.state(["!pressed"])
            self.canvas.config(cursor="arrow")
            return

        if self.is_panning:
            self.end_pan(event)

        # Se algo foi de fato arrastado, registra o estado anterior no histórico de desfazer.
        if getattr(self, "_drag_moved", False) and getattr(self, "_drag_prestate", None) is not None:
            self._push_undo(self._drag_prestate)
        self._drag_prestate = None
        self._drag_moved = False
        self.drag_target = None
        if self._fast_routing:
            self._fast_routing = False
            self.render()

    def on_canvas_double_click(self, event):
        if self.link_mode or self.nary_mode or self.zoom_select_mode:
            return
        self.is_panning = False
        mx, my = self.to_model(event.x, event.y)
        hit = self._hit_test(mx, my)
        if not hit:
            self.add_entity_custom(at=(mx, my))
            return
        kind = hit[0]
        if kind == "entity":
            self._open_entity_dialog(hit[1])
        elif kind == "attr":
            self._open_entity_dialog(hit[1], focus=hit[2].id)
        elif kind == "bk_attr":
            self._open_entity_dialog(hit[1], focus=hit[2].id)
        elif kind == "rel":
            self._open_rel_dialog(hit[1])
        elif kind == "rel_attr":
            self._open_rel_dialog(hit[1])
        elif kind == "spec":
            self.edit_specialization(hit[1])

    # ---------------- Inspetor de Seleção Lateral ----------------
    def _update_inspector(self, kind, item=None, parent_item=None):
        if not kind:
            self.lbl_insp_name.config(text="Nenhum selecionado", fg="#73809B")
            self.lbl_insp_info.config(text="Clique num elemento para inspecionar; duplo-clique para editar.")
            self.btn_insp_edit.state(["disabled"])
            self._update_counts()
            return
        self.btn_insp_edit.state(["!disabled"])
        if kind == "entity":
            t = "Entidade Fraca" if item.is_weak else "Entidade Forte"
            sp = self.project.spec_of_child(item.id)
            if sp:
                par = ", ".join(x.name for x in (self.project.find_entity(i) for i in sp.parent_ids) if x)
                t = f"Subclasse de {par}" if not sp.is_union else f"Categoria de {par}"
            self.lbl_insp_name.config(text=f"🏛 {item.name}", fg="#5948E8")
            info = f"Tipo: {t}\nAtributos: {len(item.attrs)}"
            if item.description:
                info += f"\n{item.description}"
        elif kind == "rel":
            ident = " [Identificador]" if item.is_identifying else ""
            self.lbl_insp_name.config(text=f"◇ {item.name}{ident}", fg="#b45309")
            if item.is_nary:
                names = ", ".join(x.name for x in (self.project.find_entity(i) for i, *_ in item.participants()) if x)
                info = f"Relacionamento {len(item.participants())}-ário\n{names}"
            else:
                info = f"Cardinalidade: {item.card1}:{item.card2}\nParticipação: {item.part1} / {item.part2}"
            if item.attrs:
                info += f"\nAtributos: {len(item.attrs)}"
            if item.description:
                info += f"\n{item.description}"
        elif kind == "spec":
            par = ", ".join(x.name for x in (self.project.find_entity(i) for i in item.parent_ids) if x)
            kids = ", ".join(x.name for x in (self.project.find_entity(i) for i in item.child_ids) if x)
            self.lbl_insp_name.config(text=f"△ {item.label()}", fg="#0f766e")
            rest = "" if item.is_union else f"\n{'Disjunta' if item.disjointness == 'd' else 'Sobreposta'}"
            info = f"Super: {par}\nSub: {kids}{rest}, {'total' if item.completeness == 'total' else 'parcial'}"
            if item.defining_attr:
                info += f"\nDefinidor: {item.defining_attr}"
        else:  # attr / rel_attr
            self.lbl_insp_name.config(text=f"🏷 {item.name}", fg="#4263EB")
            dom = self.project.find_domain(item.domain_id) if getattr(item, "domain_id", None) else None
            info = f"Pertence a: {parent_item.name}\nTipo: {item.type}\nClassificação: {item.attr_type}"
            if dom:
                info += f"\nDomínio: {dom.name} ({dom.summary()})"
            if item.description:
                info += f"\n{item.description}"
        self.lbl_insp_info.config(text=info)
        self._update_counts()

    def _update_counts(self):
        if hasattr(self, "status_counts"):
            p = self.project
            self.status_counts.config(
                text=f"{len(p.entities)} entidades · {len(p.rels)} relac. · {len(p.specs)} espec. · {len(p.domains)} domínios")

    def edit_selected_from_inspector(self):
        if not self.selected_item:
            self.status_msg.config(text="Selecione um elemento para editar (ou dê duplo-clique nele).")
            return
        kind = self.selected_item[0]
        if kind == "entity":
            e = self.project.find_entity(self.selected_item[1])
            if e:
                self._open_entity_dialog(e)
        elif kind in ("rel", "rel_attr"):
            r = self.project.find_rel(self.selected_item[1])
            if r:
                self._open_rel_dialog(r)
        elif kind == "attr":
            e = self.project.find_entity(self.selected_item[1])
            if e:
                self._open_entity_dialog(e, focus=self.selected_item[2])
        elif kind == "spec":
            sp = self.project.find_spec(self.selected_item[1])
            if sp:
                self.edit_specialization(sp)

    # ---------------- Operações sobre o modelo ----------------
    def add_entity(self):
        self.add_entity_custom(is_weak=False)

    def _unique_entity_name(self, base):
        names = {e.name.lower() for e in self.project.entities}
        name, n = base, 1
        while name.lower() in names:
            n += 1
            name = f"{base}{n}"
        return name

    def add_entity_custom(self, is_weak=False, at=None):
        self._push_undo()
        if at is None:
            at = self.to_model((self.canvas.winfo_width() or 1200) / 2, (self.canvas.winfo_height() or 800) / 2)
        name = self._unique_entity_name("NovaEntidadeFraca" if is_weak else "NovaEntidade")
        attrs = [Attribute("id", "INTEGER", attr_type="partial_key" if is_weak else "pk")]
        new_e = Entity(name=name, x=self._snap(at[0] - ENTITY_W / 2), y=self._snap(at[1] - ENTITY_H_CH / 2),
                       is_weak=is_weak, attrs=attrs)
        self.project.add_entity(new_e)
        self._mark_active_dirty()
        self.selected_item = ("entity", new_e.id)
        self._update_inspector("entity", new_e)
        self.render()
        self._open_entity_dialog(new_e)

    def _open_entity_dialog(self, e, focus=None):
        return EntityDialog(self, e, self._mark_model_changed, on_before_save=self._push_undo,
                            project=self.project, focus_attr_id=focus)

    def _open_rel_dialog(self, r):
        return RelationshipDialog(self, r, self._mark_model_changed, self.project, on_before_save=self._push_undo)

    def load_navathe_example(self):
        self._load_example(Project.create_navathe_company_example, "Exemplo Empresa",
                           "Carregar Exemplo Navathe",
                           "Deseja carregar o esquema clássico 'Empresa' do livro Sistemas de Banco de Dados "
                           "(Navathe 6ª Edição)?")

    def load_eer_example(self):
        self._load_example(Project.create_eer_example, "Exemplo EER",
                           "Carregar Exemplo EER",
                           "Deseja carregar o exemplo EER (especialização, categoria/união e domínio)?")

    def _load_example(self, factory, name, title, question):
        p = self.project
        if (p.entities or p.rels) and not messagebox.askyesno(title, question + "\nIsso substituirá o modelo atual "
                                                                  "(você pode desfazer com Ctrl+Z)."):
            return
        self._push_undo()
        self.project = factory()
        self.project.notation = self.notation
        self.active_document["project"] = self.project
        if not self.active_document["path"]:
            self.active_document["name"] = name
            self.active_document["storage_path"] = str(AUTOSAVE_DIR / f"project_{id(self.project)}.json")
            self.active_document["is_autosave"] = True
        self._mark_active_dirty()
        self._refresh_document_tabs()
        self._update_window_title()
        self.selected_item = None
        self._cancel_link_modes()
        self._update_inspector(None)
        self.zoom_fit()

    def toggle_link(self, identifying=False):
        turning_on = not (self.link_mode and self.link_identifying == bool(identifying))
        self._cancel_link_modes()
        if turning_on:
            self.link_mode = True
            self.link_identifying = bool(identifying)
            self.link_btn.state(["pressed"])
            self.canvas.config(cursor="crosshair")
            self.status_msg.config(text="Relacionar: clique na 1ª entidade" +
                                        (" (a proprietária) " if identifying else " ") +
                                        "e depois na 2ª (clique na mesma para auto-relacionamento). Esc cancela.")
        else:
            self.canvas.config(cursor="")
            self.status_msg.config(text="Pronto")
        self.render()

    def toggle_nary(self):
        turning_on = not self.nary_mode
        self._cancel_link_modes()
        if turning_on:
            self.nary_mode = True
            self.canvas.config(cursor="crosshair")
            self.status_msg.config(text="N-ário: clique nas 3 ou mais entidades participantes; Enter conclui, Esc cancela.")
        else:
            self.canvas.config(cursor="")
        self.render()

    def finish_nary(self):
        ids = list(self.nary_sel)
        if len(ids) < 3:
            self.status_msg.config(text="Escolha ao menos 3 entidades para um relacionamento n-ário.")
            return
        self._cancel_link_modes()
        self.canvas.config(cursor="")
        self._push_undo()
        rel = Relationship(entity1_id=ids[0], entity2_id=ids[1], card1="N", card2="N", name="relaciona",
                           extra_parts=[{"entity_id": i, "card": "N", "part": "partial", "role": ""} for i in ids[2:]])
        self.project.rels.append(rel)
        self._mark_active_dirty()
        self.selected_item = ("rel", rel.id)
        self._update_inspector("rel", rel)
        self.render()
        self._open_rel_dialog(rel)

    def delete_selected(self):
        if not self.selected_item:
            self.status_msg.config(text="Selecione um elemento para excluir.")
            return
        kind = self.selected_item[0]
        p = self.project
        if kind == "entity":
            e = p.find_entity(self.selected_item[1])
            if e and messagebox.askyesno("Excluir", f"Excluir a entidade '{e.name}', seus relacionamentos e "
                                                    f"especializações?"):
                self._push_undo()
                p.remove_entity(e.id)
                self._after_delete()
        elif kind == "rel":
            r = p.find_rel(self.selected_item[1])
            if r and messagebox.askyesno("Excluir", f"Excluir o relacionamento '{r.name}'?"):
                self._push_undo()
                p.remove_rel(r.id)
                self._after_delete()
        elif kind == "spec":
            sp = p.find_spec(self.selected_item[1])
            if sp and messagebox.askyesno("Excluir", f"Excluir esta {sp.label().lower()}?"):
                self._push_undo()
                p.remove_spec(sp.id)
                self._after_delete()
        elif kind in ("attr", "rel_attr"):
            owner, container, attr = self._locate_attr(self.selected_item)
            if attr and messagebox.askyesno("Excluir", f"Excluir o atributo '{attr.name}'?"):
                self._push_undo()
                container.remove(attr)
                self._after_delete()

    def _after_delete(self):
        self.selected_item = None
        self._mark_active_dirty()
        self._update_inspector(None)
        self.render()

    def _locate_attr(self, sel):
        """(dono, lista_que_contém, atributo) de uma seleção ('attr'|'rel_attr', dono_id, attr_id)."""
        owner = self.project.find_entity(sel[1]) if sel[0] == "attr" else self.project.find_rel(sel[1])
        if not owner:
            return None, None, None
        def find(lst):
            for a in lst:
                if a.id == sel[2]:
                    return lst, a
                res = find(a.sub_attrs)
                if res:
                    return res
            return None
        res = find(owner.attrs)
        return (owner, res[0], res[1]) if res else (owner, None, None)

    # ---------------- Hit Testing ----------------
    def entity_at(self, mx, my):
        best, best_key = None, None
        L = self._bk_layout() if self.notation == "barker" else None
        for idx, e in enumerate(self.project.entities):
            x, y, w, h = self._ent_box(e)
            if x <= mx <= x + w and y <= my <= y + h:
                depth = L[e.id]["depth"] if L and e.id in L else 0
                key = (depth, idx)
                if best_key is None or key > best_key:
                    best, best_key = e, key
        return best

    def _hit_test(self, mx, my):
        """Elemento sob o ponto: (tipo, ...) ou None. Prioriza o que está por cima no desenho."""
        if self.notation == "chen":
            h = self.attr_at(mx, my)
            if h:
                return ("attr",) + h
            h = self.rel_attr_at(mx, my)
            if h:
                return ("rel_attr",) + h
            sp = self.spec_at(mx, my)
            if sp:
                return ("spec", sp)
            r = self.rel_at(mx, my)
            if r:
                return ("rel", r)
            e = self.entity_at(mx, my)
            return ("entity", e) if e else None
        e = self.entity_at(mx, my)
        if e:
            row = self.bk_row_at(e, mx, my)
            if row:
                return ("bk_attr", e, row["attr"], row["sub"], row["parent"])
            tag = self.spec_at(mx, my)
            if tag:
                return ("spec", tag)
            return ("entity", e)
        sp = self.spec_at(mx, my)
        if sp:
            return ("spec", sp)
        r = self.rel_at(mx, my)
        return ("rel", r) if r else None

    def spec_at(self, mx, my):
        if self.notation == "barker":
            for L in self._bk_layout().values():
                for sp, (tx, ty, tw, th) in L.get("tags", []):
                    if tx <= mx <= tx + tw and ty <= my <= ty + th:
                        return sp
        for sp in reversed(self.project.specs):
            if self.notation == "barker" and not sp.is_union:
                continue
            if not self._spec_geom(sp):
                continue
            sx, sy = self._spec_pos(sp)
            if math.hypot(mx - sx, my - sy) <= SPEC_R + 3:
                return sp
        return None

    @staticmethod
    def _dist_seg(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(px - x1, py - y1)
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    def rel_at(self, mx, my):
        if self.notation == "barker":
            tol = 7 / self.zoom
            for r in reversed(self.project.rels):
                g = self._bk_rel_geom(r)
                if not g:
                    continue
                if g["box"]:
                    x1, y1, x2, y2 = g["box"]
                    if x1 <= mx <= x2 and y1 <= my <= y2:
                        return r
                for half in g["halves"]:
                    pts = half["pts"]
                    for a, b in zip(pts, pts[1:]):
                        if self._dist_seg(mx, my, a[0], a[1], b[0], b[1]) <= tol:
                            return r
                lx, ly = g["label"]
                hw = self._text_metrics(r.name, ("Segoe UI", 8))[0] / 2 + 4
                if abs(mx - lx) <= hw and abs(my - ly) <= 9:
                    return r
            return None
        for r in reversed(self.project.rels):
            rmx, rmy = self._rel_pos(r)
            dw, dh = self._relationship_bounds(r.name)
            if abs(mx - rmx) <= dw and abs(my - rmy) <= dh:
                return r
        return None

    def attr_at(self, mx, my):
        """Verifica se as coordenadas (mx, my) atingiram algum atributo ou sub-atributo."""
        for e in reversed(self.project.entities):
            w, h = ENTITY_W, ENTITY_H_CH
            cx0, cy0 = e.x + w / 2, e.y + h / 2
            n = len(e.attrs)
            spacing = getattr(e, 'attr_spacing', self.global_attr_spacing)

            for i, a in enumerate(e.attrs):
                ox, oy = self._calc_attr_pos(e, i, n, cx0, cy0, w, h, a, spacing)
                tw, th = self._ellipse_size(a.name)

                # Verifica sub-atributos primeiro
                if a.is_composite and a.sub_attrs:
                    sub_count = len(a.sub_attrs)
                    for j, sub in enumerate(a.sub_attrs):
                        if sub.offset_x is not None and sub.offset_y is not None:
                            sox = cx0 + sub.offset_x
                            soy = cy0 + sub.offset_y
                        else:
                            sox = ox + (j - (sub_count - 1) / 2) * 74
                            soy = oy - 48
                        sub_w, sub_h = self._ellipse_size(sub.name, 7.8)
                        if abs(mx - sox) <= (sub_w - 4) / 2 and abs(my - soy) <= (sub_h - 3) / 2:
                            return e, sub, sox, soy, True, a

                if abs(mx - ox) <= tw / 2 and abs(my - oy) <= th / 2:
                    return e, a, ox, oy, False, None
        return None

    def rel_attr_at(self, mx, my):
        for r in reversed(self.project.rels):
            rmx, rmy = self._rel_pos(r)
            for k, ra in enumerate(r.attrs):
                if ra.offset_x is not None and ra.offset_y is not None:
                    rox = rmx + ra.offset_x
                    roy = rmy + ra.offset_y
                else:
                    rox = rmx + (k - (len(r.attrs) - 1) / 2) * 85
                    roy = rmy - 54
                tw, th = self._ellipse_size(ra.name)
                if abs(mx - rox) <= tw / 2 and abs(my - roy) <= th / 2:
                    return r, ra, rox, roy
        return None

    def _calc_attr_pos(self, e, i, n, cx0, cy0, w, h, a, spacing):
        if a.offset_x is not None and a.offset_y is not None:
            return cx0 + a.offset_x, cy0 + a.offset_y
        if n <= 3:
            ox = cx0 + (i - (n - 1) / 2) * (spacing * 1.1)
            oy = e.y - (spacing - 20)
        else:
            angle = -math.pi * 0.85 + (i / max(1, n - 1)) * (math.pi * 0.70)
            ox = cx0 + math.cos(angle) * (w / 2 + spacing - 15)
            oy = cy0 + math.sin(angle) * (h / 2 + spacing - 25)
        return ox, oy

    def _rel_pos(self, r):
        if (r.pos_x is not None and r.pos_y is not None) and (self.notation == "chen" or r.is_nary):
            return r.pos_x, r.pos_y
        ents = [self.project.find_entity(i) for i, *_ in r.participants()]
        if any(e is None for e in ents):
            return 200, 200
        e1, e2 = ents[0], ents[1]
        if not r.is_nary and e1.id == e2.id:
            x, y, w, h = self._ent_box(e1)
            return x - 70, y + 110
        boxes = [self._ent_box(e) for e in ents]
        cx = sum(b[0] + b[2] / 2 for b in boxes) / len(boxes)
        cy = sum(b[1] + b[3] / 2 for b in boxes) / len(boxes)
        return cx, cy

    def _ent_box(self, e):
        """(x, y, w, h) da entidade na notação ativa (Barker usa o layout calculado)."""
        if self.notation == "barker":
            L = self._bk_layout().get(e.id)
            if L:
                return L["x"], L["y"], L["w"], L["h"]
        return e.x, e.y, ENTITY_W, ENTITY_H_CH

    def _finish_link(self, a_id, b_id, card1, card2, name, part1, part2, role1, role2, is_identifying):
        self._push_undo()
        rel = Relationship(entity1_id=a_id, entity2_id=b_id, card1=card1, card2=card2, name=name,
                           part1=part1, part2=part2, role1=role1, role2=role2, is_identifying=is_identifying)
        self.project.rels.append(rel)
        self._mark_active_dirty()
        self.selected_item = ("rel", rel.id)
        self._update_inspector("rel", rel)
        self.status_msg.config(text=f"Relacionamento '{name}' criado. Duplo-clique para editar atributos e papéis.")
        self.render()

    # ---------------- Renderização Principal ----------------
    def render(self):
        self._bk_cache = None
        self.canvas.delete("all")
        if self.show_grid:
            self._draw_grid()
        if self.notation == "chen":
            self._render_chen()
        else:
            self._render_barker()
        self._draw_overlays()
        self._update_counts()

    def _draw_grid(self):
        spacing = 40.0
        scaled_sp = spacing * self.zoom
        if scaled_sp < 15:
            return

        win_w = self.canvas.winfo_width() or 1200
        win_h = self.canvas.winfo_height() or 800

        start_x = (self.pan_x * self.zoom) % scaled_sp
        start_y = (self.pan_y * self.zoom) % scaled_sp

        dot_col = "#DCE3F1"
        x = start_x
        while x < win_w:
            y = start_y
            while y < win_h:
                self.canvas.create_rectangle(x, y, x + 1.2, y + 1.2, fill=dot_col, outline=dot_col)
                y += scaled_sp
            x += scaled_sp

    # ---------------- Notação de Chen (Navathe 6ª Edição) ----------------
    def _render_chen(self):
        for sp in self.project.specs:
            self._draw_spec(sp)
        for r in self.project.rels:
            self._draw_chen_relationship(r)
        for e in self.project.entities:
            self._draw_chen_entity(e)

    def _draw_chen_entity(self, e):
        w = ENTITY_W
        h = ENTITY_H_CH

        sx1, sy1 = self.to_screen(e.x, e.y)
        sx2, sy2 = self.to_screen(e.x + w, e.y + h)
        scx = (sx1 + sx2) / 2
        scy = (sy1 + sy2) / 2

        is_selected = (self.selected_item == ("entity", e.id))
        is_link_source = (self.link_mode and self.link_src == e.id) or (self.nary_mode and e.id in self.nary_sel)

        border_col = "#dc2626" if is_link_source else ("#5948E8" if is_selected else "#25304A")
        line_w = max(1.5, 2.2 * self.zoom) if is_selected else max(1.2, 1.6 * self.zoom)
        bg_col = ("#FFE8C7" if is_selected else "#FFF4E3") if e.is_weak else ("#EEEAFE" if is_selected else "#EAF2FF")
        name_font = self._fit_canvas_font(e.name, 9.5, (w - 18) * self.zoom, "bold")

        if e.is_weak:
            # Entidade Fraca: Retângulo Duplo
            inset = max(3, int(round(4.5 * self.zoom)))
            self.canvas.create_rectangle(sx1, sy1, sx2, sy2, fill=bg_col, outline=border_col, width=line_w)
            self.canvas.create_rectangle(sx1 + inset, sy1 + inset, sx2 - inset, sy2 - inset,
                                         fill="", outline=border_col, width=max(1.0, 1.3 * self.zoom))
            self.canvas.create_text(scx, scy, text=e.name,
                                    font=name_font, fill="#18233D")
        else:
            # Entidade Regular: Retângulo Simples
            self.canvas.create_rectangle(sx1, sy1, sx2, sy2, fill=bg_col, outline=border_col, width=line_w)
            self.canvas.create_text(scx, scy, text=e.name,
                                    font=name_font, fill="#18233D")

        # Renderizar atributos
        n = len(e.attrs)
        cx0, cy0 = e.x + w / 2, e.y + h / 2
        spacing = getattr(e, 'attr_spacing', self.global_attr_spacing)

        for i, a in enumerate(e.attrs):
            ox, oy = self._calc_attr_pos(e, i, n, cx0, cy0, w, h, a, spacing)

            bx, by = self._rect_border_point(e.x, e.y, w, h, ox, oy)
            sbx, sby = self.to_screen(bx, by)
            sox, soy = self.to_screen(ox, oy)

            # Linha conectando entidade ao atributo
            self.canvas.create_line(sbx, sby, sox, soy, fill="#73809B", width=max(1.0, 1.3 * self.zoom))
            self._draw_chen_attribute_oval(ox, oy, a, e, cx0, cy0)

    def _rect_border_point(self, rx, ry, rw, rh, px, py):
        cx, cy = rx + rw / 2, ry + rh / 2
        dx, dy = px - cx, py - cy
        if dx == 0 and dy == 0:
            return cx, cy
        half_w = rw / 2
        half_h = rh / 2
        scale_x = half_w / abs(dx) if dx != 0 else float("inf")
        scale_y = half_h / abs(dy) if dy != 0 else float("inf")
        scale = min(scale_x, scale_y)
        return cx + dx * scale, cy + dy * scale

    def _draw_chen_attribute_oval(self, ox, oy, a, entity, cx0, cy0):
        sox, soy = self.to_screen(ox, oy)
        oval_w, oval_h = self._ellipse_size(a.name)
        tw = oval_w * self.zoom
        th = oval_h * self.zoom

        x1, y1 = sox - tw / 2, soy - th / 2
        x2, y2 = sox + tw / 2, soy + th / 2

        is_selected = (self.selected_item == ("attr", entity.id, a.id))
        outline_col = "#4263EB" if is_selected else "#25304A"
        line_w = max(1.5, 2.0 * self.zoom) if is_selected else max(1.0, 1.4 * self.zoom)

        if a.is_multivalued:
            # Multivalorado: Elipse Dupla
            inset = max(2.5, 3.2 * self.zoom)
            self.canvas.create_oval(x1, y1, x2, y2, fill="#E7FAF5", outline=outline_col, width=line_w)
            self.canvas.create_oval(x1 + inset, y1 + inset, x2 - inset, y2 - inset,
                                    fill="#E7FAF5", outline=outline_col, width=max(1.0, 1.2 * self.zoom))
        elif a.is_derived:
            # Derivado: Elipse Tracejada
            d1 = max(3, int(round(4 * self.zoom)))
            d2 = max(2, int(round(3 * self.zoom)))
            self.canvas.create_oval(x1, y1, x2, y2, fill="#FFF0F5", outline=outline_col, width=line_w, dash=(d1, d2))
        else:
            self.canvas.create_oval(x1, y1, x2, y2, fill="#E7FAF5", outline=outline_col, width=line_w)

        # Texto do atributo
        attr_font = self.font_scaled("Segoe UI", 8.5)
        text_width, _ = self._text_metrics(a.name, attr_font)
        self.canvas.create_text(sox, soy, text=a.name, font=attr_font, fill="#18233D")

        # Sublinhados
        text_w = max(14 * self.zoom, text_width)
        under_y = soy + 6.5 * self.zoom

        if a.is_pk:
            # Chave Primária: Sublinhado Sólido
            self.canvas.create_line(sox - text_w / 2, under_y, sox + text_w / 2, under_y,
                                     fill="#18233D", width=max(1.0, 1.3 * self.zoom))
        elif a.is_partial_key:
            # Chave Parcial: Sublinhado Tracejado
            d1 = max(2, int(round(3 * self.zoom)))
            d2 = max(2, int(round(2 * self.zoom)))
            self.canvas.create_line(sox - text_w / 2, under_y, sox + text_w / 2, under_y,
                                     fill="#18233D", width=max(1.0, 1.3 * self.zoom), dash=(d1, d2))

        # Atributo Composto: Ramificação para os sub-atributos
        if a.is_composite and a.sub_attrs:
            sub_count = len(a.sub_attrs)
            for j, sub in enumerate(a.sub_attrs):
                if sub.offset_x is not None and sub.offset_y is not None:
                    so_sub_x = cx0 + sub.offset_x
                    so_sub_y = cy0 + sub.offset_y
                else:
                    so_sub_x = ox + (j - (sub_count - 1) / 2) * 74
                    so_sub_y = oy - 48

                ss_x, ss_y = self.to_screen(so_sub_x, so_sub_y)

                self.canvas.create_line(sox, soy - th / 2, ss_x, ss_y + 11 * self.zoom,
                                         fill="#73809B", width=max(1.0, 1.1 * self.zoom))

                sub_model_w, sub_model_h = self._ellipse_size(sub.name, 7.8)
                sub_w = max(54, sub_model_w - 4) * self.zoom
                sub_h = max(22, sub_model_h - 3) * self.zoom
                is_sub_sel = (self.selected_item == ("attr", entity.id, sub.id))
                sub_out = "#4263EB" if is_sub_sel else "#42506B"

                self.canvas.create_oval(ss_x - sub_w / 2, ss_y - sub_h / 2, ss_x + sub_w / 2, ss_y + sub_h / 2,
                                        fill="#ffffff", outline=sub_out, width=max(1.0, 1.1 * self.zoom))
                self.canvas.create_text(ss_x, ss_y, text=sub.name, font=self.font_scaled("Segoe UI", 7.8), fill="#25304A")

    def _side_label(self, r, card, part, role, inline=False):
        """Texto junto à linha do relacionamento: razão (1,N,M) ou (mín,máx), com o papel se houver."""
        if self.project.card_style == "minmax":
            txt = f"({'1' if part == 'total' else '0'},{'1' if card == '1' else 'N'})"
        else:
            txt = f"({card})" if role else card
        if not role:
            return txt
        return f"{role} {txt}" if inline else f"{role}\n{txt}"

    def _draw_chen_relationship(self, r):
        parts = [(self.project.find_entity(eid), card, part, role) for eid, card, part, role in r.participants()]
        if any(p[0] is None for p in parts):
            return
        e1, e2 = parts[0][0], parts[1][0]
        mx, my = self._rel_pos(r)
        smx, smy = self.to_screen(mx, my)

        is_selected = (self.selected_item == ("rel", r.id))
        outline_col = "#4263EB" if is_selected else "#25304A"
        line_w = max(1.5, 2.2 * self.zoom) if is_selected else max(1.2, 1.6 * self.zoom)
        dw = self._relationship_bounds(r.name)[0] * self.zoom
        dh = self._relationship_bounds(r.name)[1] * self.zoom
        pts_outer = [smx, smy - dh, smx + dw, smy, smx, smy + dh, smx - dw, smy]
        lblfont = self.font_scaled("Segoe UI", 8.8, "bold")

        if not r.is_nary and e1.id == e2.id:
            p1_x, p1_y = e1.x, e1.y + 14
            p2_x, p2_y = e1.x + 30, e1.y + ENTITY_H_CH
            sp1_x, sp1_y = self.to_screen(p1_x, p1_y)
            sp2_x, sp2_y = self.to_screen(p2_x, p2_y)
            self._draw_chen_connection(sp1_x, sp1_y, smx - dw * 0.5, smy - dh * 0.5, r.part1 == "total")
            self._draw_chen_connection(sp2_x, sp2_y, smx + dw * 0.2, smy + dh * 0.8, r.part2 == "total")
            f8 = self.font_scaled("Segoe UI", 8, "bold")
            self.canvas.create_text(sp1_x - 28 * self.zoom, sp1_y + 10 * self.zoom,
                                    text=self._side_label(r, r.card1, r.part1, r.role1, True), font=f8, fill="#18233D")
            self.canvas.create_text(sp2_x - 28 * self.zoom, sp2_y + 15 * self.zoom,
                                    text=self._side_label(r, r.card2, r.part2, r.role2, True), font=f8, fill="#18233D")
        else:
            for ent, card, part, role in parts:
                bx, by, bw, bh = self._ent_box(ent)
                b_x, b_y = self._rect_border_point(bx, by, bw, bh, mx, my)
                sbx, sby = self.to_screen(b_x, b_y)
                self._draw_chen_connection(sbx, sby, smx, smy, part == "total")
                tx = sbx + (smx - sbx) * 0.30
                ty = sby + (smy - sby) * 0.30 - 10 * self.zoom
                self._draw_label_with_halo(tx, ty, self._side_label(r, card, part, role), font=lblfont)

        if r.is_identifying:
            inset = max(3, int(round(4.5 * self.zoom)))
            self.canvas.create_polygon(pts_outer, fill="#F1ECFF", outline=outline_col, width=line_w)
            pts_inner = [smx, smy - dh + inset, smx + dw - inset, smy, smx, smy + dh - inset, smx - dw + inset, smy]
            self.canvas.create_polygon(pts_inner, fill="#ffffff", outline=outline_col, width=max(1.0, 1.2 * self.zoom))
        else:
            self.canvas.create_polygon(pts_outer, fill="#ffffff", outline=outline_col, width=line_w)
        self.canvas.create_text(smx, smy, text=r.name, font=self.font_scaled("Segoe UI", 8.2, "bold"), fill="#18233D")

        for k, ra in enumerate(r.attrs):
            if ra.offset_x is not None and ra.offset_y is not None:
                rox, roy = mx + ra.offset_x, my + ra.offset_y
            else:
                rox, roy = mx + (k - (len(r.attrs) - 1) / 2) * 85, my - 54
            srox, sroy = self.to_screen(rox, roy)
            self.canvas.create_line(smx, smy - dh, srox, sroy + 12 * self.zoom, fill="#73809B", width=max(1.0, 1.2 * self.zoom))
            oval_w, oval_h = self._ellipse_size(ra.name)
            tw, th = oval_w * self.zoom, oval_h * self.zoom
            is_ra_sel = (self.selected_item == ("rel_attr", r.id, ra.id))
            self.canvas.create_oval(srox - tw / 2, sroy - th / 2, srox + tw / 2, sroy + th / 2, fill="#ffffff",
                                    outline="#4263EB" if is_ra_sel else "#25304A", width=max(1.0, 1.3 * self.zoom))
            self.canvas.create_text(srox, sroy, text=ra.name, font=self.font_scaled("Segoe UI", 8.5), fill="#18233D")

    def _draw_chen_connection(self, x1, y1, x2, y2, is_total):
        line_col = "#42506B"
        base_w = max(1.2, 1.5 * self.zoom)
        if not is_total:
            self.canvas.create_line(x1, y1, x2, y2, fill=line_col, width=base_w)
        else:
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length > 0:
                offset = max(2.0, 2.5 * self.zoom)
                nx = -dy / length * offset
                ny = dx / length * offset
                self.canvas.create_line(x1 + nx, y1 + ny, x2 + nx, y2 + ny, fill=line_col, width=base_w)
                self.canvas.create_line(x1 - nx, y1 - ny, x2 - nx, y2 - ny, fill=line_col, width=base_w)
            else:
                self.canvas.create_line(x1, y1, x2, y2, fill=line_col, width=base_w)

    def _draw_label_with_halo(self, x, y, text, font):
        pad = 4 * self.zoom
        lines = text.split("\n")
        tw = max(12 * self.zoom, *(self._text_metrics(line, font)[0] for line in lines)) + pad * 2
        line_h = self._text_metrics("Ag", font)[1]
        th = max(12 * self.zoom, len(lines) * line_h) + pad * 2
        self.canvas.create_rectangle(x - tw / 2, y - th / 2, x + tw / 2, y + th / 2,
                                     fill="#ffffff", outline="", width=0)
        self.canvas.create_text(x, y, text=text, font=font, fill="#18233D", justify="center")

    # ---------------- Diagramação por Tabelas (Relacional / Lucidchart style) ----------------
    # ---------------- Arquivo / Exportações ----------------
    def save_project(self):
        document = self.active_document
        if not document:
            return False
        if not document["path"]:
            return self.save_project_as()
        try:
            self._write_json_atomically(document["path"], lambda t: document["project"].save(t))
            self._write_document_snapshot(document)
            document["dirty"] = False
            if document.get("autosave_job"):
                self.after_cancel(document["autosave_job"])
                document["autosave_job"] = None
            self.status_msg.config(text=f"Salvo em {document['path']}")
            self._refresh_document_tabs()
            self._persist_session()
            return True
        except OSError as ex:
            messagebox.showerror("Salvar", f"Não foi possível salvar o projeto:\n{ex}")
            return False

    def save_project_as(self):
        document = self.active_document
        if not document:
            return False
        initial_name = document["name"]
        if initial_name == "Projeto sem título":
            initial_name = "projeto_er"
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile=f"{initial_name}.json",
            filetypes=[("Projeto ER (JSON)", "*.json")],
        )
        if not path:
            return False
        target = Path(path).resolve()
        for other in self.documents:
            if other is document or not other["path"]:
                continue
            if Path(other["path"]).resolve() == target:
                messagebox.showerror("Salvar como", "Este projeto já está aberto em outra aba.")
                return False

        try:
            self._write_json_atomically(target, lambda temporary: document["project"].save(temporary))
        except OSError as ex:
            messagebox.showerror("Salvar como", f"Não foi possível salvar o projeto:\n{ex}")
            return False

        document["path"] = str(target)
        document["name"] = target.stem
        document["is_autosave"] = False
        document["dirty"] = False
        if document.get("autosave_job"):
            self.after_cancel(document["autosave_job"])
            document["autosave_job"] = None
        try:
            self._write_document_snapshot(document)
        except OSError:
            pass
        self.status_msg.config(text=f"Salvo: {document['name']}")
        self._refresh_document_tabs()
        self._update_window_title()
        self._persist_session()
        return True

    def load_project(self):
        path = filedialog.askopenfilename(
            filetypes=[("Projeto ER (JSON)", "*.json"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        try:
            target = Path(path).resolve()
            for index, document in enumerate(self.documents):
                if document["path"] and Path(document["path"]).resolve() == target:
                    self._switch_document(index)
                    return
            document = self._add_document(Project.load(target), target.stem, path=target)
            self._activate_document(document)
        except Exception as ex:
            messagebox.showerror("Abrir", f"Erro ao abrir o projeto:\n{ex}")

    def _confirm_validation(self):
        errors = [m for sev, m in validator.validate_project(self.project) if sev == "erro"]
        if not errors:
            return True
        return messagebox.askyesno("Modelo com erros",
                                   f"O modelo tem {len(errors)} erro(s), por exemplo:\n• " + "\n• ".join(errors[:4]) +
                                   "\n\nO DDL pode sair incompleto. Gerar mesmo assim?")

    def show_ddl(self):
        if not self._confirm_validation():
            return
        dialect = self.dialect_var.get()
        gen = lambda d: generate_ddl(self.project, d)
        try:
            ddl = gen(dialect)
        except Exception as ex:
            messagebox.showerror("Gerar DDL", str(ex))
            return
        DDLWindow(self, dialect, ddl, generator=gen, file_prefix=self.active_document["name"])

    def show_kimball_ddl(self):
        dialect = self.dialect_var.get()
        gen = lambda d: generate_kimball_ddl(self.project, d)
        try:
            ddl = gen(dialect)
        except Exception as ex:
            messagebox.showerror("Gerar DDL dimensional", str(ex))
            return
        DDLWindow(self, dialect, ddl, title_text=f"DDL Dimensional (Kimball) — {dialect.upper()}",
                  generator=gen, file_prefix=self.active_document["name"] + "_dimensional")

    def validate_model(self):
        ValidationWindow(self, validator.validate_project(self.project))

    def open_domains(self):
        DomainManagerDialog(self, self.project, self._mark_model_changed, self._push_undo)

    def export_html(self):
        path = filedialog.asksaveasfilename(defaultextension=".html", filetypes=[("HTML", "*.html")])
        if path:
            report_export.export_html(self.project, path)
            messagebox.showinfo("Exportar HTML", "Relatório HTML exportado com sucesso com visual clean.")

    def export_pdf(self):
        path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            report_export.export_pdf(self.project, path)
            messagebox.showinfo("Exportar PDF", "PDF exportado com sucesso.")
        except RuntimeError as ex:
            messagebox.showerror("Exportar PDF", str(ex))

    # ---------------- Botão direito: pan ou menu de contexto ----------------
    def on_right_press(self, event):
        self._rc_start = (event.x, event.y)
        self._rc_moved = False
        self.start_pan(event)

    def on_right_drag(self, event):
        if self._rc_start and math.hypot(event.x - self._rc_start[0], event.y - self._rc_start[1]) > 4:
            self._rc_moved = True
        if self._rc_moved:
            self.do_pan(event)

    def on_right_release(self, event):
        self.end_pan(event)
        if not self._rc_moved and self._rc_start:
            self.show_context_menu(event)
        self._rc_start = None

    def on_mouse_move(self, event):
        if self.zoom_select_mode or self.is_panning or self.drag_target:
            return
        now = time.time()
        if now - getattr(self, "_last_move", 0) < 0.04:
            return
        self._last_move = now
        mx, my = self.to_model(event.x, event.y)
        self._mouse_model = (mx, my)
        if self.link_mode or self.nary_mode:
            cur = "crosshair"
        else:
            cur = "hand2" if self._hit_test(mx, my) else ""
        if self.canvas.cget("cursor") != cur:
            self.canvas.config(cursor=cur)

    def show_context_menu(self, event):
        mx, my = self.to_model(event.x, event.y)
        hit = self._hit_test(mx, my)
        menu = tk.Menu(self, tearoff=0)
        p = self.project
        if hit is None:
            self.selected_item = None
            self._update_inspector(None)
            self.render()
            menu.add_command(label="▢ Nova entidade forte aqui", command=lambda: self.add_entity_custom(False, at=(mx, my)))
            menu.add_command(label="⧉ Nova entidade fraca aqui", command=lambda: self.add_entity_custom(True, at=(mx, my)))
            menu.add_separator()
            menu.add_command(label="🔗 Relacionar entidades", command=self.toggle_link)
            menu.add_command(label="⬡ Relacionamento n-ário", command=self.toggle_nary)
            menu.add_command(label="⌒ Novo arco de exclusividade (Barker)…", command=self.open_arc_dialog)
            menu.add_separator()
            menu.add_command(label="⚡ Auto-alinhar todos os relacionamentos", command=self.auto_align_all_relationships)
            menu.add_command(label="📥 Importar modelo conceitual…", command=self.open_import_dialog)
            menu.add_command(label="⛶ Ajustar tudo (Ctrl+0)", command=self.zoom_fit)
            menu.add_command(label="✔ Validar modelo (F5)", command=self.validate_model)
        else:
            kind = hit[0]
            if kind in ("entity", "attr", "bk_attr"):
                e = hit[1]
                if kind == "entity":
                    self.selected_item = ("entity", e.id)
                    self._update_inspector("entity", e)
                self.render()
                menu.add_command(label=f"✏ Editar '{e.name}'…  (F2)", command=lambda: self._open_entity_dialog(e))
                menu.add_command(label="○ Adicionar atributo", command=lambda: self._add_attr_to_entity(e))
                menu.add_command(label="⌒ Criar arco de exclusividade (Barker)…", command=lambda: self.open_arc_dialog())
                menu.add_command(label="⧉ Duplicar entidade", command=lambda: self._duplicate_entity(e))
                menu.add_separator()
                menu.add_command(label="🔗 Relacionar a partir daqui", command=lambda: self._start_link_from(e))
                menu.add_command(label="△ Criar especialização (esta é a superclasse)",
                                 command=lambda: self.new_specialization("specialization", parent=e))
                menu.add_command(label="▽ Criar generalização (esta é a subclasse)",
                                 command=lambda: self.new_specialization("generalization", child=e))
                menu.add_command(label="⊍ Criar categoria (união) com esta como subclasse",
                                 command=lambda: self.new_specialization("union", child=e))
                for sp in p.specs_of_parent(e.id) + ([p.spec_of_child(e.id)] if p.spec_of_child(e.id) else []):
                    menu.add_command(label=f"✏ Editar {sp.label().lower()} relacionada…",
                                     command=lambda s=sp: self.edit_specialization(s))
                menu.add_separator()
                menu.add_command(label="Tornar " + ("forte" if e.is_weak else "fraca"), command=lambda: self._toggle_weak(e))
                menu.add_command(label="🗑 Excluir entidade", command=lambda: self._delete_item(("entity", e.id)))
            elif kind in ("rel", "rel_attr"):
                r = hit[1]
                if kind == "rel":
                    self.selected_item = ("rel", r.id)
                    self._update_inspector("rel", r)
                self.render()
                menu.add_command(label=f"✏ Editar '{r.name}'…  (F2)", command=lambda: self._open_rel_dialog(r))
                menu.add_command(label="○ Adicionar atributo ao relacionamento", command=lambda: self._add_attr_to_rel(r))
                if not r.is_nary:
                    menu.add_command(label="━ Alinhar horizontalmente (mesmo Y) · Ctrl+H",
                                     command=lambda rel=r: self.align_rel_horizontal(rel))
                    menu.add_command(label="┃ Alinhar verticalmente (mesmo X) · Ctrl+Shift+H",
                                     command=lambda rel=r: self.align_rel_vertical(rel))
                    menu.add_command(label="⚡ Auto-alinhar relacionamento",
                                     command=lambda rel=r: self.auto_align_selected_rel())
                    menu.add_separator()
                    menu.add_command(label="⇄ Inverter direção (lado 1 ⇄ lado 2)", command=lambda: self.swap_relationship(r))
                    menu.add_command(label=("◇ Tornar regular" if r.is_identifying else "◈ Tornar identificador"),
                                     command=lambda: self._toggle_identifying(r))
                menu.add_command(label="⧉ Duplicar", command=lambda: self._duplicate_rel(r))
                menu.add_separator()
                menu.add_command(label="🗑 Excluir relacionamento", command=lambda: self._delete_item(("rel", r.id)))
            elif kind == "spec":
                sp = hit[1]
                self.selected_item = ("spec", sp.id)
                self._update_inspector("spec", sp)
                self.render()
                menu.add_command(label=f"✏ Editar {sp.label().lower()}…  (F2)", command=lambda: self.edit_specialization(sp))
                menu.add_command(label="🗑 Excluir", command=lambda: self._delete_item(("spec", sp.id)))
            if kind in ("attr", "bk_attr"):
                attr = hit[2]
                self.selected_item = ("attr", hit[1].id, attr.id)
                self._update_inspector("attr", attr, hit[1])
                self.render()
                menu.add_separator()
                menu.add_command(label=f"✏ Editar atributo '{attr.name}'", command=lambda: self._open_entity_dialog(hit[1], focus=attr.id))
                menu.add_command(label="⧉ Duplicar atributo", command=lambda: self._duplicate_attr(("attr", hit[1].id, attr.id)))
                menu.add_command(label="🗑 Excluir atributo", command=lambda: self._delete_item(("attr", hit[1].id, attr.id)))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_item(self, sel):
        self.selected_item = sel
        self.delete_selected()

    def _start_link_from(self, e):
        self._cancel_link_modes()
        self.link_mode = True
        self.link_src = e.id
        self.link_btn.state(["pressed"])
        self.canvas.config(cursor="crosshair")
        self.status_msg.config(text=f"Origem: {e.name} — clique na entidade de destino (Esc cancela).")
        self.render()

    def _toggle_weak(self, e):
        self._push_undo()
        e.is_weak = not e.is_weak
        self._mark_active_dirty()
        self._update_inspector("entity", e)
        self.render()

    def _toggle_identifying(self, r):
        self._push_undo()
        r.is_identifying = not r.is_identifying
        self._mark_active_dirty()
        self.render()

    def swap_relationship(self, r):
        self._push_undo()
        r.entity1_id, r.entity2_id = r.entity2_id, r.entity1_id
        r.card1, r.card2 = r.card2, r.card1
        r.part1, r.part2 = r.part2, r.part1
        r.role1, r.role2 = r.role2, r.role1
        self._mark_active_dirty()
        self._update_inspector("rel", r)
        self.render()

    # ---------------- Atributos / duplicação / deslocamento ----------------
    def _add_attr_to_entity(self, e):
        dlg = self._open_entity_dialog(e)
        dlg.add_row()

    def _add_attr_to_rel(self, r):
        dlg = self._open_rel_dialog(r)
        dlg.add_rel_attr()

    def add_attribute_to_selected(self):
        sel = self.selected_item
        if sel and sel[0] in ("entity", "attr"):
            e = self.project.find_entity(sel[1])
            if e:
                self._add_attr_to_entity(e)
                return
        if sel and sel[0] in ("rel", "rel_attr"):
            r = self.project.find_rel(sel[1])
            if r:
                self._add_attr_to_rel(r)
                return
        self.status_msg.config(text="Selecione uma entidade ou relacionamento para adicionar um atributo.")

    @staticmethod
    def _clone_attr(a):
        c = Attribute.from_dict(a.to_dict())
        def renew(x):
            x.id = new_id("a")
            x.offset_x = x.offset_y = None
            for s in x.sub_attrs:
                renew(s)
        renew(c)
        return c

    def _duplicate_attr(self, sel):
        owner, container, attr = self._locate_attr(sel)
        if not attr:
            return
        self._push_undo()
        c = self._clone_attr(attr)
        c.name += "_copia"
        if c.attr_type in ("pk", "partial_key"):
            c.attr_type = "simple"
        container.insert(container.index(attr) + 1, c)
        self._mark_active_dirty()
        self.render()

    def _duplicate_entity(self, e):
        self._push_undo()
        c = Entity.from_dict(e.to_dict())
        c.id = new_id("e")
        c.name = self._unique_entity_name(e.name + "_copia")
        c.x, c.y = e.x + 2 * SNAP, e.y + 2 * SNAP
        c.attrs = [self._clone_attr(a) for a in e.attrs]
        self.project.add_entity(c)
        self._mark_active_dirty()
        self.selected_item = ("entity", c.id)
        self._update_inspector("entity", c)
        self.render()

    def _duplicate_rel(self, r):
        self._push_undo()
        c = Relationship.from_dict(r.to_dict())
        c.id = new_id("r")
        c.name += "_copia"
        c.attrs = [self._clone_attr(a) for a in r.attrs]
        if r.pos_x is not None:
            c.pos_x, c.pos_y = r.pos_x + 2 * SNAP, r.pos_y + 2 * SNAP
        self.project.rels.append(c)
        self._mark_active_dirty()
        self.selected_item = ("rel", c.id)
        self._update_inspector("rel", c)
        self.render()

    def duplicate_selected(self):
        sel = self.selected_item
        if not sel:
            self.status_msg.config(text="Selecione um elemento para duplicar.")
            return
        if sel[0] == "entity":
            e = self.project.find_entity(sel[1])
            if e:
                self._duplicate_entity(e)
        elif sel[0] == "rel":
            r = self.project.find_rel(sel[1])
            if r:
                self._duplicate_rel(r)
        elif sel[0] in ("attr", "rel_attr"):
            self._duplicate_attr(sel)

    def nudge_selected(self, dx, dy):
        sel = self.selected_item
        if not sel or sel[0] not in ("entity", "rel", "spec"):
            return
        obj = {"entity": self.project.find_entity, "rel": self.project.find_rel,
               "spec": self.project.find_spec}[sel[0]](sel[1])
        if not obj:
            return
        if sel[0] == "entity":
            if self.notation == "barker" and not self._bk_layout().get(obj.id, {}).get("top", True):
                return
        elif sel[0] == "rel" and self.notation == "barker" and not obj.is_nary:
            return
        now = time.time()
        if now - self._last_nudge > 1.0:
            self._push_undo()
        self._last_nudge = now
        if sel[0] == "entity":
            obj.x += dx
            obj.y += dy
        else:
            px, py = (self._rel_pos(obj) if sel[0] == "rel" else self._spec_pos(obj))
            obj.pos_x, obj.pos_y = px + dx, py + dy
        self._mark_active_dirty()
        self._schedule_render()

    # ---------------- EER: especialização / generalização / categoria ----------------
    def new_specialization(self, kind="specialization", parent=None, child=None):
        if len(self.project.entities) < 2:
            messagebox.showinfo("Especialização", "Crie ao menos duas entidades (superclasse e subclasse) antes.")
            return
        sel_e = None
        if self.selected_item and self.selected_item[0] in ("entity", "attr"):
            sel_e = self.project.find_entity(self.selected_item[1])
        sp = Specialization(kind=kind)
        parents = [parent.id] if parent else ([sel_e.id] if (sel_e and kind == "specialization") else [])
        children = [child.id] if child else ([sel_e.id] if (sel_e and kind != "specialization") else [])
        SpecializationDialog(self, self.project, sp, self._spec_confirmed, is_new=True,
                             preset_parents=parents, preset_children=children)

    def edit_specialization(self, sp):
        work = Specialization.from_dict(sp.to_dict())     # edita cópia; só aplica ao confirmar
        SpecializationDialog(self, self.project, work, self._spec_confirmed, is_new=False)

    def _spec_confirmed(self, spec, is_new, delete_id=None):
        self._push_undo()
        if delete_id:
            self.project.remove_spec(delete_id)
            self.selected_item = None
            self._update_inspector(None)
        elif is_new:
            self.project.specs.append(spec)
            self.selected_item = ("spec", spec.id)
            self._update_inspector("spec", spec)
        else:
            idx = next((i for i, s in enumerate(self.project.specs) if s.id == spec.id), None)
            if idx is not None:
                self.project.specs[idx] = spec
            self._update_inspector("spec", spec)
        self._mark_active_dirty()
        self.render()

    def _spec_geom(self, sp):
        parents = [x for x in (self.project.find_entity(i) for i in sp.parent_ids) if x]
        children = [x for x in (self.project.find_entity(i) for i in sp.child_ids) if x]
        return (parents, children) if parents and children else None

    def _spec_pos(self, sp):
        if sp.pos_x is not None and sp.pos_y is not None:
            return sp.pos_x, sp.pos_y
        g = self._spec_geom(sp)
        if not g:
            return 200, 200
        def center(e):
            x, y, w, h = self._ent_box(e)
            return x + w / 2, y + h / 2
        pcs = [center(p) for p in g[0]]
        ccs = [center(c) for c in g[1]]
        pc = (sum(c[0] for c in pcs) / len(pcs), sum(c[1] for c in pcs) / len(pcs))
        cc = (sum(c[0] for c in ccs) / len(ccs), sum(c[1] for c in ccs) / len(ccs))
        t = 0.55 if sp.is_union else 0.5
        return pc[0] + (cc[0] - pc[0]) * t, pc[1] + (cc[1] - pc[1]) * t

    def _circle_edge(self, cx, cy, tx, ty, r):
        d = math.hypot(tx - cx, ty - cy) or 1.0
        return cx + (tx - cx) / d * r, cy + (ty - cy) / d * r

    def _draw_subset_symbol(self, x, y, dx, dy):
        """Símbolo ⊂ sobre a linha, com a abertura voltada para (dx, dy)."""
        font = self.font_scaled("Segoe UI", 13, "bold")
        w = 15 * self.zoom
        self.canvas.create_oval(x - w / 2, y - w / 2, x + w / 2, y + w / 2, fill="#ffffff", outline="")
        angle = -math.degrees(math.atan2(dy, dx))
        try:
            self.canvas.create_text(x, y, text="⊂", font=font, fill="#18233D", angle=angle)
        except tk.TclError:
            self.canvas.create_text(x, y, text="⊂", font=font, fill="#18233D")

    def _draw_spec(self, sp):
        """Círculo d / o / u da notação EER (Navathe cap. 8) com linhas simples ou duplas."""
        g = self._spec_geom(sp)
        if not g:
            return
        parents, children = g
        cx, cy = self._spec_pos(sp)
        r_mod = SPEC_R
        scx, scy = self.to_screen(cx, cy)
        r = r_mod * self.zoom
        selected = self.selected_item == ("spec", sp.id)
        col = "#4263EB" if selected else "#25304A"
        total = sp.completeness == "total"

        def link(ent, double, subset=False, label=None):
            bx, by, bw, bh = self._ent_box(ent)
            b_x, b_y = self._rect_border_point(bx, by, bw, bh, cx, cy)
            c_x, c_y = self._circle_edge(cx, cy, b_x, b_y, r_mod)
            sb, sc = self.to_screen(b_x, b_y), self.to_screen(c_x, c_y)
            self._draw_chen_connection(sb[0], sb[1], sc[0], sc[1], double)
            if subset:      # ⊂ com a abertura voltada para o círculo (superconjunto)
                px, py = sb[0] + (sc[0] - sb[0]) * 0.42, sb[1] + (sc[1] - sb[1]) * 0.42
                self._draw_subset_symbol(px, py, sc[0] - sb[0], sc[1] - sb[1])
            if label:
                self._draw_label_with_halo((sb[0] + sc[0]) / 2, (sb[1] + sc[1]) / 2 - 12 * self.zoom, label,
                                           self.font_scaled("Segoe UI", 8, "bold"))

        for p in parents:
            if sp.is_union:
                link(p, False, True)
            else:
                link(p, total, False, sp.defining_attr or None)
        for c in children:
            if sp.is_union:
                link(c, total)
            else:
                link(c, False, True)

        self.canvas.create_oval(scx - r, scy - r, scx + r, scy + r, fill="#ffffff", outline=col,
                                width=max(1.5, 2.0 * self.zoom) if selected else max(1.2, 1.6 * self.zoom))
        letter = "u" if sp.is_union else sp.disjointness
        self.canvas.create_text(scx, scy, text=letter, font=self.font_scaled("Segoe UI", 9, "bold"), fill="#18233D")

    # ---------------- Notação de Barker ----------------
    def _bk_rows(self, e):
        rows = []

        def add(a, sub=False, parent=None):
            dom = self.project.find_domain(a.domain_id) if a.domain_id else None
            if a.attr_type in ("pk", "partial_key"):
                pre = "#"
            elif a.nn:
                pre = "*"
            else:
                pre = "o"
            right = dom.name if dom else a.type
            if a.is_multivalued:
                right = "[multi] " + right
            elif a.is_derived:
                right = "[deriv] " + right
            elif a.is_composite:
                right = "composto"
            rows.append({"pre": pre, "name": a.name, "right": right, "attr": a, "sub": sub, "parent": parent,
                         "dom": bool(dom), "bold": a.attr_type in ("pk", "partial_key")})
            for s in (a.sub_attrs if a.is_composite else []):
                add(s, True, a)
        for a in e.attrs:
            add(a)
        return rows

    def _bk_layout(self):
        """Layout Barker (coordenadas de modelo): caixas, linhas de atributos e subtipos aninhados."""
        if self._bk_cache is not None:
            return self._bk_cache
        p = self.project
        ents = {e.id: e for e in p.entities}
        nest, parent_of = {}, {}
        for sp in p.specs:
            if sp.is_union or not sp.parent_ids or sp.parent_ids[0] not in ents:
                continue
            par = sp.parent_ids[0]
            for cid in sp.child_ids:
                if cid in ents and cid != par and cid not in parent_of:
                    parent_of[cid] = par
                    nest.setdefault(par, []).append((cid, sp))
        for cid in list(parent_of):                       # quebra ciclos
            seen, cur = {cid}, parent_of.get(cid)
            while cur is not None and cur not in seen:
                seen.add(cur)
                cur = parent_of.get(cur)
            if cur == cid:
                par = parent_of.pop(cid)
                nest[par] = [x for x in nest.get(par, []) if x[0] != cid]
        bold = ("Segoe UI", 10, "bold")
        norm = ("Segoe UI", 8)
        sizes = {}

        def measure(eid):
            if eid in sizes:
                return sizes[eid]
            e = ents[eid]
            rows = self._bk_rows(e)
            w = self._text_metrics(e.name, bold)[0] + 30
            for r in rows:
                rw = 16 + (14 if r["sub"] else 0) + self._text_metrics(r["name"], norm)[0] + 30 + \
                    self._text_metrics(r["right"], norm)[0] + 14
                w = max(w, rw)
            w = max(BK_MIN_W, w)
            own_h = BK_HEAD + len(rows) * BK_ROW + 6
            kids = [(cid, sp) for cid, sp in nest.get(eid, []) if cid in ents]
            info = {"rows": rows, "own_h": own_h, "kids": [], "tags": []}
            if kids:
                ksz = [measure(cid) for cid, _ in kids]
                gap = 10
                total_w = sum(k["w"] for k in ksz) + gap * (len(ksz) - 1) + 2 * BK_PAD
                w = max(w, total_w)
                band = 16
                x = (w - (sum(k["w"] for k in ksz) + gap * (len(ksz) - 1))) / 2
                y = own_h + band
                for (cid, sp), k in zip(kids, ksz):
                    info["kids"].append((cid, x, y))
                    x += k["w"] + gap
                info["h"] = y + max(k["h"] for k in ksz) + BK_PAD
                seen_specs = []
                for _, sp in kids:
                    if sp not in seen_specs:
                        seen_specs.append(sp)
                info["specs"] = seen_specs
            else:
                info["h"] = own_h
                info["specs"] = []
            info["w"] = w
            sizes[eid] = info
            return info

        for eid in ents:
            measure(eid)
        layout = {}

        def place(eid, x, y, depth):
            info = sizes[eid]
            L = {"x": x, "y": y, "w": info["w"], "h": info["h"], "rows": info["rows"], "own_h": info["own_h"],
                 "depth": depth, "top": depth == 0, "tags": []}
            layout[eid] = L
            if info["specs"]:
                widths = [self._text_metrics(self._spec_tag_text(sp), ("Segoe UI", 8, "italic"))[0] + 8
                          for sp in info["specs"]]
                total = sum(widths) + 6 * (len(widths) - 1)
                tx = x + (info["w"] - total) / 2
                for sp, tw in zip(info["specs"], widths):
                    L["tags"].append((sp, (tx, y + info["own_h"] + 1, tw, 14)))
                    tx += tw + 6
            for cid, dx, dy in info["kids"]:
                place(cid, x + dx, y + dy, depth + 1)

        for eid, e in ents.items():
            if eid not in parent_of:
                place(eid, e.x, e.y, 0)
        # entidades presas em ciclo já foram liberadas; garante que todas tenham layout
        for eid, e in ents.items():
            if eid not in layout:
                place(eid, e.x, e.y, 0)
        self._bk_cache = layout
        return layout

    @staticmethod
    def _spec_tag_text(sp):
        t = ("disjunta" if sp.disjointness == "d" else "sobreposta") + " · " + \
            ("total" if sp.completeness == "total" else "parcial")
        if sp.defining_attr:
            t += f" · por {sp.defining_attr}"
        return t

    def bk_row_at(self, e, mx, my):
        L = self._bk_layout().get(e.id)
        if not L:
            return None
        top = L["y"] + BK_HEAD
        if L["x"] <= mx <= L["x"] + L["w"] and top <= my < top + len(L["rows"]) * BK_ROW:
            return L["rows"][int((my - top) // BK_ROW)]
        return None

    def _unit(self, x1, y1, x2, y2):
        d = math.hypot(x2 - x1, y2 - y1) or 1.0
        return (x2 - x1) / d, (y2 - y1) / d

    def _bk_nary_box(self, r):
        cx, cy = self._rel_pos(r)
        tw = self._text_metrics(r.name, ("Segoe UI", 8, "bold"))[0]
        bw, bh = max(90, tw + 26), 26
        return (cx - bw / 2, cy - bh / 2, bw, bh)

    def _bk_routes(self):
        """Rotas ortogonais de todos os relacionamentos (cache até a geometria mudar)."""
        L = self._bk_layout()
        rects, edges, ancestors = {}, [], {}
        parent_of = {}
        for sp in self.project.specs:
            if not sp.is_union and sp.parent_ids:
                for cid in sp.child_ids:
                    if cid in L and sp.parent_ids[0] in L and L[cid]["depth"] > 0:
                        parent_of.setdefault(cid, sp.parent_ids[0])

        def chain(eid):
            out, cur, guard = set(), parent_of.get(eid), 0
            while cur and guard < 20:
                out.add(cur)
                cur = parent_of.get(cur)
                guard += 1
            return out
        for eid, v in L.items():
            rects[eid] = (v["x"], v["y"], v["w"], v["h"])
        for r in self.project.rels:
            if r.is_nary:
                if any(self.project.find_entity(i) is None for i, *_ in r.participants()):
                    continue
                rects["box:" + r.id] = self._bk_nary_box(r)
        for r in self.project.rels:
            if r.is_nary:
                if "box:" + r.id not in rects:
                    continue
                for i, (eid, *_rest) in enumerate(r.participants()):
                    if eid in rects:
                        edges.append({"key": (r.id, i), "a": eid, "b": "box:" + r.id, "ignore": chain(eid),
                                      "sides_a": ("bottom",) if eid in parent_of else tuple(router.NORMALS)})
            else:
                e1, e2 = r.entity1_id, r.entity2_id
                if e1 == e2 or e1 not in rects or e2 not in rects:
                    continue
                edges.append({"key": (r.id, 0), "a": e1, "b": e2, "ignore": chain(e1) | chain(e2),
                              "sides_a": ("bottom",) if e1 in parent_of else tuple(router.NORMALS),
                              "sides_b": ("bottom",) if e2 in parent_of else tuple(router.NORMALS)})
        sig = (tuple(sorted((k, round(v[0], 1), round(v[1], 1), round(v[2], 1), round(v[3], 1)) for k, v in rects.items())),
               tuple((e["key"], e["a"], e["b"]) for e in edges), self._fast_routing)
        if self._bk_route_cache and self._bk_route_cache[0] == sig:
            return self._bk_route_cache[1]
        routes = router.route_edges(rects, edges, fast=self._fast_routing) if edges else {}
        self._bk_port_use = {}
        for e in edges:
            rt = routes.get(e["key"])
            if rt:
                for eid, n in ((e["a"], rt["na"]), (e["b"], rt["nb"])):
                    self._bk_port_use[(eid, n)] = self._bk_port_use.get((eid, n), 0) + 1
        self._bk_route_cache = (sig, routes)
        return routes

    def _bk_rel_geom(self, r):
        """Geometria do relacionamento em Barker (modelo): metades, pés-de-galinha, barras de UID, rótulo."""
        parts = [(self.project.find_entity(eid), card, part, role) for eid, card, part, role in r.participants()]
        if any(p[0] is None for p in parts):
            return None
        many = lambda c: str(c).upper() in ("N", "M")
        if r.is_nary:
            box = self._bk_nary_box(r)
            routes = self._bk_routes()
            halves = []
            for i, (ent, card, part, role) in enumerate(parts):
                rt = routes.get((r.id, i))
                if not rt:
                    continue
                pts = rt["pts"]
                # o pé-de-galinha fica no lado da caixa de interseção; a barra no lado da entidade (card 1)
                halves.append({"pts": pts, "dashed": False,
                               "feet": [(pts[-1], (-rt["nb"][0], -rt["nb"][1]))],
                               "bars": [(pts[0], rt["na"])] if str(card) == "1" else [],
                               "role": role, "role_at": pts[0], "u": rt["na"]})
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            return {"halves": halves, "label": (cx, cy), "box": (box[0], box[1], box[0] + box[2], box[1] + box[3]),
                    "nary": True}
        (e1, c1, p1, r1), (e2, c2, p2, r2) = parts[0], parts[1]
        uid_end = None
        if r.is_identifying:
            uid_end = 0 if e1.is_weak and not e2.is_weak else 1
        if e1.id == e2.id:
            x, y, w, h = self._ent_box(e1)
            self._bk_routes()
            use = getattr(self, "_bk_port_use", {})
            order = [("right", (1, 0)), ("left", (-1, 0)), ("bottom", (0, 1)), ("top", (0, -1))]
            same = [q for q in self.project.rels if not q.is_nary and q.entity1_id == q.entity2_id == e1.id]
            k = same.index(r) if r in same else 0
            side, n = min(order, key=lambda o: (use.get((e1.id, o[1]), 0), order.index(o)))
            if n[1] == 0:      # lado vertical (direita/esquerda)
                px = x + w if n[0] > 0 else x
                a1, a2 = (px, y + h * 0.28), (px, y + h * 0.72)
            else:
                py = y + h if n[1] > 0 else y
                a1, a2 = (x + w * 0.28, py), (x + w * 0.72, py)
            reach = 46 + 26 * k
            b1 = (a1[0] + n[0] * reach, a1[1] + n[1] * reach)
            b2 = (a2[0] + n[0] * reach, a2[1] + n[1] * reach)
            mid = ((b1[0] + b2[0]) / 2, (b1[1] + b2[1]) / 2)
            halves = []
            for idx, (pt, pt2, card, part, role) in enumerate(((a1, b1, c1, p1, r1), (a2, b2, c2, p2, r2))):
                d = math.hypot(pt2[0] - mid[0], pt2[1] - mid[1]) or 1.0
                away = ((pt2[0] - mid[0]) / d, (pt2[1] - mid[1]) / d)       # para fora do par de conectores
                halves.append({"pts": [pt, pt2, mid], "dashed": part != "total",
                               "feet": [(pt, n)] if many(card) else [],
                               "bars": [(pt, n)] if uid_end == idx else [],
                               "role": role,
                               "role_at": (pt[0] + n[0] * 24 + away[0] * 10, pt[1] + n[1] * 24 + away[1] * 10),
                               "u": (0, 0)})
            anchor = "w" if n[0] > 0 else ("e" if n[0] < 0 else "center")
            lab = (mid[0] + n[0] * 8, mid[1] + n[1] * 12)
            return {"halves": halves, "label": lab, "box": None, "self": True, "anchor": anchor}
        rt = self._bk_routes().get((r.id, 0))
        if not rt:
            return None
        h1, h2, M = router.split_at_midpoint(rt["pts"])
        n1, n2 = rt["na"], rt["nb"]
        halves = [
            {"pts": h1, "dashed": p1 != "total", "feet": [(h1[0], n1)] if many(c1) else [],
             "bars": [(h1[0], n1)] if uid_end == 0 else [], "role": r1, "role_at": h1[0], "u": n1},
            {"pts": h2, "dashed": p2 != "total", "feet": [(h2[0], n2)] if many(c2) else [],
             "bars": [(h2[0], n2)] if uid_end == 1 else [], "role": r2, "role_at": h2[0], "u": n2},
        ]
        return {"halves": halves, "label": M, "box": None}

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _render_barker(self):
        L = self._bk_layout()
        for r in self.project.rels:
            self._draw_bk_relationship(r)
        for sp in self.project.specs:
            if sp.is_union:
                self._draw_spec(sp)
        for e in sorted(self.project.entities, key=lambda x: L[x.id]["depth"] if x.id in L else 0):
            if e.id in L:
                self._draw_bk_entity(e, L[e.id])
        self._draw_bk_arcs(L)

    def _draw_bk_arcs(self, L):
        """Renderiza os Arcos de Exclusividade Barker / Oracle Designer (XOR).

        O arco é desenhado como uma curva contínua (sólida para obrigatório ou tracejada para opcional)
        atravessando os conectores dos relacionamentos participantes próximos à entidade âncora.
        """
        if not hasattr(self.project, "arcs") or not self.project.arcs:
            return
        z = self.zoom
        routes = self._bk_routes()

        for arc in self.project.arcs:
            ent = self.project.find_entity(arc.entity_id)
            if not ent or ent.id not in L:
                continue
            box = L[ent.id]
            cx, cy = box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0

            arc_pts = []
            for rid in arc.rel_ids:
                rel = self.project.find_rel(rid)
                if not rel or rel.is_nary:
                    continue
                rt = routes.get((rel.id, 0))
                if not rt or not rt.get("pts"):
                    continue
                pts = rt["pts"]
                if rel.entity1_id == ent.id:
                    p0 = pts[0]
                    norm = rt.get("na", (0, 1))
                elif rel.entity2_id == ent.id:
                    p0 = pts[-1]
                    nb = rt.get("nb", (0, 1))
                    norm = (-nb[0], -nb[1])
                else:
                    continue

                cross_x = p0[0] + norm[0] * 24.0
                cross_y = p0[1] + norm[1] * 24.0
                ang = math.atan2(cross_y - cy, cross_x - cx)
                arc_pts.append((ang, cross_x, cross_y))

            if len(arc_pts) < 2:
                continue

            arc_pts.sort(key=lambda t: t[0])

            if len(arc_pts) == 2:
                p1 = (arc_pts[0][1], arc_pts[0][2])
                p2 = (arc_pts[1][1], arc_pts[1][2])
                mid_x = (p1[0] + p2[0]) / 2.0
                mid_y = (p1[1] + p2[1]) / 2.0
                vx, vy = mid_x - cx, mid_y - cy
                vlen = math.hypot(vx, vy) or 1.0
                arc_h = 10.0
                bend_x = mid_x + (vx / vlen) * arc_h
                bend_y = mid_y + (vy / vlen) * arc_h
                curve_pts = [p1, (bend_x, bend_y), p2]
            else:
                curve_pts = [(p[1], p[2]) for p in arc_pts]

            screen_pts = [self.to_screen(px, py) for px, py in curve_pts]
            flat_coords = [c for pt in screen_pts for c in pt]

            color = "#18233D"
            lw = max(1.6, 2.2 * z)
            dash = () if arc.mandatory else (max(4, int(5 * z)), max(3, int(3 * z)))
            tag_name = f"arc_{arc.id}"

            self.canvas.create_line(
                *flat_coords,
                smooth=True,
                splinesteps=12,
                fill=color,
                width=lw,
                dash=dash,
                tags=("arc", tag_name)
            )

            mid_pt = screen_pts[len(screen_pts) // 2]
            lbl = arc.name or "⌒"
            self._draw_label_with_halo(
                mid_pt[0], mid_pt[1] - 8 * z,
                lbl,
                self.font_scaled("Segoe UI", 7.5, "bold")
            )

            self.canvas.tag_bind(tag_name, "<Double-Button-1>", lambda _e, a=arc: self.open_arc_dialog(a))

    def _draw_bk_entity(self, e, L):
        z = self.zoom
        sx1, sy1 = self.to_screen(L["x"], L["y"])
        sx2, sy2 = self.to_screen(L["x"] + L["w"], L["y"] + L["h"])
        sel = self.selected_item
        selected = sel == ("entity", e.id) or (sel and sel[0] == "attr" and sel[1] == e.id)
        marked = (self.link_mode and self.link_src == e.id) or (self.nary_mode and e.id in self.nary_sel)
        outline = "#dc2626" if marked else ("#5948E8" if selected else "#25304A")
        fill = "#FFF4E3" if e.is_weak else ("#FFFFFF" if L["depth"] else "#EAF2FF")
        lw = max(1.6, 2.2 * z) if (selected or marked) else max(1.2, 1.5 * z)
        self._round_rect(sx1, sy1, sx2, sy2, 9 * z, fill=fill, outline=outline, width=lw)
        name_font = self._fit_canvas_font(e.name, 9.5, (L["w"] - 18) * z, "bold")
        self.canvas.create_text((sx1 + sx2) / 2, sy1 + BK_HEAD * z / 2, text=e.name, font=name_font, fill="#18233D")
        sep_y = sy1 + BK_HEAD * z
        row_end = sep_y + len(L["rows"]) * BK_ROW * z
        self.canvas.create_line(sx1, sep_y, sx2, sep_y, fill=outline, width=max(1.0, 1.2 * z))
        pre_col = {"#": "#1d4ed8", "*": "#18233D", "o": "#73809B"}
        f_pre = self.font_scaled("Consolas", 8.5, "bold")
        for i, row in enumerate(L["rows"]):
            ty = sep_y + (i + 0.5) * BK_ROW * z
            a = row["attr"]
            if sel == ("attr", e.id, a.id):
                self.canvas.create_rectangle(sx1 + 3 * z, ty - BK_ROW * z / 2, sx2 - 3 * z, ty + BK_ROW * z / 2,
                                             fill="#DDD6FE", outline="")
            indent = (14 if row["sub"] else 0) * z
            self.canvas.create_text(sx1 + (9 * z) + indent, ty, text=row["pre"], anchor="w", font=f_pre, fill=pre_col[row["pre"]])
            f_name = self._fit_canvas_font(row["name"], 8, (L["w"] * 0.58) * z, "bold" if row["bold"] else "normal")
            self.canvas.create_text(sx1 + (24 * z) + indent, ty, text=row["name"], anchor="w", font=f_name, fill="#18233D")
            style = ("italic",) if row["dom"] else ()
            self.canvas.create_text(sx2 - 8 * z, ty, text=row["right"], anchor="e",
                                    font=self.font_scaled("Segoe UI", 7.5, *style),
                                    fill="#7c3aed" if row["dom"] else "#98A4BB")
        for sp, (tx, ty, tw, th) in L["tags"]:
            sx, sy = self.to_screen(tx, ty)
            self.canvas.create_text(sx + tw * z / 2, sy + th * z / 2, text=self._spec_tag_text(sp),
                                    font=self.font_scaled("Segoe UI", 7.5, "italic"),
                                    fill="#4263EB" if self.selected_item == ("spec", sp.id) else "#0f766e")

    def _rounded_pts(self, pts, radius):
        """Pontos de controle para suavizar cantos ortogonais (raio em unidades de tela)."""
        if len(pts) < 3 or radius <= 0:
            return [c for p in pts for c in p], False
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            la, lc = math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(c[0] - b[0], c[1] - b[1])
            r = min(radius, la / 2.0, lc / 2.0)
            if r < 1:
                out.append(b)
                continue
            out.append((b[0] + (a[0] - b[0]) / la * r, b[1] + (a[1] - b[1]) / la * r))
            out.append(b)
            out.append((b[0] + (c[0] - b[0]) / lc * r, b[1] + (c[1] - b[1]) / lc * r))
        out.append(pts[-1])
        return [c for p in out for c in p], True

    def _draw_bk_relationship(self, r):
        g = self._bk_rel_geom(r)
        if not g:
            return
        z = self.zoom
        selected = self.selected_item == ("rel", r.id)
        col = "#4263EB" if selected else "#42506B"
        lw = max(1.4, 2.0 * z) if selected else max(1.2, 1.5 * z)
        for half in g["halves"]:
            spts = [self.to_screen(*p) for p in half["pts"]]
            coords, smooth = self._rounded_pts(spts, 7 * z)
            kw = {"dash": (max(4, int(7 * z)), max(3, int(4 * z)))} if half["dashed"] else {}
            if smooth:
                kw.update(smooth=True, splinesteps=8)
            self.canvas.create_line(*coords, fill=col, width=lw, **kw)
            for (px, py), (ux, uy) in half["feet"]:
                sx, sy = self.to_screen(px, py)
                L_, W_ = 13 * z, 6.5 * z
                tx, ty = sx + ux * L_, sy + uy * L_
                nx, ny = -uy, ux
                for k in (-1, 0, 1):
                    self.canvas.create_line(tx, ty, sx + nx * W_ * k, sy + ny * W_ * k, fill=col, width=lw)
            for (px, py), (ux, uy) in half["bars"]:
                sx, sy = self.to_screen(px, py)
                bx, by = sx + ux * 17 * z, sy + uy * 17 * z
                nx, ny = -uy, ux
                self.canvas.create_line(bx - nx * 7 * z, by - ny * 7 * z, bx + nx * 7 * z, by + ny * 7 * z, fill=col, width=max(2.0, 2.6 * z))
            if half.get("role"):
                px, py = half["role_at"]
                ux, uy = half["u"]
                sx, sy = self.to_screen(px, py)
                self._draw_label_with_halo(sx + ux * 34 * z - uy * 11 * z, sy + uy * 34 * z + ux * 11 * z, half["role"],
                                           self.font_scaled("Segoe UI", 7, "italic"))
        lx, ly = self.to_screen(*g["label"])
        if g["box"]:
            x1, y1 = self.to_screen(g["box"][0], g["box"][1])
            x2, y2 = self.to_screen(g["box"][2], g["box"][3])
            self._round_rect(x1, y1, x2, y2, 7 * z, fill="#F1ECFF", outline=col, width=lw)
            self.canvas.create_text(lx, ly, text=r.name, font=self.font_scaled("Segoe UI", 8, "bold"), fill="#18233D")
        elif g.get("self"):
            self.canvas.create_text(lx, ly, text=r.name, anchor=g.get("anchor", "w"),
                                    font=self.font_scaled("Segoe UI", 8, "bold"), fill="#18233D")
        else:
            self._draw_label_with_halo(lx, ly, r.name, self.font_scaled("Segoe UI", 8, "bold"))

    # ---------------- Legenda e avisos sobre o canvas ----------------
    def _draw_overlays(self):
        c = self.canvas
        w, h = c.winfo_width() or 1200, c.winfo_height() or 800
        banner = None
        if self.nary_mode:
            banner = f"N-ÁRIO — clique nas entidades ({len(self.nary_sel)} escolhidas) · Enter conclui · Esc cancela"
        elif self.link_mode:
            banner = "RELACIONAR — " + ("clique na entidade de destino" if self.link_src else "clique na 1ª entidade") + " · Esc cancela"
        if banner:
            c.create_rectangle(0, 0, w, 24, fill="#dc2626", outline="")
            c.create_text(w / 2, 12, text=banner, fill="#ffffff", font=("Segoe UI", 9, "bold"))
        if not self.project.entities and not banner:
            c.create_text(w / 2, h / 2 - 12, text="Modelo vazio", fill="#98A4BB", font=("Segoe UI", 16, "bold"))
            c.create_text(w / 2, h / 2 + 16, text="Duplo-clique no fundo para criar uma entidade — ou use a aba 'Inserir Elementos'",
                          fill="#98A4BB", font=("Segoe UI", 10))
        if self.show_legend and self.project.entities:
            self._draw_legend(w, h)

    def _draw_legend(self, w, h):
        c = self.canvas
        col = "#25304A"
        if self.notation == "chen":
            items = [("rect", "Entidade forte"), ("drect", "Entidade fraca"), ("dia", "Relacionamento"),
                     ("ddia", "Relac. identificador"), ("ell", "Atributo"), ("kell", "Chave (sublinhado)"),
                     ("dell", "Multivalorado"), ("hell", "Derivado"), ("circ", "Espec. d/o · União u")]
        else:
            items = [("bk", "Entidade / subtipo"), ("txt#", "# identificador único"),
                     ("txt*", "* obrigatório"), ("txto", "o opcional"), ("sol", "Linha cheia: obrigatório"),
                     ("das", "Tracejada: opcional"), ("foot", "Pé-de-galinha: muitos"), ("bar", "Barra: parte do UID")]
        cols = 2
        rows = (len(items) + cols - 1) // cols
        cw, rh = 178, 17
        x0, y0 = 10, h - rows * rh - 14
        c.create_rectangle(x0 - 4, y0 - 6, x0 + cols * cw, h - 4, fill="#FFFFFF", outline="#DCE3F1")
        for i, (shape, label) in enumerate(items):
            cx = x0 + (i // rows) * cw
            cy = y0 + (i % rows) * rh + rh / 2
            sx = cx + 16
            if shape == "rect":
                c.create_rectangle(sx - 12, cy - 5, sx + 12, cy + 5, outline=col)
            elif shape == "drect":
                c.create_rectangle(sx - 12, cy - 6, sx + 12, cy + 6, outline=col)
                c.create_rectangle(sx - 9, cy - 3, sx + 9, cy + 3, outline=col)
            elif shape in ("dia", "ddia"):
                c.create_polygon(sx - 12, cy, sx, cy - 6, sx + 12, cy, sx, cy + 6, fill="", outline=col)
                if shape == "ddia":
                    c.create_polygon(sx - 8, cy, sx, cy - 3, sx + 8, cy, sx, cy + 3, fill="", outline=col)
            elif shape in ("ell", "kell", "dell", "hell"):
                c.create_oval(sx - 12, cy - 6, sx + 12, cy + 6, outline=col, dash=(3, 2) if shape == "hell" else ())
                if shape == "dell":
                    c.create_oval(sx - 9, cy - 3, sx + 9, cy + 3, outline=col)
                if shape == "kell":
                    c.create_line(sx - 6, cy + 3, sx + 6, cy + 3, fill=col)
            elif shape == "circ":
                c.create_oval(sx - 6, cy - 6, sx + 6, cy + 6, outline=col)
            elif shape == "bk":
                self._round_rect(sx - 12, cy - 6, sx + 12, cy + 6, 4, fill="#EAF2FF", outline=col)
            elif shape.startswith("txt"):
                c.create_text(sx, cy, text=shape[3:], font=("Consolas", 10, "bold"), fill=col)
            elif shape in ("sol", "das"):
                c.create_line(sx - 12, cy, sx + 12, cy, fill=col, width=1.6, dash=(4, 3) if shape == "das" else ())
            elif shape == "foot":
                c.create_line(sx - 12, cy, sx + 12, cy, fill=col)
                for k in (-5, 0, 5):
                    c.create_line(sx, cy, sx + 12, cy + k, fill=col)
            elif shape == "bar":
                c.create_line(sx - 12, cy, sx + 12, cy, fill=col)
                c.create_line(sx, cy - 6, sx, cy + 6, fill=col, width=2)
            c.create_text(cx + 34, cy, text=label, anchor="w", font=("Segoe UI", 7), fill="#475569")

    def show_help(self):
        win = tk.Toplevel(self)
        win.title("Atalhos e dicas")
        win.transient(self)
        win.geometry("560x560")
        txt = tk.Text(win, wrap="word", font=("Segoe UI", 10), padx=12, pady=10, bg="#F8F9FE", relief="flat")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", """MOUSE
  Duplo-clique no fundo ........ nova entidade (já abre a edição)
  Duplo-clique em um elemento .. editar (atributo: abre a entidade no campo dele)
  Botão direito ................ menu de contexto (ou arraste para mover a visão)
  Arrastar o fundo ............. mover a visão (pan)
  Ctrl + Roda .................. zoom no cursor · Shift + Roda: horizontal
  Arrastar elementos ........... mover (com 'Encaixar na grade' ligado, alinha de 20 em 20)

TECLADO
  Ctrl+S salvar · Ctrl+Shift+S salvar como · Ctrl+O abrir · Ctrl+N novo projeto
  Ctrl+Z / Ctrl+Y desfazer / refazer · Ctrl+D duplicar seleção
  Del excluir · F2 ou Enter editar seleção · Setas movem a seleção (Shift = passo maior)
  Ctrl+L relacionar · Ctrl+0 ajustar tudo · Ctrl+1 zoom 100% · Ctrl +/- zoom
  Ctrl+G grade · F5 validar modelo · F9 gerar DDL · Esc cancela o modo atual

CRIAR RELACIONAMENTOS
  Binário: 'Relacionar', clique nas duas entidades (a mesma duas vezes = auto-relacionamento).
  N-ário (3+ entidades): 'N-ário', clique nas entidades e Enter.
  Identificador (losango duplo): use 'Identificador' e comece pela entidade proprietária.

EER (especialização / generalização / categoria)
  Selecione a superclasse (ou subclasse) e use a aba 'Inserir Elementos'.
  No Barker, os subtipos aparecem aninhados dentro do supertipo.

DOMÍNIOS
  'Domínios…' cria listas de valores (ex.: 1 = Atualizado, 2 = Desatualizado, 3 = Fechado).
  Depois escolha o domínio na coluna 'Domínio' do atributo. No DDL vira tabela de domínio + FK, ou CHECK.
""")
        txt.config(state="disabled")
        ttk.Button(win, text="Fechar", command=win.destroy).pack(pady=6)
        win.bind("<Escape>", lambda e: win.destroy())

    # ---------------- Gravar DDL / Oracle ----------------
    def _ddl_body(self, kind, dialect):
        if kind == "dimensional":
            return generate_kimball_ddl(self.project, dialect)
        return generate_ddl(self.project, dialect)

    def _ddl_file_text(self, kind, dialect):
        name = self.active_document["name"]
        head = (f"-- Projeto: {name}\n-- Modelo: {kind}\n-- Dialeto: {dialect.upper()}\n"
                f"-- Gerado em: {datetime.datetime.now():%Y-%m-%d %H:%M}\n")
        if dialect == "oracle":
            head += "SET DEFINE OFF\n"      # evita que o SQL*Plus trate '&' dos comentários como variável
        text = self._ddl_body(kind, dialect).rstrip() + "\n"
        return head + "\n" + text

    def _can_save_kind(self, kind):
        if kind == "dimensional":
            if not any(e.kimball_role in ("dimension", "fact") for e in self.project.entities):
                messagebox.showinfo("DDL dimensional", "Nenhuma entidade está marcada como Dimensão ou Fato.\n"
                                                       "Edite a entidade (duplo-clique) e defina o 'Papel Dimensional'.")
                return False
            return True
        return self._confirm_validation()

    def save_ddl(self, kind):
        """Grava o DDL transacional, dimensional ou ambos em arquivos .sql."""
        dialect = self.dialect_var.get()
        kinds = ["transacional", "dimensional"] if kind == "ambos" else [kind]
        if not all(self._can_save_kind(k) for k in kinds):
            return
        base = self.active_document["name"].replace(" ", "_")
        try:
            if len(kinds) == 1:
                path = filedialog.asksaveasfilename(defaultextension=".sql", initialfile=f"{base}_{kinds[0]}_{dialect}.sql",
                                                    filetypes=[("SQL", "*.sql")], title=f"Gravar DDL {kinds[0]}")
                if not path:
                    return
                paths = {kinds[0]: path}
            else:
                folder = filedialog.askdirectory(title="Pasta para gravar os DDLs transacional e dimensional")
                if not folder:
                    return
                paths = {k: os.path.join(folder, f"{base}_{k}_{dialect}.sql") for k in kinds}
            for k, p in paths.items():
                with open(p, "w", encoding="utf-8", newline="\n") as f:
                    f.write(self._ddl_file_text(k, dialect))
        except OSError as ex:
            messagebox.showerror("Gravar DDL", f"Não foi possível gravar:\n{ex}")
            return
        self.status_msg.config(text="DDL gravado: " + "; ".join(paths.values()))
        messagebox.showinfo("Gravar DDL", "Arquivo(s) gravado(s):\n\n" + "\n".join(paths.values()))

    def open_oracle(self):
        """Janela do Oracle. A conexão pertence ao app: fechar a janela não desconecta."""
        dlg = self._oracle_dlg
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    dlg.focus_force()
                    return dlg
            except tk.TclError:
                pass

        def statements(kind):
            if kind == "dimensional" and not any(e.kimball_role in ("dimension", "fact") for e in self.project.entities):
                raise ValueError("Nenhuma entidade marcada como Dimensão ou Fato.")
            if kind == "transacional" and not self._confirm_validation():
                raise ValueError("Geração cancelada: corrija os erros do modelo.")
            return [self._ddl_body(kind, "oracle")]
        self._oracle_dlg = OracleDialog(self, statements)
        return self._oracle_dlg

    def disconnect_oracle(self):
        if self.oracle.connected and messagebox.askyesno("Desconectar", "Encerrar a conexão com o Oracle?"):
            self.oracle.close()
            self._refresh_oracle_status()
            dlg = self._oracle_dlg
            if dlg is not None:
                try:
                    if dlg.winfo_exists():
                        dlg._refresh_header()
                except tk.TclError:
                    pass

    # ---------------- Dicas, barra de navegação e menu ----------------
    def _tip(self, widget, text):
        Tooltip(widget, text)
        return widget

    def _build_quick_toolbar(self):
        """Barra de ferramentas de alta produtividade apenas com ícones essenciais e tooltips explicativos."""
        bar = tk.Frame(self, bg="#FFFFFF", height=38, highlightbackground="#DCE3F1", highlightthickness=1)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        self.quick_bar = bar

        def sep():
            tk.Frame(bar, bg="#E2E8F0", width=1).pack(side="left", fill="y", pady=6, padx=4)

        def btn(icon, cmd, tip, width=3):
            b = ttk.Button(bar, text=icon, command=cmd, style="Tool.TButton", width=width)
            b.pack(side="left", padx=1, pady=4)
            self._tip(b, tip)
            return b

        # 1. Arquivo & Gestão de Modelos
        btn("📄", self.new_project, "Novo Projeto · Ctrl+N", 3)
        btn("📂", self.load_project, "Abrir Projeto… · Ctrl+O", 3)
        btn("💾", self.save_project, "Salvar Projeto · Ctrl+S", 3)
        btn("📥", self.open_import_dialog, "Importar Modelo (Excel / JSON / SQL / Oracle)… · Ctrl+I", 3)
        btn("📤", self.export_pdf, "Exportar Documento PDF…", 3)
        sep()

        # 2. Histórico
        self.undo_btn = btn("↶", self.undo, "Desfazer · Ctrl+Z", 3)
        self.redo_btn = btn("↷", self.redo, "Refazer · Ctrl+Y", 3)
        self._update_undo_redo_buttons()
        sep()

        # 3. Formas e Elementos Essenciais
        btn("▢", lambda: self.add_entity_custom(is_weak=False), "Inserir Entidade Forte (Duplo-clique no fundo)", 3)
        btn("⧉", lambda: self.add_entity_custom(is_weak=True), "Inserir Entidade Fraca", 3)
        btn("◇", self.toggle_link, "Criar Relacionamento Binário · Ctrl+L", 3)
        btn("◈", lambda: self.toggle_link(identifying=True), "Criar Relacionamento Identificador", 3)
        btn("⬡", self.toggle_nary, "Criar Relacionamento N-ário (3+ Entidades)", 3)
        btn("○", self.add_attribute_to_selected, "Adicionar Atributo ao Elemento Selecionado", 3)
        btn("⌒", self.open_arc_dialog, "Arco de Exclusividade (Barker / Oracle Designer XOR)", 3)
        sep()

        # 4. Alinhamento de Relacionamentos (Chen & Barker)
        btn("━", self.align_rel_horizontal, "Alinhar Relacionamento Horizontalmente (mesmo Y) · Ctrl+H", 3)
        btn("┃", self.align_rel_vertical, "Alinhar Relacionamento Verticalmente (mesmo X) · Ctrl+Shift+H", 3)
        btn("⚡", self.auto_align_selected_rel, "Auto-alinhar Relacionamento Selecionado", 3)
        btn("⚡⚡", self.auto_align_all_relationships, "Auto-alinhar Todos os Relacionamentos do Diagrama", 4)
        sep()

        # 5. Notação (Ícones Puros)
        self.btn_chen_mode = tk.Button(bar, text="📐", relief="flat", bd=1, width=3, pady=2,
                                       command=lambda: self.set_notation("chen"), cursor="hand2")
        self.btn_chen_mode.pack(side="left", padx=1, pady=5)
        self._tip(self.btn_chen_mode, "Notação de Chen / EER (Navathe)")

        self.btn_table_mode = tk.Button(bar, text="📊", relief="flat", bd=1, width=3, pady=2,
                                        command=lambda: self.set_notation("barker"), cursor="hand2")
        self.btn_table_mode.pack(side="left", padx=1, pady=5)
        self._tip(self.btn_table_mode, "Notação de Barker (Engenharia de Informação)")
        self._sync_notation_buttons()
        sep()

        # 6. Zoom & Visualização (Ícones Puros)
        btn("🔍−", self.zoom_out, "Diminuir Zoom · Ctrl+−", 4)
        self.zoom_var = tk.StringVar(value="100%")
        zc = ttk.Combobox(bar, textvariable=self.zoom_var, width=5, values=["50%", "75%", "100%", "125%", "150%", "200%"])
        zc.pack(side="left", padx=1, pady=6)
        zc.bind("<<ComboboxSelected>>", self._zoom_from_combo)
        zc.bind("<Return>", self._zoom_from_combo)
        self._tip(zc, "Nível de Zoom")
        btn("🔍+", self.zoom_in, "Aumentar Zoom · Ctrl++", 4)
        btn("1:1", self.zoom_reset, "Zoom Original 100% · Ctrl+1", 4)
        btn("⛶", self.zoom_fit, "Ajustar Todo o Diagrama à Tela · Ctrl+0", 3)
        btn("🎯", self.center_selection, "Centralizar Elemento Selecionado", 3)
        sep()

        # 7. Modos e Integrações (Ícones Puros)
        for icon, var, cmd, tip in (
            ("▦", self.grid_var, self.toggle_grid, "Exibir Grade · Ctrl+G"),
            ("🧲", self.snap_var, self.toggle_snap, "Encaixar na Grade (Snap)"),
            ("🏷", self.legend_var, self.toggle_legend, "Exibir Legenda da Notação"),
        ):
            cb = ttk.Checkbutton(bar, text=icon, variable=var, command=cmd, style="Tool.Toolbutton", width=3)
            cb.pack(side="left", padx=1, pady=4)
            self._tip(cb, tip)

        btn("⚙", self.show_ddl, "Gerar Script DDL SQL · F9", 3)
        btn("🔌", self.open_oracle, "Conexão Oracle & Dicionário de Dados", 3)
        btn("✔", self.validate_model, "Validar Modelo · F5", 3)

        # 8. Busca à Direita
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=16)
        self.search_entry.pack(side="right", padx=(2, 8), pady=6)
        self.search_entry.bind("<Return>", lambda _e: self.find_element())
        self.search_entry.bind("<Escape>", lambda _e: (self.search_var.set(""), self.canvas.focus_set()))
        tk.Label(bar, text="🔎", bg="#FFFFFF", fg="#73809B", font=("Segoe UI", 9)).pack(side="right")
        self._tip(self.search_entry, "Busca (entidade, atributo, relacionamento) · Enter = próximo · Ctrl+F")

    def open_import_dialog(self):
        """Abre o diálogo universal para importação de modelos conceituais (Excel, JSON, SQL, etc)."""
        from dialogs import ImportModelDialog
        ImportModelDialog(self, self)

    def open_arc_dialog(self, arc=None):
        """Abre o diálogo para criação ou edição de Arcos de Exclusividade (Barker / Oracle Designer)."""
        from dialogs import ArcDialog
        if not self.project.entities:
            messagebox.showinfo("Arco Barker", "O modelo precisa ter entidades e relacionamentos primeiro.", parent=self)
            return
        dlg = ArcDialog(self, self.project, arc=arc)
        self.wait_window(dlg)
        if dlg.result:
            self._push_undo()
            self._mark_active_dirty()
            self.render()
            self.status_msg.config(text=f"Arco '{dlg.result.name}' configurado com sucesso.")

    def align_rel_horizontal(self, rel=None):
        """Alinha as duas entidades conectadas pelo relacionamento no mesmo nível vertical Y.

        - Centraliza o diamante (se Chen) exatamente na metade do caminho e no mesmo nível Y.
        - Em Barker, como ambas as entidades passam a ter a mesma coordenada Y, o conector
          torna-se uma linha 100% reta horizontal sem nenhum cotovelo/degrau.
        """
        if rel is None:
            if self.selected_item and self.selected_item[0] == "rel":
                rel = self.project.find_rel(self.selected_item[1])
            elif self.selected_item and self.selected_item[0] == "entity":
                eid = self.selected_item[1]
                rels = [r for r in self.project.rels if eid in (r.entity1_id, r.entity2_id)]
                if rels:
                    rel = rels[0]
        if not rel or rel.is_nary or rel.entity1_id == rel.entity2_id:
            return
        e1 = self.project.find_entity(rel.entity1_id)
        e2 = self.project.find_entity(rel.entity2_id)
        if not e1 or not e2:
            return

        self._push_undo()
        self._mark_active_dirty()
        h1 = self._ent_box(e1)[3]
        h2 = self._ent_box(e2)[3]
        center_y = (e1.y + h1 / 2 + e2.y + h2 / 2) / 2
        e1.y = int(center_y - h1 / 2)
        e2.y = int(center_y - h2 / 2)

        if self.notation == "chen":
            w1 = self._ent_box(e1)[2]
            w2 = self._ent_box(e2)[2]
            center_x1 = e1.x + w1 / 2
            center_x2 = e2.x + w2 / 2
            rel.pos_x = int((center_x1 + center_x2) / 2)
            rel.pos_y = int(center_y)

        self.render()
        self.status_msg.config(text=f"Relacionamento '{rel.name}' alinhado horizontalmente.")

    def align_rel_vertical(self, rel=None):
        """Alinha as duas entidades conectadas pelo relacionamento na mesma coluna horizontal X.

        - Centraliza o diamante (se Chen) exatamente no ponto médio Y e no mesmo nível X.
        - Em Barker, o conector torna-se uma linha 100% reta vertical.
        """
        if rel is None:
            if self.selected_item and self.selected_item[0] == "rel":
                rel = self.project.find_rel(self.selected_item[1])
            elif self.selected_item and self.selected_item[0] == "entity":
                eid = self.selected_item[1]
                rels = [r for r in self.project.rels if eid in (r.entity1_id, r.entity2_id)]
                if rels:
                    rel = rels[0]
        if not rel or rel.is_nary or rel.entity1_id == rel.entity2_id:
            return
        e1 = self.project.find_entity(rel.entity1_id)
        e2 = self.project.find_entity(rel.entity2_id)
        if not e1 or not e2:
            return

        self._push_undo()
        self._mark_active_dirty()
        w1 = self._ent_box(e1)[2]
        w2 = self._ent_box(e2)[2]
        center_x = (e1.x + w1 / 2 + e2.x + w2 / 2) / 2
        e1.x = int(center_x - w1 / 2)
        e2.x = int(center_x - w2 / 2)

        if self.notation == "chen":
            h1 = self._ent_box(e1)[3]
            h2 = self._ent_box(e2)[3]
            center_y1 = e1.y + h1 / 2
            center_y2 = e2.y + h2 / 2
            rel.pos_x = int(center_x)
            rel.pos_y = int((center_y1 + center_y2) / 2)

        self.render()
        self.status_msg.config(text=f"Relacionamento '{rel.name}' alinhado verticalmente.")

    def auto_align_selected_rel(self):
        """Detecta a orientação predominante (horizontal ou vertical) e alinha o relacionamento selecionado."""
        rel = None
        if self.selected_item and self.selected_item[0] == "rel":
            rel = self.project.find_rel(self.selected_item[1])
        elif self.selected_item and self.selected_item[0] == "entity":
            eid = self.selected_item[1]
            rels = [r for r in self.project.rels if eid in (r.entity1_id, r.entity2_id)]
            if rels:
                rel = rels[0]
        if not rel or rel.is_nary:
            messagebox.showinfo("Alinhar", "Selecione um relacionamento binário para alinhar.", parent=self)
            return
        e1 = self.project.find_entity(rel.entity1_id)
        e2 = self.project.find_entity(rel.entity2_id)
        if not e1 or not e2:
            return
        dx = abs((e2.x + self._ent_box(e2)[2] / 2) - (e1.x + self._ent_box(e1)[2] / 2))
        dy = abs((e2.y + self._ent_box(e2)[3] / 2) - (e1.y + self._ent_box(e1)[3] / 2))
        if dx >= dy:
            self.align_rel_horizontal(rel)
        else:
            self.align_rel_vertical(rel)

    def auto_align_all_relationships(self):
        """Percorre todos os relacionamentos binários do diagrama e alinha cada um automaticamente."""
        if not self.project.rels:
            return
        self._push_undo()
        self._mark_active_dirty()
        count = 0
        for rel in self.project.rels:
            if rel.is_nary or rel.entity1_id == rel.entity2_id:
                continue
            e1 = self.project.find_entity(rel.entity1_id)
            e2 = self.project.find_entity(rel.entity2_id)
            if not e1 or not e2:
                continue
            dx = abs((e2.x + self._ent_box(e2)[2] / 2) - (e1.x + self._ent_box(e1)[2] / 2))
            dy = abs((e2.y + self._ent_box(e2)[3] / 2) - (e1.y + self._ent_box(e1)[3] / 2))
            if dx >= dy:
                h1 = self._ent_box(e1)[3]
                h2 = self._ent_box(e2)[3]
                center_y = (e1.y + h1 / 2 + e2.y + h2 / 2) / 2
                e2.y = int(center_y - h2 / 2)
                if self.notation == "chen":
                    rel.pos_y = int(center_y)
            else:
                w1 = self._ent_box(e1)[2]
                w2 = self._ent_box(e2)[2]
                center_x = (e1.x + w1 / 2 + e2.x + w2 / 2) / 2
                e2.x = int(center_x - w2 / 2)
                if self.notation == "chen":
                    rel.pos_x = int(center_x)
            count += 1
        self.render()
        self.status_msg.config(text=f"{count} relacionamentos alinhados com sucesso.")

    def _zoom_from_combo(self, _e=None):
        txt = self.zoom_var.get().replace("%", "").strip()
        try:
            self.set_zoom(float(txt.replace(",", ".")) / 100.0)
        except ValueError:
            self._update_zoom_label()
        self.canvas.focus_set()

    def focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def _center_on(self, mx, my):
        cw, ch = self.canvas.winfo_width() or 1200, self.canvas.winfo_height() or 800
        self.pan_x = (cw / 2) / self.zoom - mx
        self.pan_y = (ch / 2) / self.zoom - my
        self.render()

    def _selected_center(self):
        sel = self.selected_item
        if not sel:
            return None
        if sel[0] in ("entity", "attr"):
            e = self.project.find_entity(sel[1])
            if e:
                x, y, w, h = self._ent_box(e)
                return x + w / 2, y + h / 2
        elif sel[0] in ("rel", "rel_attr"):
            r = self.project.find_rel(sel[1])
            if r:
                return self._rel_pos(r)
        elif sel[0] == "spec":
            sp = self.project.find_spec(sel[1])
            if sp:
                return self._spec_pos(sp)
        return None

    def center_selection(self):
        c = self._selected_center()
        if c:
            self._center_on(*c)
        else:
            self.status_msg.config(text="Selecione um elemento para centralizar.")

    def find_element(self):
        """Busca entidades, atributos e relacionamentos; Enter repetido percorre os resultados."""
        q = self.search_var.get().strip().lower()
        if not q:
            return
        hits = []
        for e in self.project.entities:
            if q in e.name.lower():
                hits.append((f"entidade {e.name}", ("entity", e.id)))

        def walk(attrs):
            for a in attrs:
                yield a
                yield from walk(a.sub_attrs)
        for e in self.project.entities:
            for a in walk(e.attrs):
                if q in a.name.lower():
                    hits.append((f"atributo {e.name}.{a.name}", ("attr", e.id, a.id)))
        for r in self.project.rels:
            if q in r.name.lower():
                hits.append((f"relacionamento {r.name}", ("rel", r.id)))
        if not hits:
            self.status_msg.config(text=f"Nada encontrado para '{q}'.")
            return
        self._search_idx = (self._search_idx + 1) % len(hits) if self._search_key == q else 0
        self._search_key = q
        label, sel = hits[self._search_idx]
        self.selected_item = sel
        if sel[0] == "entity":
            self._update_inspector("entity", self.project.find_entity(sel[1]))
        elif sel[0] == "rel":
            self._update_inspector("rel", self.project.find_rel(sel[1]))
        else:
            e = self.project.find_entity(sel[1])
            attr = next((a for a in walk(e.attrs) if a.id == sel[2]), None)
            self._update_inspector("attr", attr, e)
        self.status_msg.config(text=f"Encontrado {self._search_idx + 1} de {len(hits)}: {label}  (Enter = próximo)")
        c = self._selected_center()
        if c:
            self._center_on(*c)
        else:
            self.render()

    # ---------------- Salvar todos ----------------
    def save_all(self):
        current = self.active_document
        saved = failed = skipped = 0
        for d in list(self.documents):
            if d["path"]:
                if not d["dirty"]:
                    continue
                try:
                    self._write_json_atomically(d["path"], lambda t, doc=d: doc["project"].save(t))
                    self._write_document_snapshot(d)
                    d["dirty"] = False
                    saved += 1
                except OSError as ex:
                    failed += 1
                    messagebox.showerror("Salvar todos", f"Não foi possível salvar '{d['name']}':\n{ex}")
            else:
                self._activate_document(d)
                if self.save_project_as():
                    saved += 1
                else:
                    skipped += 1
        if current in self.documents:
            self._activate_document(current)
        self._refresh_document_tabs()
        self._persist_session()
        self.status_msg.config(text=f"Salvar todos: {saved} salvo(s)" + (f", {skipped} sem nome ignorado(s)" if skipped else "")
                                     + (f", {failed} com erro" if failed else "") + ".")

    # ---------------- Menu suspenso ----------------
    def _build_menubar(self):
        mb = tk.Menu(self, tearoff=0)
        self.ribbon_var = tk.BooleanVar(value=True)

        arq = tk.Menu(mb, tearoff=0)
        arq.add_command(label="Novo projeto", accelerator="Ctrl+N", command=self.new_project)
        arq.add_command(label="Abrir…", accelerator="Ctrl+O", command=self.load_project)
        arq.add_command(label="Importar modelo…", accelerator="Ctrl+I", command=self.open_import_dialog)
        ex = tk.Menu(arq, tearoff=0)
        ex.add_command(label="Empresa (Navathe cap. 3)", command=self.load_navathe_example)
        ex.add_command(label="EER (especialização, categoria, domínio)", command=self.load_eer_example)
        arq.add_cascade(label="Criar a partir de exemplo", menu=ex)
        arq.add_separator()
        arq.add_command(label="Salvar", accelerator="Ctrl+S", command=self.save_project)
        arq.add_command(label="Salvar como…", accelerator="Ctrl+Shift+S", command=self.save_project_as)
        arq.add_command(label="Salvar todos", command=self.save_all)
        arq.add_separator()
        exp = tk.Menu(arq, tearoff=0)
        exp.add_command(label="Relatório HTML…", command=self.export_html)
        exp.add_command(label="Documento PDF…", command=self.export_pdf)
        arq.add_cascade(label="Exportar", menu=exp)
        ddl = tk.Menu(arq, tearoff=0)
        ddl.add_command(label="DDL transacional…", command=lambda: self.save_ddl("transacional"))
        ddl.add_command(label="DDL dimensional…", command=lambda: self.save_ddl("dimensional"))
        ddl.add_command(label="Ambos…", command=lambda: self.save_ddl("ambos"))
        arq.add_cascade(label="Gravar DDL (.sql)", menu=ddl)
        arq.add_separator()
        arq.add_command(label="Sair", command=self.close_application)
        mb.add_cascade(label="Arquivo", menu=arq)

        ed = tk.Menu(mb, tearoff=0)
        ed.add_command(label="Desfazer", accelerator="Ctrl+Z", command=self.undo)
        ed.add_command(label="Refazer", accelerator="Ctrl+Y", command=self.redo)
        ed.add_separator()
        ed.add_command(label="Editar seleção", accelerator="F2", command=self.edit_selected_from_inspector)
        ed.add_command(label="Duplicar", accelerator="Ctrl+D", command=self.duplicate_selected)
        ed.add_command(label="Excluir", accelerator="Del", command=self.delete_selected)
        ed.add_separator()
        ed.add_command(label="Buscar elemento", accelerator="Ctrl+F", command=self.focus_search)
        mb.add_cascade(label="Editar", menu=ed)

        ex_ = tk.Menu(mb, tearoff=0)
        ex_.add_command(label="Aumentar zoom", accelerator="Ctrl++", command=self.zoom_in)
        ex_.add_command(label="Diminuir zoom", accelerator="Ctrl+−", command=self.zoom_out)
        ex_.add_command(label="Zoom 100%", accelerator="Ctrl+1", command=self.zoom_reset)
        ex_.add_command(label="Ajustar modelo à tela", accelerator="Ctrl+0", command=self.zoom_fit)
        ex_.add_command(label="Zoom em uma área", command=self.toggle_zoom_select_mode)
        ex_.add_command(label="Centralizar seleção", command=self.center_selection)
        ex_.add_separator()
        nt = tk.Menu(ex_, tearoff=0)
        nt.add_command(label="Chen / EER (Navathe)", command=lambda: self.set_notation("chen"))
        nt.add_command(label="Barker", command=lambda: self.set_notation("barker"))
        ex_.add_cascade(label="Notação", menu=nt)
        ex_.add_separator()
        ex_.add_checkbutton(label="Grade", accelerator="Ctrl+G", variable=self.grid_var, command=self.toggle_grid)
        ex_.add_checkbutton(label="Encaixar na grade", variable=self.snap_var, command=self.toggle_snap)
        ex_.add_checkbutton(label="Legenda", variable=self.legend_var, command=self.toggle_legend)
        ex_.add_checkbutton(label="Paleta lateral", variable=self.sidebar_var, command=self.toggle_sidebar)
        ex_.add_checkbutton(label="Faixa de opções", accelerator="Ctrl+F1", variable=self.ribbon_var, command=self.toggle_ribbon)
        mb.add_cascade(label="Exibir", menu=ex_)

        ins = tk.Menu(mb, tearoff=0)
        ins.add_command(label="Entidade forte", command=lambda: self.add_entity_custom(is_weak=False))
        ins.add_command(label="Entidade fraca", command=lambda: self.add_entity_custom(is_weak=True))
        ins.add_separator()
        ins.add_command(label="Relacionamento", accelerator="Ctrl+L", command=self.toggle_link)
        ins.add_command(label="Relacionamento identificador", command=lambda: self.toggle_link(identifying=True))
        ins.add_command(label="Relacionamento n-ário", command=self.toggle_nary)
        ins.add_command(label="Arco de exclusividade (Barker XOR)…", command=self.open_arc_dialog)
        ins.add_separator()
        ins.add_command(label="Especialização", command=lambda: self.new_specialization("specialization"))
        ins.add_command(label="Generalização", command=lambda: self.new_specialization("generalization"))
        ins.add_command(label="Categoria (união)", command=lambda: self.new_specialization("union"))
        ins.add_separator()
        ins.add_command(label="Atributo na seleção", command=self.add_attribute_to_selected)
        ins.add_command(label="Domínios…", command=self.open_domains)
        mb.add_cascade(label="Inserir", menu=ins)

        dia = tk.Menu(mb, tearoff=0)
        dia.add_command(label="Alinhar horizontalmente (mesmo nível Y)", accelerator="Ctrl+H", command=self.align_rel_horizontal)
        dia.add_command(label="Alinhar verticalmente (mesma coluna X)", accelerator="Ctrl+Shift+H", command=self.align_rel_vertical)
        dia.add_command(label="Auto-alinhar relacionamento selecionado", command=self.auto_align_selected_rel)
        dia.add_separator()
        dia.add_command(label="Auto-alinhar todos os relacionamentos", command=self.auto_align_all_relationships)
        mb.add_cascade(label="Diagramação", menu=dia)

        db = tk.Menu(mb, tearoff=0)
        db.add_command(label="Validar modelo", accelerator="F5", command=self.validate_model)
        db.add_command(label="Ver DDL transacional", accelerator="F9", command=self.show_ddl)
        db.add_command(label="Ver DDL dimensional", command=self.show_kimball_ddl)
        db.add_separator()
        db.add_command(label="Oracle: conectar / dicionário / criar tabelas…", command=self.open_oracle)
        db.add_command(label="Oracle: desconectar", command=self.disconnect_oracle)
        mb.add_cascade(label="Banco de dados", menu=db)

        aj = tk.Menu(mb, tearoff=0)
        aj.add_command(label="Atalhos e dicas", accelerator="F1", command=self.show_help)
        mb.add_cascade(label="Ajuda", menu=aj)
        self.config(menu=mb)
        self.bind_all("<Control-F1>", lambda _e: self.toggle_ribbon())
