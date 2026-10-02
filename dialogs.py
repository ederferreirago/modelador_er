"""Janelas (Toplevel) de edição - Notação de Chen / EER (Navathe 6ª Edição)."""
import copy
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from models import Attribute, Domain, Specialization, RelationshipArc

TYPES = ["STRING", "INTEGER", "DECIMAL", "DATE", "BOOLEAN", "TEXT"]

ATTR_TYPE_MAP = [
    ("simple", "Simples (Elipse contínua)"),
    ("pk", "Chave Primária (Sublinhado sólido)"),
    ("partial_key", "Chave Parcial (Sublinhado tracejado)"),
    ("multivalued", "Multivalorado (Elipse dupla)"),
    ("derived", "Derivado (Elipse tracejada)"),
    ("composite", "Composto (Ramificado)"),
]
ATTR_TYPE_LABELS = [lbl for _, lbl in ATTR_TYPE_MAP]
ATTR_TYPE_TO_LABEL = dict(ATTR_TYPE_MAP)
LABEL_TO_ATTR_TYPE = {lbl: code for code, lbl in ATTR_TYPE_MAP}

KIMBALL_ROLE_MAP = [(None, "Nenhum (fora do esquema estrela)"), ("dimension", "Dimensão"), ("fact", "Fato")]
KIMBALL_ROLE_TO_LABEL = dict(KIMBALL_ROLE_MAP)
LABEL_TO_KIMBALL_ROLE = {lbl: code for code, lbl in KIMBALL_ROLE_MAP}

SCD_TYPE_MAP = [("scd1", "SCD Tipo 1 (sobrescreve o histórico)"), ("scd2", "SCD Tipo 2 (mantém histórico versionado)")]
SCD_TYPE_TO_LABEL = dict(SCD_TYPE_MAP)
LABEL_TO_SCD_TYPE = {lbl: code for code, lbl in SCD_TYPE_MAP}

NO_DOMAIN = "(nenhum)"
PART_LABELS = ["Parcial (Linha simples)", "Total (Linha dupla)"]
DOMAIN_IMPL = [("lookup", "Tabela de domínio + FK"), ("check", "Somente CHECK (IN ...)")]


# ---------------------------------------------------------------- utilidades
def bind_mousewheel(widget, canvas):
    """Rolagem com a roda do mouse enquanto o ponteiro estiver sobre `widget`."""
    def _wheel(e):
        if getattr(e, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(e, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        else:
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _enter(_e):
        widget.bind_all("<MouseWheel>", _wheel)
        widget.bind_all("<Button-4>", _wheel)
        widget.bind_all("<Button-5>", _wheel)

    def _leave(_e):
        widget.unbind_all("<MouseWheel>")
        widget.unbind_all("<Button-4>")
        widget.unbind_all("<Button-5>")

    widget.bind("<Enter>", _enter)
    widget.bind("<Leave>", _leave)


class ScrollFrame(ttk.Frame):
    """Frame com rolagem vertical e horizontal para tabelas editáveis."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bg="#F4F6FC")
        vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        bind_mousewheel(self, self.canvas)


def unique_labels(items, key=lambda x: x.name):
    """{id: rótulo único} — desambigua nomes repetidos com #n."""
    labels, seen = {}, {}
    for it in items:
        base = key(it) or "(sem nome)"
        seen[base] = seen.get(base, 0) + 1
        labels[it.id] = base if seen[base] == 1 else f"{base} #{seen[base]}"
    return labels


def parse_domain_text(text):
    """Converte texto colado em valores de domínio.

    Aceita, por linha: `1 - Atualizado`, `1 = Atualizado`, `1;Atualizado;detalhe`, `1<TAB>Atualizado<TAB>detalhe`.
    """
    values = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = None
        for sep in ("\t", ";", " - ", "="):
            if sep in line:
                parts = [p.strip() for p in line.split(sep, 2)]
                break
        if parts is None:
            m = re.match(r"^(\S+)\s+(.+)$", line)
            parts = [m.group(1), m.group(2)] if m else [line, line]
        while len(parts) < 3:
            parts.append("")
        if parts[0]:
            values.append({"code": parts[0], "label": parts[1] or parts[0], "description": parts[2]})
    return values


# ---------------------------------------------------------------- Domínios
class DomainManagerDialog(tk.Toplevel):
    """Cria/edita os domínios de valores do projeto (fornecidos pelo usuário)."""

    def __init__(self, parent, project, on_change, on_before_save=None, select_id=None):
        super().__init__(parent)
        self.title("Domínios de Valores")
        self.geometry("900x600")
        self.minsize(780, 480)
        self.transient(parent)
        self.project = project
        self.on_change = on_change
        self.on_before_save = on_before_save
        self.work = [Domain.from_dict(d.to_dict()) for d in project.domains]
        self.current = None
        self.value_rows = []

        left = ttk.Frame(self, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Domínios", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(left, width=26, exportselection=False, activestyle="none")
        self.listbox.pack(fill="y", expand=True, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        ttk.Button(left, text="＋ Novo domínio", command=self.new_domain).pack(fill="x", pady=1)
        ttk.Button(left, text="⧉ Duplicar", command=self.duplicate_domain).pack(fill="x", pady=1)
        ttk.Button(left, text="🗑 Excluir", command=self.delete_domain).pack(fill="x", pady=1)
        self.usage_lbl = ttk.Label(left, text="", foreground="#73809B", wraplength=170, justify="left")
        self.usage_lbl.pack(anchor="w", pady=(8, 0))

        ttk.Separator(self, orient="vertical").pack(side="left", fill="y")

        btns = ttk.Frame(self, padding=8)
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text="💾 Salvar", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Fechar", command=self.destroy).pack(side="left", padx=4)
        ttk.Label(btns, text="Ex.: Status → 1 = Atualizado, 2 = Desatualizado, 3 = Fechado",
                  foreground="#73809B").pack(side="right")

        self.right = ttk.Frame(self, padding=8)
        self.right.pack(side="left", fill="both", expand=True)

        form = ttk.Frame(self.right)
        form.pack(fill="x")
        self.name_var, self.type_var = tk.StringVar(), tk.StringVar(value="INTEGER")
        self.impl_var, self.desc_var = tk.StringVar(), tk.StringVar()
        ttk.Label(form, text="Nome:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.name_var, width=28).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Tipo do código:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Combobox(form, textvariable=self.type_var, values=TYPES, width=10, state="readonly").grid(row=0, column=3, padx=4)
        ttk.Label(form, text="No DDL:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(form, textvariable=self.impl_var, values=[l for _, l in DOMAIN_IMPL], width=26,
                     state="readonly").grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Descrição:").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(form, textvariable=self.desc_var).grid(row=2, column=1, columnspan=3, sticky="we", padx=4)
        form.columnconfigure(3, weight=1)

        head = ttk.Frame(self.right)
        head.pack(fill="x", pady=(10, 2))
        ttk.Label(head, text="Valores do domínio", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(head, text="📋 Colar lista…", command=self.import_text).pack(side="right")
        ttk.Button(head, text="＋ Valor", command=self.add_value).pack(side="right", padx=4)

        self.values_frame = ScrollFrame(self.right)
        self.values_frame.pack(fill="both", expand=True)
        self._refresh_list(select_id)

    # ----- lista
    def _refresh_list(self, select_id=None):
        self.listbox.delete(0, "end")
        for d in self.work:
            self.listbox.insert("end", d.name)
        target = next((i for i, d in enumerate(self.work) if d.id == select_id), 0 if self.work else None)
        if target is not None:
            self.listbox.selection_set(target)
            self._load(self.work[target])
        else:
            self._load(None)

    def _on_select(self, _e=None):
        sel = self.listbox.curselection()
        if sel:
            self._commit_form()
            self._load(self.work[sel[0]])

    def _commit_form(self):
        d = self.current
        if not d:
            return
        d.name = self.name_var.get().strip() or d.name
        d.type = self.type_var.get()
        d.impl = next((c for c, l in DOMAIN_IMPL if l == self.impl_var.get()), "lookup")
        d.description = self.desc_var.get().strip()
        d.values = [{"code": c.get().strip(), "label": l.get().strip(), "description": x.get().strip()}
                    for c, l, x in self.value_rows if c.get().strip() or l.get().strip()]
        idx = self.work.index(d)
        self.listbox.delete(idx)
        self.listbox.insert(idx, d.name)
        self.listbox.selection_set(idx)

    def _load(self, d):
        self.current = d
        for w in self.values_frame.inner.winfo_children():
            w.destroy()
        self.value_rows = []
        state = "normal" if d else "disabled"
        if not d:
            self.name_var.set("")
            self.usage_lbl.config(text="Crie um domínio para começar.")
            return
        self.name_var.set(d.name)
        self.type_var.set(d.type)
        self.impl_var.set(dict(DOMAIN_IMPL)[d.impl])
        self.desc_var.set(d.description)
        uses = sum(1 for a in self.project.iter_attributes() if a.domain_id == d.id)
        self.usage_lbl.config(text=f"Usado por {uses} atributo(s).")
        f = self.values_frame.inner
        for c, h in enumerate(["Código", "Descrição do valor", "Detalhamento (opcional)", ""]):
            ttk.Label(f, text=h, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, padx=3, pady=3, sticky="w")
        for i, v in enumerate(d.values, start=1):
            self._value_row(f, i, v)

    def _value_row(self, f, i, v):
        cv, lv, xv = tk.StringVar(value=v["code"]), tk.StringVar(value=v["label"]), tk.StringVar(value=v.get("description", ""))
        ttk.Entry(f, textvariable=cv, width=9).grid(row=i, column=0, padx=3, pady=1)
        ttk.Entry(f, textvariable=lv, width=30).grid(row=i, column=1, padx=3)
        ttk.Entry(f, textvariable=xv, width=38).grid(row=i, column=2, padx=3)
        ttk.Button(f, text="×", width=2, command=lambda row=(cv, lv, xv): self.remove_value(row)).grid(row=i, column=3, padx=3)
        self.value_rows.append((cv, lv, xv))

    def add_value(self):
        if not self.current:
            return
        self._commit_form()
        codes = [v["code"] for v in self.current.values]
        nxt = "1"
        if self.current.is_numeric():
            nums = [int(c) for c in codes if c.lstrip("-").isdigit()]
            nxt = str(max(nums) + 1 if nums else 1)
        self.current.values.append({"code": nxt, "label": "", "description": ""})
        self._load(self.current)

    def remove_value(self, row):
        self._commit_form()
        try:
            idx = self.value_rows.index(row)
        except ValueError:
            return
        # _commit_form descartou linhas vazias; localiza por posição atual
        if idx < len(self.current.values):
            del self.current.values[idx]
        self._load(self.current)

    def import_text(self):
        if not self.current:
            return
        win = tk.Toplevel(self)
        win.title("Colar lista de valores")
        win.geometry("480x340")
        win.transient(self)
        ttk.Label(win, text="Um valor por linha: 1 - Atualizado  |  2 = Desatualizado  |  3;Fechado;detalhe\n"
                            "(também aceita colunas coladas do Excel)", justify="left").pack(anchor="w", padx=8, pady=6)
        txt = tk.Text(win, height=10, font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=8)
        replace_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(win, text="Substituir valores atuais", variable=replace_var).pack(anchor="w", padx=8, pady=4)

        def apply():
            vals = parse_domain_text(txt.get("1.0", "end"))
            if not vals:
                messagebox.showwarning("Colar lista", "Nenhum valor reconhecido.", parent=win)
                return
            self._commit_form()
            self.current.values = vals if replace_var.get() else self.current.values + vals
            self._load(self.current)
            win.destroy()
        ttk.Button(win, text="Importar", command=apply).pack(pady=8)
        txt.focus_set()

    # ----- CRUD
    def new_domain(self):
        self._commit_form()
        base, n = "Novo_dominio", 1
        names = {d.name for d in self.work}
        name = base
        while name in names:
            n += 1
            name = f"{base}{n}"
        d = Domain(name=name, values=[{"code": "1", "label": "", "description": ""}])
        self.work.append(d)
        self._refresh_list(d.id)

    def duplicate_domain(self):
        if not self.current:
            return
        self._commit_form()
        c = Domain.from_dict(self.current.to_dict())
        c.id = Domain().id
        c.name += "_copia"
        self.work.append(c)
        self._refresh_list(c.id)

    def delete_domain(self):
        if not self.current:
            return
        uses = sum(1 for a in self.project.iter_attributes() if a.domain_id == self.current.id)
        msg = f"Excluir o domínio '{self.current.name}'?"
        if uses:
            msg += f"\n{uses} atributo(s) que o usam ficarão sem domínio."
        if messagebox.askyesno("Excluir domínio", msg, parent=self):
            self.work.remove(self.current)
            self.current = None
            self._refresh_list()

    def save(self):
        self._commit_form()
        problems = []
        names = [d.name.lower() for d in self.work]
        for d in self.work:
            if names.count(d.name.lower()) > 1:
                problems.append(f"Nome de domínio repetido: {d.name}")
            codes = [v["code"] for v in d.values]
            if len(set(codes)) != len(codes):
                problems.append(f"{d.name}: códigos repetidos")
            if not d.values:
                problems.append(f"{d.name}: sem valores")
            if d.is_numeric():
                for v in d.values:
                    try:
                        float(v["code"])
                    except ValueError:
                        problems.append(f"{d.name}: código '{v['code']}' não é numérico (tipo {d.type})")
            for v in d.values:
                if not v["label"]:
                    problems.append(f"{d.name}: valor '{v['code']}' sem descrição")
        if problems:
            messagebox.showerror("Domínios", "Corrija antes de salvar:\n\n• " + "\n• ".join(problems[:12]), parent=self)
            return
        if self.on_before_save:
            self.on_before_save()
        old_ids = {d.id for d in self.project.domains}
        new_ids = {d.id for d in self.work}
        removed = old_ids - new_ids
        self.project.domains = self.work
        if removed:
            for a in self.project.iter_attributes():
                if a.domain_id in removed:
                    a.domain_id = None
        self.on_change()
        self.destroy()


# ---------------------------------------------------------------- Entidade
class EntityDialog(tk.Toplevel):
    """Editar entidade (nome, forte/fraca, descrição, atributos com domínio/descrição, Kimball).

    Trabalha sobre uma cópia: nada muda no modelo até clicar em Salvar."""

    def __init__(self, parent, entity, on_change, on_before_save=None, project=None, focus_attr_id=None):
        super().__init__(parent)
        self.title("Editar Entidade — Notação de Chen")
        self.entity = entity
        self.project = project or parent.project
        self.on_change = on_change
        self.on_before_save = on_before_save
        self.parent_app = parent
        self.work = [Attribute.from_dict(a.to_dict()) for a in entity.attrs]
        self.rows = []
        self.geometry("1240x700")
        self.minsize(980, 540)
        self.transient(parent)
        self._focus_attr_id = focus_attr_id

        top = ttk.Frame(self, padding=(8, 8, 8, 2))
        top.pack(fill="x")
        ttk.Label(top, text="Nome da Entidade:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 6))
        self.name_var = tk.StringVar(value=entity.name)
        name_entry = ttk.Entry(top, textvariable=self.name_var, width=24)
        name_entry.pack(side="left", padx=(0, 16))
        self.weak_var = tk.BooleanVar(value=entity.is_weak)
        ttk.Checkbutton(top, text="Entidade Fraca (Retângulo duplo / Chave Parcial)", variable=self.weak_var).pack(side="left")

        desc_row = ttk.Frame(self, padding=(8, 2))
        desc_row.pack(fill="x")
        ttk.Label(desc_row, text="Descrição:", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 6))
        self.desc_var = tk.StringVar(value=entity.description)
        ttk.Entry(desc_row, textvariable=self.desc_var).pack(side="left", fill="x", expand=True)

        opt = ttk.Frame(self, padding=(8, 2))
        opt.pack(fill="x")
        ttk.Label(opt, text="Raio / Espaçamento dos Atributos:").pack(side="left", padx=(0, 6))
        self.spacing_var = tk.DoubleVar(value=getattr(entity, "attr_spacing", 85.0))
        ttk.Scale(opt, from_=50, to=180, variable=self.spacing_var, orient="horizontal", length=140).pack(side="left", padx=4)
        ttk.Button(opt, text="🔄 Resetar Posições dos Atributos", command=self.reset_attr_offsets).pack(side="left", padx=8)

        kb = ttk.LabelFrame(self, text="⭐ Modelagem Dimensional (Kimball)", padding=6)
        kb.pack(fill="x", padx=8, pady=(2, 4))
        ttk.Label(kb, text="Papel Dimensional:").pack(side="left", padx=(0, 6))
        self.kimball_role_var = tk.StringVar(value=KIMBALL_ROLE_TO_LABEL.get(getattr(entity, "kimball_role", None), KIMBALL_ROLE_TO_LABEL[None]))
        role_combo = ttk.Combobox(kb, textvariable=self.kimball_role_var, values=[l for _, l in KIMBALL_ROLE_MAP], width=28, state="readonly")
        role_combo.pack(side="left", padx=(0, 16))
        role_combo.bind("<<ComboboxSelected>>", lambda e: self._update_scd_state())
        ttk.Label(kb, text="Tipo SCD (se Dimensão):").pack(side="left", padx=(0, 6))
        self.scd_type_var = tk.StringVar(value=SCD_TYPE_TO_LABEL.get(getattr(entity, "scd_type", "scd1"), SCD_TYPE_TO_LABEL["scd1"]))
        self.scd_combo = ttk.Combobox(kb, textvariable=self.scd_type_var, values=[l for _, l in SCD_TYPE_MAP], width=32, state="readonly")
        self.scd_combo.pack(side="left")
        self._update_scd_state()

        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        btns = ttk.Frame(self, padding=8)
        btns.pack(side="bottom", fill="x")
        ttk.Button(btns, text="+ Atributo", command=self.add_row).pack(side="left", padx=4)
        ttk.Button(btns, text="📚 Domínios…", command=self.open_domains).pack(side="left", padx=4)
        ttk.Button(btns, text="💾 Salvar (Ctrl+Enter)", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="Fechar (Esc)", command=self.destroy).pack(side="left", padx=4)
        ttk.Label(btns, text="Chave=sublinhado sólido | Parcial=tracejado | Multivalorado=elipse dupla | Derivado=tracejada",
                  font=("Segoe UI", 8), foreground="#555").pack(side="right", padx=6)

        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=4)
        self.table_frame = self.scroll.inner
        self._build_rows()
        self.bind("<Control-Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        name_entry.focus_set()
        name_entry.select_range(0, "end")

    # ----- domínios
    def _domain_labels(self):
        labels = unique_labels(self.project.domains)
        return labels, {v: k for k, v in labels.items()}

    def open_domains(self):
        self._sync_from_widgets()
        DomainManagerDialog(self, self.project, self._domains_changed, self.on_before_save)

    def _domains_changed(self):
        self.parent_app._mark_model_changed()
        self._build_rows()

    def _update_scd_state(self):
        role = LABEL_TO_KIMBALL_ROLE.get(self.kimball_role_var.get())
        self.scd_combo.state(["!disabled"] if role == "dimension" else ["disabled"])

    # ----- linhas
    def _sync_from_widgets(self):
        for r in self.rows:
            a = r["attr"]
            a.name = r["name"].get().strip() or a.name
            a.attr_type = LABEL_TO_ATTR_TYPE.get(r["cat"].get(), "simple")
            a.nn = r["nn"].get() or (a.attr_type == "pk")
            a.unique = r["uq"].get()
            a.description = r["desc"].get().strip()
            dom_id = r["dom_map"].get(r["dom"].get())
            a.domain_id = dom_id
            dom = self.project.find_domain(dom_id) if dom_id else None
            a.type = dom.type if dom else r["type"].get()
            if a.is_composite:
                subs = []
                for token in [t.strip() for t in r["sub"].get().split(",") if t.strip()]:
                    nm, _, tp = token.partition(":")
                    nm, tp = nm.strip(), tp.strip().upper()
                    ex = next((s for s in a.sub_attrs if s.name == nm), None)
                    if ex:
                        if tp in TYPES:
                            ex.type = tp
                        subs.append(ex)
                    else:
                        subs.append(Attribute(name=nm, type=tp if tp in TYPES else "STRING", attr_type="simple"))
                a.sub_attrs = subs
            else:
                a.sub_attrs = []

    def _build_rows(self):
        for w in self.table_frame.winfo_children():
            w.destroy()
        self.rows = []
        labels, rev = self._domain_labels()
        dom_values = [NO_DOMAIN] + list(labels.values())
        dom_map = {NO_DOMAIN: None, **rev}
        heads = ["", "Nome", "Tipo de Dado", "Classificação Chen", "Domínio", "Descrição", "Sub-atributos (Composto)", "Único", "NOT NULL", ""]
        for c, h in enumerate(heads):
            ttk.Label(self.table_frame, text=h, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, padx=3, pady=4, sticky="w")

        focus_entry = None
        for i, a in enumerate(self.work, start=1):
            name_var = tk.StringVar(value=a.name)
            type_var = tk.StringVar(value=a.type)
            cat_var = tk.StringVar(value=ATTR_TYPE_TO_LABEL.get(a.attr_type, ATTR_TYPE_TO_LABEL["simple"]))
            dom_var = tk.StringVar(value=labels.get(a.domain_id, NO_DOMAIN))
            desc_var = tk.StringVar(value=a.description)
            sub_var = tk.StringVar(value=", ".join(s.name if s.type == "STRING" else f"{s.name}:{s.type}" for s in a.sub_attrs))
            nn_var, uq_var = tk.BooleanVar(value=a.nn), tk.BooleanVar(value=a.unique)

            mv = ttk.Frame(self.table_frame)
            mv.grid(row=i, column=0, padx=2)
            ttk.Button(mv, text="▲", width=2, command=lambda at=a: self.move(at, -1)).pack(side="left")
            ttk.Button(mv, text="▼", width=2, command=lambda at=a: self.move(at, 1)).pack(side="left")

            e = ttk.Entry(self.table_frame, textvariable=name_var, width=16)
            e.grid(row=i, column=1, padx=3, pady=2)
            type_combo = ttk.Combobox(self.table_frame, textvariable=type_var, values=TYPES, width=9, state="readonly")
            type_combo.grid(row=i, column=2, padx=3)
            ttk.Combobox(self.table_frame, textvariable=cat_var, values=ATTR_TYPE_LABELS, width=27, state="readonly").grid(row=i, column=3, padx=3)
            dom_combo = ttk.Combobox(self.table_frame, textvariable=dom_var, values=dom_values, width=16, state="readonly")
            dom_combo.grid(row=i, column=4, padx=3)
            ttk.Entry(self.table_frame, textvariable=desc_var, width=34).grid(row=i, column=5, padx=3)
            ttk.Entry(self.table_frame, textvariable=sub_var, width=22).grid(row=i, column=6, padx=3)
            ttk.Checkbutton(self.table_frame, variable=uq_var).grid(row=i, column=7, padx=3)
            ttk.Checkbutton(self.table_frame, variable=nn_var).grid(row=i, column=8, padx=3)
            act = ttk.Frame(self.table_frame)
            act.grid(row=i, column=9, padx=3)
            ttk.Button(act, text="⧉", width=2, command=lambda at=a: self.duplicate(at)).pack(side="left")
            ttk.Button(act, text="×", width=2, command=lambda at=a: self.remove_attr(at)).pack(side="left")

            def on_dom(_ev=None, tv=type_var, dv=dom_var, tc=type_combo):
                dom = self.project.find_domain(dom_map.get(dv.get()))
                if dom:
                    tv.set(dom.type)
                    tc.state(["disabled"])
                else:
                    tc.state(["!disabled"])
            dom_combo.bind("<<ComboboxSelected>>", on_dom)
            if a.domain_id and self.project.find_domain(a.domain_id):
                on_dom()
            self.rows.append({"attr": a, "name": name_var, "type": type_var, "cat": cat_var, "dom": dom_var,
                              "desc": desc_var, "sub": sub_var, "nn": nn_var, "uq": uq_var, "dom_map": dom_map})
            if a.id == self._focus_attr_id:
                focus_entry = e
        if focus_entry is not None:
            self._focus_attr_id = None
            focus_entry.focus_set()
            focus_entry.select_range(0, "end")

    def add_row(self):
        self._sync_from_widgets()
        default_cat = "partial_key" if self.weak_var.get() and not self.work else "simple"
        na = Attribute("novo_campo", "STRING", attr_type=default_cat)
        self.work.append(na)
        self._focus_attr_id = na.id
        self._build_rows()
        self.scroll.canvas.after(50, lambda: self.scroll.canvas.yview_moveto(1.0))

    def duplicate(self, attr):
        self._sync_from_widgets()
        c = Attribute.from_dict(attr.to_dict())
        c.id = Attribute().id
        c.name += "_copia"
        c.offset_x = c.offset_y = None
        if c.attr_type in ("pk", "partial_key"):
            c.attr_type = "simple"
        self.work.insert(self.work.index(attr) + 1, c)
        self._focus_attr_id = c.id
        self._build_rows()

    def remove_attr(self, attr):
        self._sync_from_widgets()
        self.work = [a for a in self.work if a.id != attr.id]
        self._build_rows()

    def move(self, attr, delta):
        self._sync_from_widgets()
        i = self.work.index(attr)
        j = i + delta
        if 0 <= j < len(self.work):
            self.work[i], self.work[j] = self.work[j], self.work[i]
            self._build_rows()

    def reset_attr_offsets(self):
        for a in self.work:
            a.offset_x = None
            a.offset_y = None
            for s in a.sub_attrs:
                s.offset_x = s.offset_y = None
        messagebox.showinfo("Resetar Posições", "As posições dos atributos voltarão à distribuição automática ao salvar.", parent=self)

    def save(self):
        self._sync_from_widgets()
        names = [a.name.lower() for a in self.work]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            messagebox.showerror("Atributos", f"Atributos com nome repetido: {', '.join(dup)}", parent=self)
            return
        if self.on_before_save:
            self.on_before_save()
        self.entity.name = self.name_var.get().strip() or self.entity.name
        self.entity.is_weak = self.weak_var.get()
        self.entity.description = self.desc_var.get().strip()
        self.entity.attr_spacing = self.spacing_var.get()
        self.entity.kimball_role = LABEL_TO_KIMBALL_ROLE.get(self.kimball_role_var.get())
        self.entity.scd_type = LABEL_TO_SCD_TYPE.get(self.scd_type_var.get(), "scd1")
        self.entity.attrs = self.work
        self.on_change()
        self.destroy()


# ---------------------------------------------------------------- Relacionamento
class CardinalityDialog(tk.Toplevel):
    """Configurar cardinalidade, participação, papéis e tipo ao criar nova ligação."""

    def __init__(self, parent, a_id, b_id, on_confirm):
        super().__init__(parent)
        self.title("Criar Relacionamento — Notação de Chen")
        self.resizable(False, False)
        self.on_confirm = on_confirm
        self.parent = parent
        self.a_id, self.b_id = a_id, b_id
        a, b = parent.project.find_entity(a_id), parent.project.find_entity(b_id)
        self.a_name = a.name if a else "A"
        self.b_name = b.name if b else "B"
        is_self_rel = (a_id == b_id)
        pad = {"padx": 12, "pady": 4}
        self.transient(parent)

        title_text = f"Auto-relacionamento: {self.a_name}" if is_self_rel else f"{self.a_name}  ⟷  {self.b_name}"
        ttk.Label(self, text=title_text, font=("Segoe UI", 11, "bold")).pack(pady=8)

        f_name = ttk.Frame(self)
        f_name.pack(fill="x", **pad)
        ttk.Label(f_name, text="Nome do relacionamento:").pack(side="left")
        self.name_var = tk.StringVar(value="relaciona" if not is_self_rel else "supervisao")
        name_entry = ttk.Entry(f_name, textvariable=self.name_var, width=20)
        name_entry.pack(side="left", padx=8)

        is_weak_involved = bool((a and a.is_weak) or (b and b.is_weak))
        self.identifying_var = tk.BooleanVar(value=is_weak_involved)
        ttk.Checkbutton(self, text="Relacionamento Identificador (Losango Duplo — Entidade Fraca)",
                        variable=self.identifying_var).pack(anchor="w", **pad)

        f_card = ttk.LabelFrame(self, text="Razão de Cardinalidade", padding=8)
        f_card.pack(fill="x", **pad)
        self.card_choice = tk.StringVar(value="1N")
        ttk.Radiobutton(f_card, text="1:1 — Um para Um", variable=self.card_choice, value="11").pack(anchor="w")
        ttk.Radiobutton(f_card, text=f"1:N — {self.a_name} (1) ⟷ {self.b_name} (N)", variable=self.card_choice, value="1N").pack(anchor="w")
        if not is_self_rel:
            ttk.Radiobutton(f_card, text=f"N:1 — {self.a_name} (N) ⟷ {self.b_name} (1)", variable=self.card_choice, value="N1").pack(anchor="w")
        ttk.Radiobutton(f_card, text="M:N — Muitos para Muitos", variable=self.card_choice, value="MN").pack(anchor="w")

        f_part = ttk.LabelFrame(self, text="Restrição de Participação (Linha Simples / Dupla)", padding=8)
        f_part.pack(fill="x", **pad)
        self.part1_var, self.part2_var = tk.StringVar(value=PART_LABELS[0]), tk.StringVar(value=PART_LABELS[1 if is_weak_involved else 0])
        for var, nm, lado in ((self.part1_var, self.a_name, 1), (self.part2_var, self.b_name, 2)):
            row = ttk.Frame(f_part)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{nm} (Lado {lado}):", width=18).pack(side="left")
            ttk.Combobox(row, textvariable=var, values=PART_LABELS, state="readonly", width=22).pack(side="left")

        f_roles = ttk.LabelFrame(self, text="Papéis / Roles (opcional para relacionamentos recursivos)", padding=8)
        f_roles.pack(fill="x", **pad)
        self.role1_var = tk.StringVar(value="Supervisor" if is_self_rel else "")
        self.role2_var = tk.StringVar(value="Supervisionado" if is_self_rel else "")
        for lbl, var in (("Papel Lado 1:", self.role1_var), ("Papel Lado 2:", self.role2_var)):
            row = ttk.Frame(f_roles)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lbl, width=14).pack(side="left")
            ttk.Entry(row, textvariable=var, width=16).pack(side="left")

        btns = ttk.Frame(self)
        btns.pack(pady=12)
        ttk.Button(btns, text="Confirmar (Enter)", command=self.confirm).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar (Esc)", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Return>", lambda e: self.confirm())
        self.bind("<Escape>", lambda e: self.destroy())
        name_entry.focus_set()
        name_entry.select_range(0, "end")

    def confirm(self):
        choice = self.card_choice.get()
        name = self.name_var.get().strip() or "relaciona"
        card1, card2 = {"11": ("1", "1"), "1N": ("1", "N"), "N1": ("N", "1")}.get(choice, ("M", "N"))
        self.on_confirm(self.a_id, self.b_id, card1, card2, name,
                        "total" if "Total" in self.part1_var.get() else "partial",
                        "total" if "Total" in self.part2_var.get() else "partial",
                        self.role1_var.get().strip(), self.role2_var.get().strip(), self.identifying_var.get())
        self.destroy()


class RelationshipDialog(tk.Toplevel):
    """Editar relacionamento (binário ou n-ário): cardinalidade, participação, papéis, atributos."""

    def __init__(self, parent, rel, on_change, project, on_before_save=None):
        super().__init__(parent)
        self.title("Editar Relacionamento — Notação de Chen")
        self.geometry("880x760")
        self.minsize(760, 600)
        self.transient(parent)
        self.rel, self.on_change, self.project = rel, on_change, project
        self.on_before_save = on_before_save
        self.parent_app = parent
        self.work_attrs = [Attribute.from_dict(a.to_dict()) for a in rel.attrs]
        self.work_extra = [dict(p) for p in rel.extra_parts]
        self.ent_labels = unique_labels(project.entities)
        self.ent_rev = {v: k for k, v in self.ent_labels.items()}

        e1, e2 = project.find_entity(rel.entity1_id), project.find_entity(rel.entity2_id)
        e1_name, e2_name = (e1.name if e1 else "?"), (e2.name if e2 else "?")
        is_self = rel.entity1_id == rel.entity2_id
        pad = {"padx": 12, "pady": 3}

        ttk.Label(self, text=f"Auto-relacionamento: {e1_name}" if is_self else f"{e1_name}  ⟷  {e2_name}",
                  font=("Segoe UI", 11, "bold")).pack(pady=6)

        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        btns = ttk.Frame(self, padding=(0, 8))
        btns.pack(side="bottom", pady=2)
        ttk.Button(btns, text="💾 Salvar (Ctrl+Enter)", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="🗑 Excluir", command=self.delete).pack(side="left", padx=4)
        ttk.Button(btns, text="Fechar (Esc)", command=self.destroy).pack(side="left", padx=4)

        f_name = ttk.Frame(self)
        f_name.pack(fill="x", **pad)
        ttk.Label(f_name, text="Nome:").pack(side="left")
        self.name_var = tk.StringVar(value=rel.name)
        ne = ttk.Entry(f_name, textvariable=self.name_var, width=24)
        ne.pack(side="left", padx=8)
        self.identifying_var = tk.BooleanVar(value=rel.is_identifying)
        ttk.Checkbutton(f_name, text="Identificador (Losango Duplo — Entidade Fraca)", variable=self.identifying_var).pack(side="left", padx=12)

        f_desc = ttk.Frame(self)
        f_desc.pack(fill="x", **pad)
        ttk.Label(f_desc, text="Descrição:").pack(side="left")
        self.desc_var = tk.StringVar(value=rel.description)
        ttk.Entry(f_desc, textvariable=self.desc_var).pack(side="left", fill="x", expand=True, padx=8)

        f_grid = ttk.LabelFrame(self, text="Participantes: cardinalidade, participação e papel", padding=8)
        f_grid.pack(fill="x", **pad)
        for c, h in enumerate(["Entidade", "Cardinalidade", "Participação", "Papel", ""]):
            ttk.Label(f_grid, text=h, font=("Segoe UI", 8, "bold")).grid(row=0, column=c, padx=3, sticky="w")
        self.card1_var, self.card2_var = tk.StringVar(value=rel.card1), tk.StringVar(value=rel.card2)
        self.part1_var = tk.StringVar(value=PART_LABELS[1 if rel.part1 == "total" else 0])
        self.part2_var = tk.StringVar(value=PART_LABELS[1 if rel.part2 == "total" else 0])
        self.role1_var, self.role2_var = tk.StringVar(value=rel.role1), tk.StringVar(value=rel.role2)
        for r_i, (nm, cv, pv, rv) in enumerate([(e1_name, self.card1_var, self.part1_var, self.role1_var),
                                                (e2_name, self.card2_var, self.part2_var, self.role2_var)], start=1):
            ttk.Label(f_grid, text=nm, font=("Segoe UI", 9, "bold")).grid(row=r_i, column=0, sticky="w", padx=3, pady=2)
            ttk.Combobox(f_grid, textvariable=cv, values=["1", "N", "M"], width=5, state="readonly").grid(row=r_i, column=1, padx=3)
            ttk.Combobox(f_grid, textvariable=pv, values=PART_LABELS, width=22, state="readonly").grid(row=r_i, column=2, padx=3)
            ttk.Entry(f_grid, textvariable=rv, width=18).grid(row=r_i, column=3, padx=3)
        self.extra_frame = ttk.Frame(f_grid)
        self.extra_frame.grid(row=3, column=0, columnspan=5, sticky="w")
        self.extra_rows = []
        self._build_extra()
        ttk.Button(f_grid, text="＋ Participante (relacionamento n-ário / ternário)", command=self.add_extra).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(6, 0), padx=3)

        f_attrs = ttk.LabelFrame(self, text="Atributos Próprios do Relacionamento (ex: Horas, Data_inicio)", padding=8)
        f_attrs.pack(fill="both", expand=True, **pad)
        self.attr_scroll = ScrollFrame(f_attrs)
        self.attr_scroll.pack(fill="both", expand=True)
        self.rel_attr_rows = []
        self._build_rel_attr_rows()
        ttk.Button(f_attrs, text="+ Atributo no Relacionamento", command=self.add_rel_attr).pack(anchor="w", pady=4)
        ttk.Button(f_attrs, text="📚 Domínios…", command=self.open_domains).pack(anchor="w")
        self.bind("<Control-Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        ne.focus_set()
        ne.select_range(0, "end")

    # ----- participantes extras
    def _build_extra(self):
        for w in self.extra_frame.winfo_children():
            w.destroy()
        self.extra_rows = []
        for i, p in enumerate(self.work_extra):
            ev = tk.StringVar(value=self.ent_labels.get(p["entity_id"], ""))
            cv = tk.StringVar(value=p["card"])
            pv = tk.StringVar(value=PART_LABELS[1 if p["part"] == "total" else 0])
            rv = tk.StringVar(value=p["role"])
            ttk.Combobox(self.extra_frame, textvariable=ev, values=list(self.ent_labels.values()), width=16, state="readonly").grid(row=i, column=0, padx=3, pady=2)
            ttk.Combobox(self.extra_frame, textvariable=cv, values=["1", "N", "M"], width=5, state="readonly").grid(row=i, column=1, padx=3)
            ttk.Combobox(self.extra_frame, textvariable=pv, values=PART_LABELS, width=22, state="readonly").grid(row=i, column=2, padx=3)
            ttk.Entry(self.extra_frame, textvariable=rv, width=18).grid(row=i, column=3, padx=3)
            ttk.Button(self.extra_frame, text="×", width=2, command=lambda idx=i: self.remove_extra(idx)).grid(row=i, column=4, padx=3)
            self.extra_rows.append((ev, cv, pv, rv))

    def _sync_extra(self):
        self.work_extra = [{"entity_id": self.ent_rev.get(ev.get()), "card": cv.get(),
                            "part": "total" if "Total" in pv.get() else "partial", "role": rv.get().strip()}
                           for ev, cv, pv, rv in self.extra_rows]

    def add_extra(self):
        self._sync_extra()
        first = next(iter(self.ent_labels), None)
        self.work_extra.append({"entity_id": first, "card": "N", "part": "partial", "role": ""})
        self._build_extra()

    def remove_extra(self, idx):
        self._sync_extra()
        del self.work_extra[idx]
        self._build_extra()

    # ----- atributos
    def open_domains(self):
        self._sync_attrs()
        DomainManagerDialog(self, self.project, self._domains_changed, self.on_before_save)

    def _domains_changed(self):
        self.parent_app._mark_model_changed()
        self._build_rel_attr_rows()

    def _sync_attrs(self):
        for a, n_var, t_var, d_var, ds_var, dom_map in self.rel_attr_rows:
            a.name = n_var.get().strip() or a.name
            a.domain_id = dom_map.get(d_var.get())
            dom = self.project.find_domain(a.domain_id) if a.domain_id else None
            a.type = dom.type if dom else t_var.get()
            a.description = ds_var.get().strip()

    def _build_rel_attr_rows(self):
        f = self.attr_scroll.inner
        for w in f.winfo_children():
            w.destroy()
        self.rel_attr_rows = []
        labels = unique_labels(self.project.domains)
        dom_map = {NO_DOMAIN: None, **{v: k for k, v in labels.items()}}
        for c, h in enumerate(["Nome do Atributo", "Tipo", "Domínio", "Descrição", ""]):
            ttk.Label(f, text=h, font=("Segoe UI", 8, "bold")).grid(row=0, column=c, padx=3, pady=2, sticky="w")
        for i, a in enumerate(self.work_attrs, start=1):
            n_var, t_var = tk.StringVar(value=a.name), tk.StringVar(value=a.type)
            d_var = tk.StringVar(value=labels.get(a.domain_id, NO_DOMAIN))
            ds_var = tk.StringVar(value=a.description)
            ttk.Entry(f, textvariable=n_var, width=18).grid(row=i, column=0, padx=3, pady=1)
            tc = ttk.Combobox(f, textvariable=t_var, values=TYPES, width=10, state="readonly")
            tc.grid(row=i, column=1, padx=3)
            dc = ttk.Combobox(f, textvariable=d_var, values=[NO_DOMAIN] + list(labels.values()), width=16, state="readonly")
            dc.grid(row=i, column=2, padx=3)
            ttk.Entry(f, textvariable=ds_var, width=36).grid(row=i, column=3, padx=3)
            ttk.Button(f, text="×", width=2, command=lambda attr=a: self.remove_rel_attr(attr)).grid(row=i, column=4, padx=3)

            def on_dom(_e=None, tv=t_var, dv=d_var, tcb=tc):
                dom = self.project.find_domain(dom_map.get(dv.get()))
                if dom:
                    tv.set(dom.type)
                    tcb.state(["disabled"])
                else:
                    tcb.state(["!disabled"])
            dc.bind("<<ComboboxSelected>>", on_dom)
            on_dom()
            self.rel_attr_rows.append((a, n_var, t_var, d_var, ds_var, dom_map))

    def add_rel_attr(self):
        self._sync_attrs()
        self.work_attrs.append(Attribute("novo_campo", "STRING", attr_type="simple"))
        self._build_rel_attr_rows()

    def remove_rel_attr(self, attr):
        self._sync_attrs()
        self.work_attrs = [a for a in self.work_attrs if a.id != attr.id]
        self._build_rel_attr_rows()

    def save(self):
        self._sync_attrs()
        self._sync_extra()
        if any(not p["entity_id"] for p in self.work_extra):
            messagebox.showerror("Participantes", "Escolha a entidade de cada participante adicional.", parent=self)
            return
        if self.on_before_save:
            self.on_before_save()
        r = self.rel
        r.name = self.name_var.get().strip() or r.name
        r.description = self.desc_var.get().strip()
        r.is_identifying = self.identifying_var.get() and not self.work_extra
        r.card1, r.card2 = self.card1_var.get(), self.card2_var.get()
        r.part1 = "total" if "Total" in self.part1_var.get() else "partial"
        r.part2 = "total" if "Total" in self.part2_var.get() else "partial"
        r.role1, r.role2 = self.role1_var.get().strip(), self.role2_var.get().strip()
        r.extra_parts = self.work_extra
        r.attrs = self.work_attrs
        self.on_change()
        self.destroy()

    def delete(self):
        if messagebox.askyesno("Excluir", f"Excluir o relacionamento '{self.rel.name}'?", parent=self):
            if self.on_before_save:
                self.on_before_save()
            self.project.rels = [r for r in self.project.rels if r.id != self.rel.id]
            self.on_change()
            self.destroy()


# ---------------------------------------------------------------- Especialização / Categoria
class SpecializationDialog(tk.Toplevel):
    """Cria/edita Especialização, Generalização ou Categoria (união) — Navathe cap. 8."""

    KINDS = [("specialization", "Especialização (top-down)"), ("generalization", "Generalização (bottom-up)"),
             ("union", "Categoria / União (subclasse de várias superclasses)")]

    def __init__(self, parent, project, spec, on_confirm, is_new=True, preset_parents=None, preset_children=None):
        super().__init__(parent)
        self.title("Nova Especialização / Generalização" if is_new else "Editar Especialização / Generalização")
        self.geometry("720x640")
        self.minsize(640, 560)
        self.transient(parent)
        self.project, self.spec, self.on_confirm, self.is_new = project, spec, on_confirm, is_new
        self.ents = list(project.entities)
        self.labels = unique_labels(self.ents)
        ids = [e.id for e in self.ents]
        pad = {"padx": 12, "pady": 4}

        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        btns = ttk.Frame(self, padding=8)
        btns.pack(side="bottom")
        ttk.Button(btns, text="Confirmar", command=self.confirm).pack(side="left", padx=4)
        if not is_new:
            ttk.Button(btns, text="🗑 Excluir", command=self.delete).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancelar (Esc)", command=self.destroy).pack(side="left", padx=4)

        f_kind = ttk.LabelFrame(self, text="Tipo", padding=8)
        f_kind.pack(fill="x", **pad)
        self.kind_var = tk.StringVar(value=spec.kind)
        for k, lbl in self.KINDS:
            ttk.Radiobutton(f_kind, text=lbl, variable=self.kind_var, value=k, command=self._kind_changed).pack(anchor="w")

        lists = ttk.Frame(self)
        lists.pack(fill="both", expand=True, **pad)
        for col, (title, attr) in enumerate((("Superclasse(s)", "lb_parent"), ("Subclasse(s)", "lb_child"))):
            box = ttk.LabelFrame(lists, text=title, padding=6)
            box.grid(row=0, column=col, sticky="nsew", padx=4)
            lb = tk.Listbox(box, exportselection=False, height=8, activestyle="none")
            for e in self.ents:
                lb.insert("end", self.labels[e.id])
            lb.pack(fill="both", expand=True)
            setattr(self, attr, lb)
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        for eid in (preset_parents if preset_parents is not None else spec.parent_ids):
            if eid in ids:
                self.lb_parent.selection_set(ids.index(eid))
        for eid in (preset_children if preset_children is not None else spec.child_ids):
            if eid in ids:
                self.lb_child.selection_set(ids.index(eid))

        f_c = ttk.LabelFrame(self, text="Restrições", padding=8)
        f_c.pack(fill="x", **pad)
        self.disj_var = tk.StringVar(value=spec.disjointness if spec.disjointness in ("d", "o") else "d")
        self.comp_var = tk.StringVar(value=spec.completeness)
        self.disj_rbs = [ttk.Radiobutton(f_c, text="Disjunta — círculo 'd'", variable=self.disj_var, value="d"),
                         ttk.Radiobutton(f_c, text="Sobreposta — círculo 'o'", variable=self.disj_var, value="o")]
        ttk.Label(f_c, text="Disjunção:").grid(row=0, column=0, sticky="w")
        for i, rb in enumerate(self.disj_rbs):
            rb.grid(row=0, column=i + 1, sticky="w", padx=8)
        ttk.Label(f_c, text="Completude:").grid(row=1, column=0, sticky="w")
        ttk.Radiobutton(f_c, text="Total — linha dupla", variable=self.comp_var, value="total").grid(row=1, column=1, sticky="w", padx=8)
        ttk.Radiobutton(f_c, text="Parcial — linha simples", variable=self.comp_var, value="partial").grid(row=1, column=2, sticky="w", padx=8)

        f_o = ttk.Frame(self)
        f_o.pack(fill="x", **pad)
        ttk.Label(f_o, text="Atributo definidor:").grid(row=0, column=0, sticky="w")
        self.def_var = tk.StringVar(value=spec.defining_attr)
        ttk.Entry(f_o, textvariable=self.def_var, width=24).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(f_o, text="(rótulo na linha da superclasse; opcional)", foreground="#73809B").grid(row=0, column=2, sticky="w")
        ttk.Label(f_o, text="Mapeamento no DDL:").grid(row=1, column=0, sticky="w", pady=4)
        self.map_var = tk.StringVar(value="Uma tabela por classe (8A)" if spec.mapping == "multi" else "Tabela única (8C/8D)")
        self.map_combo = ttk.Combobox(f_o, textvariable=self.map_var, state="readonly", width=26,
                                      values=["Uma tabela por classe (8A)", "Tabela única (8C/8D)"])
        self.map_combo.grid(row=1, column=1, padx=6, sticky="w")
        ttk.Label(f_o, text="Descrição:").grid(row=2, column=0, sticky="w")
        self.desc_var = tk.StringVar(value=spec.description)
        ttk.Entry(f_o, textvariable=self.desc_var, width=50).grid(row=2, column=1, columnspan=2, sticky="we", padx=6)
        self._kind_changed()
        self.bind("<Escape>", lambda e: self.destroy())

    def _kind_changed(self):
        union = self.kind_var.get() == "union"
        self.lb_parent.config(selectmode="extended" if union else "browse")
        self.lb_child.config(selectmode="extended" if not union else "browse")
        for lb in (self.lb_parent, self.lb_child):
            sel = lb.curselection()
            if lb.cget("selectmode") == "browse" and len(sel) > 1:
                lb.selection_clear(0, "end")
                lb.selection_set(sel[0])
        for rb in self.disj_rbs:
            rb.state(["disabled"] if union else ["!disabled"])
        self.map_combo.state(["disabled"] if union else ["!disabled"])

    def _selected(self, lb):
        return [self.ents[i].id for i in lb.curselection()]

    def confirm(self):
        kind = self.kind_var.get()
        parents, children = self._selected(self.lb_parent), self._selected(self.lb_child)
        err = None
        if not parents or not children:
            err = "Selecione ao menos uma superclasse e uma subclasse."
        elif set(parents) & set(children):
            err = "Uma entidade não pode ser superclasse e subclasse da mesma especialização."
        elif kind == "union" and len(parents) < 2:
            err = "Uma categoria (união) precisa de 2 ou mais superclasses."
        elif kind == "union" and len(children) != 1:
            err = "Uma categoria (união) tem exatamente 1 subclasse."
        elif kind != "union" and len(parents) != 1:
            err = "Especialização/Generalização tem exatamente 1 superclasse."
        else:
            other = [sp for sp in self.project.specs if sp.id != self.spec.id and not sp.is_union and kind != "union"]
            clash = [c for c in children for sp in other if c in sp.child_ids]
            if clash:
                name = self.project.find_entity(clash[0]).name
                err = f"'{name}' já é subclasse de outra especialização (herança múltipla não é suportada)."
        if err:
            messagebox.showerror("Especialização", err, parent=self)
            return
        s = self.spec
        s.kind, s.parent_ids, s.child_ids = kind, parents, children
        s.disjointness = "u" if kind == "union" else self.disj_var.get()
        s.completeness = self.comp_var.get()
        s.defining_attr = self.def_var.get().strip()
        s.mapping = "multi" if self.map_var.get().startswith("Uma") else "single"
        s.description = self.desc_var.get().strip()
        self.on_confirm(s, self.is_new)
        self.destroy()

    def delete(self):
        if messagebox.askyesno("Excluir", "Excluir esta especialização?", parent=self):
            self.on_confirm(None, False, delete_id=self.spec.id)
            self.destroy()


# ---------------------------------------------------------------- Validação e DDL
class ValidationWindow(tk.Toplevel):
    ICONS = {"erro": "⛔", "aviso": "⚠", "info": "ℹ"}

    def __init__(self, parent, issues):
        super().__init__(parent)
        self.title("Validação do Modelo")
        self.geometry("820x460")
        self.transient(parent)
        n_err = sum(1 for s, _ in issues if s == "erro")
        n_warn = sum(1 for s, _ in issues if s == "aviso")
        summary = "✅ Nenhum problema encontrado." if not issues else \
            f"{n_err} erro(s), {n_warn} aviso(s), {len(issues) - n_err - n_warn} informação(ões)"
        ttk.Label(self, text=summary, font=("Segoe UI", 10, "bold"), padding=8).pack(anchor="w")
        ttk.Button(self, text="Fechar", command=self.destroy).pack(side="bottom", pady=8)
        tree = ttk.Treeview(self, columns=("sev", "msg"), show="headings")
        tree.heading("sev", text="Gravidade")
        tree.heading("msg", text="Mensagem")
        tree.column("sev", width=90, stretch=False)
        tree.column("msg", width=700)
        for sev, msg in issues:
            tree.insert("", "end", values=(f"{self.ICONS[sev]} {sev}", msg))
        tree.pack(fill="both", expand=True, padx=8)
        self.bind("<Escape>", lambda e: self.destroy())


class DDLWindow(tk.Toplevel):
    """Mostra o DDL gerado (com realce de sintaxe), com opções de copiar e salvar."""

    KEYWORDS = r"\b(CREATE TABLE|ALTER TABLE|ADD COLUMN|ADD CONSTRAINT|ADD|CONSTRAINT|PRIMARY KEY|FOREIGN KEY|REFERENCES|" \
               r"UNIQUE|NOT NULL|CHECK|IN|DEFAULT|ON DELETE CASCADE|CREATE INDEX|ON|INSERT INTO|VALUES|COMMENT ON TABLE|" \
               r"COMMENT ON COLUMN|COMMENT|IS|GENERATED BY DEFAULT AS IDENTITY|AUTO_INCREMENT|NULL)\b"

    def __init__(self, parent, dialect, ddl_text, title_text=None, file_prefix="modelo", generator=None):
        super().__init__(parent)
        self.title(title_text or f"DDL Gerado — {dialect.upper()} (Mapeamento Navathe)")
        self.geometry("900x640")
        self.minsize(560, 380)
        self.transient(parent)
        self.ddl_text, self.file_prefix, self.generator = ddl_text, file_prefix, generator
        self.dialect_var = tk.StringVar(value=dialect)

        bar = ttk.Frame(self, padding=6)
        bar.pack(side="top", fill="x")
        if generator:
            ttk.Label(bar, text="SGBD:").pack(side="left")
            cb = ttk.Combobox(bar, textvariable=self.dialect_var, values=["postgres", "mysql", "oracle"], width=10, state="readonly")
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self.regenerate())
        ttk.Button(bar, text="📋 Copiar", command=self.copy).pack(side="left", padx=4)
        ttk.Button(bar, text="💾 Salvar .sql", command=self.save).pack(side="left", padx=4)
        ttk.Button(bar, text="Fechar", command=self.destroy).pack(side="left", padx=4)
        self.info = ttk.Label(bar, text="", foreground="#73809B")
        self.info.pack(side="right")

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.txt = tk.Text(frame, wrap="none", font=("Consolas", 10), undo=False)
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.txt.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("kw", foreground="#4938D0", font=("Consolas", 10, "bold"))
        self.txt.tag_configure("cm", foreground="#15803d")
        self.txt.tag_configure("st", foreground="#b45309")
        self._show(ddl_text)
        self.bind("<Escape>", lambda e: self.destroy())

    def _show(self, text):
        self.ddl_text = text
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        for m in re.finditer(self.KEYWORDS, text):
            self.txt.tag_add("kw", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        for m in re.finditer(r"'(?:[^']|'')*'", text):
            self.txt.tag_add("st", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        for m in re.finditer(r"--[^\n]*", text):
            self.txt.tag_add("cm", f"1.0+{m.start()}c", f"1.0+{m.end()}c")
        self.txt.config(state="disabled")
        self.info.config(text=f"{len(text.splitlines())} linhas · {text.count('CREATE TABLE')} tabelas")

    def regenerate(self):
        try:
            self._show(self.generator(self.dialect_var.get()))
        except Exception as ex:
            messagebox.showerror("Gerar DDL", str(ex), parent=self)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.ddl_text)
        self.info.config(text="DDL copiado para a área de transferência.")

    def save(self):
        path = filedialog.asksaveasfilename(defaultextension=".sql", parent=self,
                                            initialfile=f"{self.file_prefix}_{self.dialect_var.get()}.sql",
                                            filetypes=[("SQL", "*.sql")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.ddl_text)
            self.info.config(text=f"Salvo: {path}")


class ArcDialog(tk.Toplevel):
    """Diálogo para criação e edição de Arcos de Relacionamento (Barker / Oracle Designer).

    Permite agrupar relacionamentos mutuamente exclusivos em torno de uma entidade âncora.
    """

    def __init__(self, parent, project, arc=None):
        super().__init__(parent)
        self.project = project
        self.arc = arc
        self.result = None
        self.title("Arco de Exclusividade (Barker / Oracle Designer)" if not arc else f"Editar Arco: {arc.name}")
        self.geometry("560x520")
        self.transient(parent)
        self.grab_set()

        # Entidades disponíveis com pelo menos 2 relacionamentos
        self.entities = project.entities
        if not self.entities:
            messagebox.showwarning("Arco", "O modelo não possui entidades.", parent=self)
            self.destroy()
            return

        ent_initial = arc.entity_id if arc else self.entities[0].id
        self.ent_var = tk.StringVar(value=ent_initial)
        self.name_var = tk.StringVar(value=arc.name if arc else "")
        self.mandatory_var = tk.BooleanVar(value=arc.mandatory if arc else True)
        self.desc_var = tk.StringVar(value=arc.description if arc else "")
        self.rel_checks = {}  # rel_id -> BooleanVar

        self._build_ui()
        self._refresh_rels()

    def _build_ui(self):
        pad = 12
        frame = ttk.Frame(self, padding=pad)
        frame.pack(fill="both", expand=True)

        # Cabeçalho explicativo
        hdr = ttk.Label(
            frame,
            text="Na notação de Barker (Oracle Designer), um Arco conecta dois ou mais relacionamentos\n"
                 "de uma entidade para indicar exclusividade mútua (XOR).",
            foreground="#55627D",
            font=("Segoe UI", 9, "italic")
        )
        hdr.pack(anchor="w", pady=(0, 10))

        # Entidade Âncora
        frow = ttk.Frame(frame)
        frow.pack(fill="x", pady=4)
        ttk.Label(frow, text="Entidade Âncora:", width=16).pack(side="left")
        self.ent_combo = ttk.Combobox(
            frow,
            values=[f"{e.name} ({e.id})" for e in self.entities],
            state="readonly",
            width=36
        )
        curr_e = self.project.find_entity(self.ent_var.get())
        if curr_e:
            self.ent_combo.set(f"{curr_e.name} ({curr_e.id})")
        self.ent_combo.bind("<<ComboboxSelected>>", self._on_ent_changed)
        self.ent_combo.pack(side="left", padx=6)

        # Nome do Arco
        nrow = ttk.Frame(frame)
        nrow.pack(fill="x", pady=4)
        ttk.Label(nrow, text="Nome do Arco:", width=16).pack(side="left")
        ttk.Entry(nrow, textvariable=self.name_var, width=38).pack(side="left", padx=6)

        # Mandatoriedade
        mrow = ttk.Frame(frame)
        mrow.pack(fill="x", pady=4)
        ttk.Label(mrow, text="Tipo de Arco:", width=16).pack(side="left")
        ttk.Radiobutton(mrow, text="Obrigatório (Exatamente um — linha contínua)",
                        variable=self.mandatory_var, value=True).pack(anchor="w")
        ttk.Radiobutton(mrow, text="Opcional (No máximo um — linha tracejada)",
                        variable=self.mandatory_var, value=False).pack(anchor="w", padx=(120, 0))

        # Lista de Relacionamentos com Checkboxes
        lframe = ttk.LabelFrame(frame, text="Relacionamentos Participantes (selecione pelo menos 2)", padding=8)
        lframe.pack(fill="both", expand=True, pady=10)

        self.canvas = tk.Canvas(lframe, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(lframe, orient="vertical", command=self.canvas.yview)
        self.scroll_frame = ttk.Frame(self.canvas)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        bind_mousewheel(self.scroll_frame, self.canvas)

        # Descrição
        drow = ttk.Frame(frame)
        drow.pack(fill="x", pady=4)
        ttk.Label(drow, text="Descrição / Regra:", width=16).pack(side="left")
        ttk.Entry(drow, textvariable=self.desc_var, width=44).pack(side="left", padx=6)

        # Botões Salvar / Cancelar
        btn_box = ttk.Frame(frame)
        btn_box.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_box, text="Cancelar", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btn_box, text="Salvar Arco", command=self._save).pack(side="right", padx=4)

    def _on_ent_changed(self, _event=None):
        sel = self.ent_combo.get()
        if "(" in sel and sel.endswith(")"):
            eid = sel.split("(")[-1].rstrip(")")
            self.ent_var.set(eid)
            self._refresh_rels()

    def _refresh_rels(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        self.rel_checks.clear()

        eid = self.ent_var.get()
        ent = self.project.find_entity(eid)
        if not ent:
            return

        # Busca todos os relacionamentos onde essa entidade participa
        connected_rels = [
            r for r in self.project.rels
            if eid in (r.entity1_id, r.entity2_id) or any(p.get("entity_id") == eid for p in r.extra_parts)
        ]

        if not connected_rels:
            ttk.Label(self.scroll_frame, text=f"A entidade '{ent.name}' não possui relacionamentos conectados.",
                      foreground="#9CA3AF").pack(anchor="w", pady=4)
            return

        active_rids = set(self.arc.rel_ids) if self.arc else set()
        for r in connected_rels:
            other_ids = [p[0] for p in r.participants() if p[0] != eid]
            other_names = [self.project.find_entity(oid).name for oid in other_ids if self.project.find_entity(oid)]
            other_txt = f" ➔ {', '.join(other_names)}" if other_names else ""
            var = tk.BooleanVar(value=r.id in active_rids)
            self.rel_checks[r.id] = var
            cb = ttk.Checkbutton(
                self.scroll_frame,
                text=f"{r.name}{other_txt} ({r.card1}:{r.card2})",
                variable=var
            )
            cb.pack(anchor="w", pady=2)

    def _save(self):
        eid = self.ent_var.get()
        selected_rids = [rid for rid, var in self.rel_checks.items() if var.get()]

        if len(selected_rids) < 2:
            messagebox.showwarning(
                "Arco de Exclusividade",
                "Um arco deve conter pelo menos 2 relacionamentos mutuamente exclusivos.",
                parent=self
            )
            return

        ent = self.project.find_entity(eid)
        name = self.name_var.get().strip() or f"arc_{ent.name.lower()}"

        if self.arc:
            self.arc.entity_id = eid
            self.arc.rel_ids = selected_rids
            self.arc.name = name
            self.arc.mandatory = self.mandatory_var.get()
            self.arc.description = self.desc_var.get().strip()
            self.result = self.arc
        else:
            self.result = RelationshipArc(
                entity_id=eid,
                rel_ids=selected_rids,
                name=name,
                mandatory=self.mandatory_var.get(),
                description=self.desc_var.get().strip(),
            )
            self.project.add_arc(self.result)

        self.destroy()


class ImportModelDialog(tk.Toplevel):
    """Diálogo para importação de Modelos Conceituais e Dimensionais a partir de múltiplos formatos."""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Importar Modelo Conceitual / Dimensional")
        self.geometry("580x440")
        self.transient(parent)
        self.grab_set()

        self._build_ui()

    def _build_ui(self):
        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(
            f,
            text="Selecione a fonte de dados para importar o modelo:",
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))

        ttk.Label(
            f,
            text="O Modelador ER irá analisar as tabelas, atributos, chaves primárias, estrangeiras e domínios,\n"
                 "gerando o diagrama conceitual com posicionamento visual automático.",
            foreground="#55627D"
        ).pack(anchor="w", pady=(0, 14))

        options = [
            ("📊 Matriz DE-PARA em Excel (.xlsx)",
             "Importa tabelas fato, dimensões (SCD1/SCD2), campos e domínios de matrizes de rastreabilidade DW/BI.",
             self._import_excel),
            ("📁 Arquivo de Modelo ER em JSON (.json)",
             "Importa especificações de modelos conceituais completos (com ou sem diagrama prévio).",
             self._import_json),
            ("⚙ Script SQL DDL (.sql)",
             "Lê instruções CREATE TABLE, PRIMARY KEY e FOREIGN KEY de scripts SQL para gerar o modelo conceitual.",
             self._import_sql),
            ("🏛 Exemplo Dimensional Estrela (SEFAZ / Correios DTE)",
             "Carrega o modelo dimensional completo de Mensagens em Lote / DTE pronto para estudo e documentação.",
             self._load_sample_star),
        ]

        for title, desc, cmd in options:
            box = ttk.Frame(f, padding=8, relief="groove")
            box.pack(fill="x", pady=5)
            row = ttk.Frame(box)
            row.pack(fill="x")
            ttk.Label(row, text=title, font=("Segoe UI", 10, "bold")).pack(side="left")
            ttk.Button(row, text="Importar...", command=cmd).pack(side="right")
            ttk.Label(box, text=desc, foreground="#64748B", wraplength=520, justify="left").pack(anchor="w", pady=(3, 0))

        btn_box = ttk.Frame(f)
        btn_box.pack(fill="x", pady=(14, 0))
        ttk.Button(btn_box, text="Fechar", command=self.destroy).pack(side="right")

    def _apply_project(self, new_project, source_name):
        doc_name = f"Importado ({source_name})"
        document = self.app._add_document(new_project, doc_name)
        self.app._activate_document(document)
        self.app.zoom_fit()
        self.app.render()
        if hasattr(self.app, "status_msg"):
            self.app.status_msg.config(
                text=f"Modelo importado com sucesso: {len(new_project.entities)} entidades, "
                     f"{len(new_project.rels)} relacionamentos."
            )
        messagebox.showinfo(
            "Importação Concluída",
            f"Modelo importado com sucesso a partir de:\n{source_name}\n\n"
            f"• {len(new_project.entities)} Entidades / Tabelas\n"
            f"• {len(new_project.rels)} Relacionamentos\n"
            f"• {len(new_project.domains)} Domínios de Valores\n\n"
            f"O diagrama foi gerado com layout limpo no editor.",
            parent=self
        )
        self.destroy()

    def _import_excel(self):
        import model_importer
        path = filedialog.askopenfilename(
            parent=self,
            title="Selecionar Planilha Excel DE-PARA",
            filetypes=[("Planilhas Excel", "*.xlsx;*.xlsm"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        try:
            proj = model_importer.import_from_dexpara_excel(path)
            self._apply_project(proj, os.path.basename(path))
        except Exception as ex:
            messagebox.showerror("Erro na Importação", f"Falha ao ler a planilha Excel:\n{ex}", parent=self)

    def _import_json(self):
        import model_importer
        path = filedialog.askopenfilename(
            parent=self,
            title="Selecionar Arquivo JSON",
            filetypes=[("Arquivos JSON", "*.json"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        try:
            proj = model_importer.import_from_json(path)
            self._apply_project(proj, os.path.basename(path))
        except Exception as ex:
            messagebox.showerror("Erro na Importação", f"Falha ao ler arquivo JSON:\n{ex}", parent=self)

    def _import_sql(self):
        import model_importer
        path = filedialog.askopenfilename(
            parent=self,
            title="Selecionar Script SQL DDL",
            filetypes=[("Scripts SQL", "*.sql"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            proj = model_importer.import_from_sql_ddl(content)
            self._apply_project(proj, os.path.basename(path))
        except Exception as ex:
            messagebox.showerror("Erro na Importação", f"Falha ao processar script SQL:\n{ex}", parent=self)

    def _load_sample_star(self):
        import model_importer
        sample_path = r"C:\Users\eder.souza\Downloads\DEXPARA_Transacional_DW.xlsx"
        if os.path.exists(sample_path):
            try:
                proj = model_importer.import_from_dexpara_excel(sample_path)
                self._apply_project(proj, "Exemplo Estrela SEFAZ DTE / Correios (DEXPARA_Transacional_DW.xlsx)")
                return
            except Exception:
                pass
        # Fallback para JSON navathe
        json_path = r"C:\Users\eder.souza\Downloads\03-diagrama-estrela-navathe.json"
        if os.path.exists(json_path):
            proj = model_importer.import_from_json(json_path)
            self._apply_project(proj, "Exemplo Estrela Navathe (03-diagrama-estrela-navathe.json)")
        else:
            messagebox.showinfo("Exemplo", "Arquivo de exemplo não encontrado nos Downloads.", parent=self)

