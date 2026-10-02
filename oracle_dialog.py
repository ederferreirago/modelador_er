"""Janela 'Oracle': conexão persistente, explorador do dicionário de dados, verificação e criação de tabelas."""
import csv
import datetime
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import oracle_tools as O

STATE_LABEL = {"NOVA": "Nova (não existe)", "NO_DESTINO": "Já existe no destino",
               "EM_OUTRO_SCHEMA": "Existe em outro schema"}
STATE_SHORT = {"NOVA": "Novas", "NO_DESTINO": "No destino", "EM_OUTRO_SCHEMA": "Em outro schema"}
ACTION_LABEL = {"criar": "Criar no destino", "acessar": "Conceder acesso + sinônimo", "ignorar": "Ignorar"}
CTYPE = {"P": "PK", "U": "UNIQUE", "R": "FK", "C": "CHECK"}
FILTERS = ["Todas", "Novas", "Já existem no destino", "Existem em outro schema", "Com diferenças"]


def _fmt_num(n):
    return "—" if n is None else f"{int(n):,}".replace(",", ".")


def _fmt_date(d):
    try:
        return d.strftime("%d/%m/%Y %H:%M") if d else "—"
    except Exception:
        return str(d) if d else "—"


def make_tree(parent, cols, height=8, anchors=None):
    """Treeview com barras de rolagem. cols = [(id, título, largura)]."""
    box = ttk.Frame(parent)
    tree = ttk.Treeview(box, columns=[c[0] for c in cols], show="headings", height=height)
    for cid, title, width in cols:
        tree.heading(cid, text=title)
        tree.column(cid, width=width, anchor=(anchors or {}).get(cid, "w"), stretch=True)
    ys = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
    xs = ttk.Scrollbar(box, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
    ys.pack(side="right", fill="y")
    xs.pack(side="bottom", fill="x")
    tree.pack(side="left", fill="both", expand=True)
    return box, tree


class OracleDialog(tk.Toplevel):
    def __init__(self, app, get_statements, default_kind="transacional"):
        super().__init__(app)
        self.title("Oracle — dicionário de dados e criação de tabelas")
        self.geometry("1220x800")
        self.minsize(1000, 640)
        self.transient(app)
        self.app = app
        self.sess = app.oracle                          # conexão que sobrevive ao fechamento desta janela
        self.get_statements = get_statements            # callable(kind) -> lista de comandos SQL (dialeto Oracle)
        self.statuses, self.expected, self.meta, self.statements = [], {}, {}, []
        self.grants, self.synonyms = {}, {}
        self.owner_idx = {}
        self.steps, self.notes = [], []
        self.busy = False
        self.ex_tables, self.ex_current = [], None
        cfg = self.sess.cfg if self.sess.cfg.user else O.load_profile()

        self.user_var = tk.StringVar(value=cfg.user)
        self.pwd_var = tk.StringVar()
        self.dsn_var = tk.StringVar(value=cfg.dsn)
        self.tns_var = tk.StringVar(value=cfg.tns_dir)
        self.kind_var = tk.StringVar(value=default_kind)
        self.target_var = tk.StringVar()
        self.mkschema_var = tk.BooleanVar(value=False)
        self.newpwd_var = tk.StringVar()
        self.ts_var = tk.StringVar(value="USERS")
        self.sync_var = tk.BooleanVar(value=False)
        self.stop_var = tk.BooleanVar(value=True)

        st = ttk.Style(self)
        st.configure("Treeview", rowheight=22, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        st.configure("Card.TLabel", font=("Segoe UI", 9, "bold"), padding=(10, 6), background="#FFFFFF")

        self._build_header()
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(2, 0))
        self.action_buttons = []
        self._build_conn_tab()
        self._build_explorer_tab()
        self._build_verify_tab()
        self._build_plan_tab()
        self.status = ttk.Label(self, text="", anchor="w", padding=(10, 4))
        self.status.pack(fill="x")
        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh_header()
        if self.sess.connected:
            self._after_connect(initial=True)

    # ------------------------------------------------------------ ciclo de vida
    def destroy(self):
        """Fecha só a janela: a conexão continua aberta na sessão do aplicativo."""
        self.pwd_var.set("")
        self.newpwd_var.set("")
        if getattr(self.app, "_oracle_dlg", None) is self:
            self.app._oracle_dlg = None
        super().destroy()

    def _set_busy(self, busy, msg=""):
        self.busy = busy
        self.config(cursor="watch" if busy else "")
        for b in self.action_buttons:
            b.state(["disabled"] if busy else ["!disabled"])
        if msg:
            self.status.config(text=msg)

    def _bg(self, fn, done, msg, on_error=None):
        """Executa `fn` numa thread (interface não trava) e chama `done(resultado)` na thread da UI."""
        if self.busy:
            self.status.config(text="Aguarde: há uma operação em andamento…")
            return
        self._set_busy(True, msg)
        q = queue.Queue()

        def work():
            try:
                q.put(("ok", fn()))
            except Exception as ex:      # noqa: BLE001
                q.put(("err", ex))
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not self.winfo_exists():
                return
            try:
                kind, val = q.get_nowait()
            except queue.Empty:
                self.after(80, poll)
                return
            self._set_busy(False)
            if kind == "err":
                self.status.config(text=f"Erro: {val}")
                if on_error:
                    on_error(val)
                else:
                    messagebox.showerror("Oracle", str(val), parent=self)
            else:
                done(val)
        self.after(80, poll)

    def _cfg(self):
        return O.ConnConfig(user=self.user_var.get().strip(), dsn=self.dsn_var.get().strip(),
                            tns_dir=self.tns_var.get().strip())

    def _connected_user(self):
        return (self.sess.info.get("user") or self.user_var.get()).upper()

    def _need_conn(self):
        if not self.sess.check():
            self._refresh_header()
            messagebox.showinfo("Oracle", "Sem conexão ativa (ela pode ter caído). Conecte-se na aba 1; "
                                          "a senha é pedida de novo por segurança.", parent=self)
            self.nb.select(0)
            return False
        return True

    # ------------------------------------------------------------ faixa de status (sempre visível)
    def _build_header(self):
        bar = tk.Frame(self, bg="#EEF1FA")
        bar.pack(fill="x", padx=8, pady=(8, 4))
        self.hdr_dot = tk.Label(bar, text="●", bg="#EEF1FA", fg="#9CA3AF", font=("Segoe UI", 12))
        self.hdr_dot.pack(side="left", padx=(10, 4), pady=6)
        self.hdr_lbl = tk.Label(bar, text="", bg="#EEF1FA", fg="#25304A", font=("Segoe UI", 9, "bold"), anchor="w")
        self.hdr_lbl.pack(side="left")
        self.hdr_sub = tk.Label(bar, text="", bg="#EEF1FA", fg="#73809B", font=("Segoe UI", 8))
        self.hdr_sub.pack(side="left", padx=12)
        self.btn_disc = ttk.Button(bar, text="⏏ Desconectar", command=self.disconnect)
        self.btn_disc.pack(side="right", padx=8, pady=4)

    def _refresh_header(self):
        s = self.sess
        if s.connected:
            scope = "DBA_*" if s.views == "dba" else "ALL_*"
            self.hdr_dot.config(fg="#16A34A")
            self.hdr_lbl.config(text=f"Conectado como {s.info.get('user', '')}")
            self.hdr_sub.config(text=f"{s.cfg.dsn}  ·  container {s.info.get('container') or '—'}  ·  "
                                     f"{len(s.schemas)} schemas  ·  dicionário {scope}")
            self.btn_disc.state(["!disabled"])
        else:
            self.hdr_dot.config(fg="#9CA3AF")
            self.hdr_lbl.config(text="Desconectado")
            self.hdr_sub.config(text="Informe usuário, senha e DSN na aba 1")
            self.btn_disc.state(["disabled"])
        try:
            self.app._refresh_oracle_status()
        except Exception:
            pass

    # ------------------------------------------------------------ aba 1: conexão
    def _build_conn_tab(self):
        f = ttk.Frame(self.nb, padding=14)
        self.nb.add(f, text="1. Conexão")
        box = ttk.LabelFrame(f, text="Banco de dados Oracle", padding=10)
        box.pack(fill="x")
        rows = [("Usuário:", self.user_var, None), ("Senha:", self.pwd_var, "●"),
                ("DSN (host:porta/serviço):", self.dsn_var, None), ("Pasta TNS/Wallet (opcional):", self.tns_var, None)]
        for i, (lbl, var, show) in enumerate(rows):
            ttk.Label(box, text=lbl).grid(row=i, column=0, sticky="w", pady=3)
            e = ttk.Entry(box, textvariable=var, width=46, show=show or "")
            e.grid(row=i, column=1, sticky="w", padx=8)
            if lbl == "Senha:":
                e.bind("<Return>", lambda _e: self.connect())
        ttk.Label(box, text="Ex.: localhost:1521/FREEPDB1  ·  a senha nunca é gravada em disco; a conexão fica "
                            "aberta até você desconectar ou fechar o aplicativo", foreground="#73809B").grid(
            row=4, column=1, sticky="w", padx=8)
        bar = ttk.Frame(box)
        bar.grid(row=5, column=1, sticky="w", padx=8, pady=(8, 0))
        self.btn_test = ttk.Button(bar, text="⚡ Testar Conexão", command=self.test_connection)
        self.btn_test.pack(side="left")
        self.btn_connect = ttk.Button(bar, text="🔌 Conectar", command=self.connect)
        self.btn_connect.pack(side="left", padx=6)
        ttk.Button(bar, text="💾 Salvar perfil (sem senha)", command=self.save_profile).pack(side="left", padx=6)

        dest = ttk.LabelFrame(f, text="O que criar e onde", padding=10)
        dest.pack(fill="x", pady=10)
        ttk.Label(dest, text="Modelo:").grid(row=0, column=0, sticky="w")
        kb = ttk.Frame(dest)
        kb.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Radiobutton(kb, text="Transacional (relacional)", variable=self.kind_var, value="transacional").pack(side="left")
        ttk.Radiobutton(kb, text="Dimensional (Kimball)", variable=self.kind_var, value="dimensional").pack(side="left", padx=12)
        ttk.Label(dest, text="Schema de destino:").grid(row=1, column=0, sticky="w", pady=6)
        self.target_cb = ttk.Combobox(dest, textvariable=self.target_var, width=30)
        self.target_cb.grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(dest, text="(escolha ou digite; as tabelas do modelo serão criadas nele)", foreground="#73809B").grid(
            row=1, column=2, sticky="w")
        ttk.Checkbutton(dest, text="Criar o schema se não existir (exige privilégio CREATE USER)",
                        variable=self.mkschema_var, command=self._toggle_mkschema).grid(row=2, column=1, columnspan=2, sticky="w", padx=8)
        self.mk_frame = ttk.Frame(dest)
        self.mk_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=8)
        ttk.Label(self.mk_frame, text="Senha do novo schema:").pack(side="left")
        self.newpwd_entry = ttk.Entry(self.mk_frame, textvariable=self.newpwd_var, show="●", width=22)
        self.newpwd_entry.pack(side="left", padx=6)
        ttk.Label(self.mk_frame, text="Tablespace:").pack(side="left")
        self.ts_cb = ttk.Combobox(self.mk_frame, textvariable=self.ts_var, width=14, values=["USERS"])
        self.ts_cb.pack(side="left", padx=6)
        self._toggle_mkschema()
        ttk.Label(f, text="Nada é apagado nem sobrescrito: tabelas que já existem são apenas comparadas.",
                  foreground="#73809B").pack(anchor="w", pady=(4, 0))
        self.action_buttons.extend([self.btn_test, self.btn_connect])

    def _toggle_mkschema(self):
        st = "normal" if self.mkschema_var.get() else "disabled"
        for w in (self.newpwd_entry, self.ts_cb):
            w.config(state=st)

    def save_profile(self):
        O.save_profile(self._cfg())
        self.status.config(text=f"Perfil salvo em {O.profile_path()} (sem a senha)")

    def test_connection(self):
        cfg, pwd = self._cfg(), self.pwd_var.get()
        if not cfg.user or not cfg.dsn:
            messagebox.showwarning("Oracle", "Informe usuário e DSN para testar a conexão.", parent=self)
            return

        def work():
            c = O.connect(cfg, pwd)
            try:
                info = O.session_info(c)
                banner = ""
                cur = c.cursor()
                try:
                    cur.execute("SELECT banner FROM v$version WHERE ROWNUM = 1")
                    row = cur.fetchone()
                    if row:
                        banner = str(row[0])
                except Exception:
                    pass
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
                return info, banner
            finally:
                try:
                    c.close()
                except Exception:
                    pass

        def done(res):
            info, banner = res
            cont = f" (Container: {info.get('container')})" if info.get("container") else ""
            msg = f"Conexão com o Oracle testada com SUCESSO!\n\n• Usuário: {info.get('user', cfg.user)}\n• Servidor: {banner or 'Oracle Database'}{cont}\n• DSN: {cfg.dsn}"
            messagebox.showinfo("Oracle - Teste de Conexão", msg, parent=self)
            self.status.config(text="Teste de conexão bem-sucedido.")

        self._bg(work, done, "Testando conexão com o Oracle…")

    def connect(self):
        cfg, pwd = self._cfg(), self.pwd_var.get()
        sess = self.sess

        def done(_):
            self.pwd_var.set("")
            self._after_connect()
        self._bg(lambda: sess.open(cfg, pwd), done, "Conectando…")

    def disconnect(self):
        if self.sess.connected and messagebox.askyesno("Desconectar", "Encerrar a conexão com o Oracle?", parent=self):
            self.sess.close()
            self.statuses, self.steps = [], []
            self._refresh_header()
            self.status.config(text="Desconectado.")

    def _after_connect(self, initial=False):
        s = self.sess
        self.target_cb.config(values=s.schemas)
        if s.tablespaces:
            self.ts_cb.config(values=s.tablespaces)
        if not self.target_var.get():
            self.target_var.set(s.info.get("current_schema", ""))
        self.ex_schema_cb.config(values=s.schemas)
        if not self.ex_schema.get():
            self.ex_schema.set(self.target_var.get() or (s.schemas[0] if s.schemas else ""))
        self._refresh_header()
        self.status.config(text="Conexão ativa. Explore o dicionário (aba 2) ou verifique o modelo (aba 3).")
        if self.ex_schema.get():
            self.ex_load_tables()

    # ------------------------------------------------------------ aba 2: explorador do dicionário
    def _build_explorer_tab(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="2. Dicionário de dados")
        paned = ttk.PanedWindow(f, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left, right = ttk.Frame(paned, padding=(0, 0, 6, 0)), ttk.Frame(paned)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="Schema:").pack(side="left")
        self.ex_schema = tk.StringVar()
        self.ex_schema_cb = ttk.Combobox(top, textvariable=self.ex_schema, width=24, state="readonly")
        self.ex_schema_cb.pack(side="left", padx=6)
        self.ex_schema_cb.bind("<<ComboboxSelected>>", lambda _e: self.ex_load_tables())
        ttk.Button(top, text="⟳", width=3, command=self.ex_load_tables).pack(side="left")
        self.ex_counts = ttk.Label(left, text="", foreground="#73809B", wraplength=300, justify="left")
        self.ex_counts.pack(anchor="w", pady=(4, 2))
        srow = ttk.Frame(left)
        srow.pack(fill="x", pady=2)
        ttk.Label(srow, text="🔎").pack(side="left")
        self.ex_search = tk.StringVar()
        ttk.Entry(srow, textvariable=self.ex_search).pack(side="left", fill="x", expand=True, padx=4)
        self.ex_search.trace_add("write", lambda *_: self._ex_fill())
        box, self.ex_tree = make_tree(left, [("name", "Tabela", 190), ("rows", "Linhas", 70), ("comment", "Comentário", 160)],
                                      height=18, anchors={"rows": "e"})
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.ex_tree.bind("<<TreeviewSelect>>", self._ex_select)

        act_bar = ttk.Frame(left)
        act_bar.pack(fill="x", pady=(6, 0))
        self.btn_import_concept = ttk.Button(act_bar, text="📥 Importar para Modelo", command=self.ex_import_conceptual)
        self.btn_import_concept.pack(side="left", fill="x", expand=True)
        self.btn_export_dict = ttk.Button(act_bar, text="📊 Exportar CSV", command=self.ex_export_dictionary)
        self.btn_export_dict.pack(side="left", padx=(4, 0))
        self.action_buttons.extend([self.btn_import_concept, self.btn_export_dict])

        self.ex_head = ttk.Label(right, text="Selecione uma tabela à esquerda.", font=("Segoe UI", 10, "bold"),
                                 wraplength=760, justify="left")
        self.ex_head.pack(anchor="w")
        self.ex_sub = ttk.Label(right, text="", foreground="#73809B", wraplength=760, justify="left")
        self.ex_sub.pack(anchor="w", pady=(2, 6))
        self.ex_nb = ttk.Notebook(right)
        self.ex_nb.pack(fill="both", expand=True)
        tabs = {
            "cols": ("Colunas", [("n", "#", 36), ("name", "Coluna", 180), ("type", "Tipo", 130), ("null", "Nulo?", 60),
                                 ("key", "Chave", 70), ("comment", "Comentário", 360)]),
            "cons": ("Constraints", [("type", "Tipo", 80), ("name", "Nome", 220), ("cols", "Colunas", 200), ("ref", "Referencia", 170),
                                     ("rule", "ON DELETE", 90), ("status", "Status", 80), ("cond", "Condição", 240)]),
            "idx": ("Índices", [("name", "Nome", 240), ("type", "Tipo", 120), ("uniq", "Único", 70), ("cols", "Colunas", 300),
                                ("status", "Status", 90)]),
            "priv": ("Privilégios", [("grantee", "Concedido a", 200), ("priv", "Privilégio", 160), ("grant", "Repassável", 90)]),
            "refs": ("Referenciada por", [("table", "Tabela filha", 260), ("cons", "Constraint", 260), ("rule", "ON DELETE", 120)]),
        }
        self.ex_trees = {}
        for key, (title, cols) in tabs.items():
            fr = ttk.Frame(self.ex_nb, padding=4)
            self.ex_nb.add(fr, text=title)
            box, tree = make_tree(fr, cols, height=12)
            box.pack(fill="both", expand=True)
            self.ex_trees[key] = tree
        self.ex_trees["cols"].tag_configure("pk", foreground="#1d4ed8")
        self.ex_trees["cols"].tag_configure("fk", foreground="#7c3aed")
        # DDL
        fr = ttk.Frame(self.ex_nb, padding=4)
        self.ex_nb.add(fr, text="DDL")
        bar = ttk.Frame(fr)
        bar.pack(fill="x")
        self.btn_ddl = ttk.Button(bar, text="Carregar DDL (DBMS_METADATA)", command=self.ex_load_ddl)
        self.btn_ddl.pack(side="left")
        ttk.Button(bar, text="📋 Copiar", command=lambda: self._copy_text(self.ex_ddl_txt)).pack(side="left", padx=6)
        self.ex_ddl_txt = tk.Text(fr, font=("Consolas", 9), wrap="none", height=12, bg="#F8F9FE")
        self.ex_ddl_txt.pack(fill="both", expand=True, pady=4)
        # Amostra de dados
        fr = ttk.Frame(self.ex_nb, padding=4)
        self.ex_nb.add(fr, text="Amostra de dados")
        bar = ttk.Frame(fr)
        bar.pack(fill="x")
        self.sample_n = tk.StringVar(value="50")
        ttk.Label(bar, text="Linhas:").pack(side="left")
        ttk.Combobox(bar, textvariable=self.sample_n, values=["20", "50", "100", "200"], width=6, state="readonly").pack(side="left", padx=4)
        self.btn_sample = ttk.Button(bar, text="Carregar amostra (somente leitura)", command=self.ex_load_sample)
        self.btn_sample.pack(side="left", padx=6)
        self.sample_holder = ttk.Frame(fr)
        self.sample_holder.pack(fill="both", expand=True, pady=4)
        self.action_buttons += [self.btn_ddl, self.btn_sample]

    def _copy_text(self, widget):
        self.clipboard_clear()
        self.clipboard_append(widget.get("1.0", "end").strip())
        self.status.config(text="Copiado para a área de transferência.")

    def ex_load_tables(self):
        if not self._need_conn():
            return
        owner, conn, views = self.ex_schema.get(), self.sess.conn, self.sess.views
        if not owner:
            return

        def work():
            return O.list_tables(conn, owner, views), O.count_objects(conn, owner, views)

        def done(r):
            self.ex_tables, counts = r
            self.ex_counts.config(text=f"{owner}: " + (", ".join(f"{n} {t.lower()}" for t, n in counts.items()) or "sem objetos"))
            self._ex_fill()
            self.status.config(text=f"{len(self.ex_tables)} tabelas em {owner}.")
        self._bg(work, done, f"Lendo tabelas de {owner}…")

    def _ex_fill(self):
        q = self.ex_search.get().strip().upper()
        self.ex_tree.delete(*self.ex_tree.get_children())
        for t in self.ex_tables:
            if q and q not in t["name"] and q not in t["comment"].upper():
                continue
            self.ex_tree.insert("", "end", iid=t["name"], values=(t["name"], _fmt_num(t["rows"]), t["comment"]))

    def _ex_select(self, _e=None):
        sel = self.ex_tree.selection()
        if not sel or not self._need_conn():
            return
        owner, table, conn, views = self.ex_schema.get(), sel[0], self.sess.conn, self.sess.views
        self.ex_current = (owner, table)
        self._ex_clear_extra()
        self._bg(lambda: O.table_details(conn, owner, table, views), lambda d: self._ex_show(d),
                 f"Lendo metadados de {owner}.{table}…")

    def _ex_clear_extra(self):
        self.ex_ddl_txt.delete("1.0", "end")
        for w in self.sample_holder.winfo_children():
            w.destroy()

    def _ex_show(self, d):
        if self.ex_current != (d["owner"], d["table"]):
            return
        sm = d["summary"]
        flags = [x for x, on in (("particionada", sm.get("partitioned")), ("temporária", sm.get("temporary"))) if on]
        self.ex_head.config(text=f"{d['owner']}.{d['table']}" + (f"   —   {sm['comment']}" if sm.get("comment") else ""))
        self.ex_sub.config(text=f"{_fmt_num(sm.get('rows'))} linhas (estatística)  ·  analisada em {_fmt_date(sm.get('analyzed'))}  ·  "
                                f"tablespace {sm.get('tablespace') or '—'}  ·  {len(d["columns"])} coluna(s)"
                                + (f"  ·  {', '.join(flags)}" if flags else ""))
        fk_cols = {c for con in d["constraints"] if con["type"] == "R" for c in con["columns"]}
        t = self.ex_trees["cols"]
        t.delete(*t.get_children())
        for i, c in enumerate(d["columns"], 1):
            key = ("🔑 PK " if c["name"] in d["pk"] else "") + ("🔗 FK" if c["name"] in fk_cols else "")
            tag = ("pk",) if c["name"] in d["pk"] else (("fk",) if c["name"] in fk_cols else ())
            t.insert("", "end", values=(i, c["name"], c["type"] + (" · identity" if c["identity"] else ""),
                                        "sim" if c["nullable"] else "NÃO", key.strip(), c["comment"]), tags=tag)
        t = self.ex_trees["cons"]
        t.delete(*t.get_children())
        for c in d["constraints"]:
            kind = CTYPE.get(c["type"], c["type"])
            cond = " ".join(c["condition"].split())
            if c["type"] == "C" and cond.upper().endswith("IS NOT NULL"):
                kind = "NOT NULL"
            t.insert("", "end", values=(kind, c["name"], ", ".join(c["columns"]),
                                        c["ref_table"] if c["type"] == "R" else "", c["delete_rule"], c["status"],
                                        cond if c["type"] == "C" else ""))
        t = self.ex_trees["idx"]
        t.delete(*t.get_children())
        for i in d["indexes"]:
            t.insert("", "end", values=(i["name"], i["type"], "sim" if i["unique"] else "não", ", ".join(i["columns"]), i["status"]))
        t = self.ex_trees["priv"]
        t.delete(*t.get_children())
        for p in d["privileges"]:
            t.insert("", "end", values=(p["grantee"], p["privilege"], "sim" if p["grantable"] else "não"))
        t = self.ex_trees["refs"]
        t.delete(*t.get_children())
        for c in d["children"]:
            t.insert("", "end", values=(c["table"], c["constraint"], c["delete_rule"]))
        titles = {"cons": len(d["constraints"]), "idx": len(d["indexes"]), "priv": len(d["privileges"]), "refs": len(d["children"])}
        names = {"cons": "Constraints", "idx": "Índices", "priv": "Privilégios", "refs": "Referenciada por"}
        for i, key in enumerate(["cols", "cons", "idx", "priv", "refs"]):
            n = len(d["columns"]) if key == "cols" else titles[key]
            self.ex_nb.tab(i, text=f"{'Colunas' if key == 'cols' else names[key]} ({n})")
        self.status.config(text=f"Metadados de {d['owner']}.{d['table']} carregados.")

    def ex_load_ddl(self):
        if not self.ex_current or not self._need_conn():
            return
        owner, table, conn = self.ex_current[0], self.ex_current[1], self.sess.conn

        def done(txt):
            self.ex_ddl_txt.delete("1.0", "end")
            self.ex_ddl_txt.insert("1.0", txt.strip())
        self._bg(lambda: O.get_ddl(conn, owner, table), done, "Lendo o DDL…",
                 on_error=lambda ex: (self.ex_ddl_txt.delete("1.0", "end"),
                                      self.ex_ddl_txt.insert("1.0", f"Não foi possível obter o DDL: {ex}\n\n"
                                                                    f"(DBMS_METADATA exige EXECUTE e acesso à tabela.)")))

    def ex_load_sample(self):
        if not self.ex_current or not self._need_conn():
            return
        owner, table, conn = self.ex_current[0], self.ex_current[1], self.sess.conn
        n = int(self.sample_n.get())

        def done(r):
            cols, rows = r
            for w in self.sample_holder.winfo_children():
                w.destroy()
            box, tree = make_tree(self.sample_holder, [(f"c{i}", c, 120) for i, c in enumerate(cols)], height=12)
            box.pack(fill="both", expand=True)
            for row in rows:
                tree.insert("", "end", values=["" if v is None else str(v)[:200] for v in row])
            self.status.config(text=f"{len(rows)} linhas de {owner}.{table} (limite {n}).")
        self._bg(lambda: O.sample_rows(conn, owner, table, n), done, "Lendo amostra…")

    def ex_import_conceptual(self):
        if not self._need_conn():
            return
        owner = self.ex_schema.get()
        if not owner:
            messagebox.showinfo("Oracle", "Selecione um schema primeiro.", parent=self)
            return
        sel = self.ex_tree.selection()
        if not sel:
            if not self.ex_tables:
                messagebox.showinfo("Importar", f"Nenhuma tabela encontrada no schema {owner}.", parent=self)
                return
            if not messagebox.askyesno("Importar Tabelas",
                                       f"Nenhuma tabela específica foi selecionada.\n\n"
                                       f"Deseja importar todas as {len(self.ex_tables)} tabelas do schema '{owner}' "
                                       f"para o modelo conceitual?", parent=self):
                return
            table_names = [t["name"] for t in self.ex_tables]
        else:
            table_names = list(sel)

        import model_importer

        def work():
            return model_importer.import_from_oracle_tables(self.sess, owner, table_names)

        def done(new_project):
            self.app.project = new_project
            self.app.history.reset()
            self.app._rebuild_sidebar()
            self.app.render()
            msg = (f"Modelo conceitual importado com sucesso a partir do Oracle!\n\n"
                   f"• {len(new_project.entities)} Entidades\n"
                   f"• {len(new_project.rels)} Relacionamentos\n\n"
                   f"O diagrama foi gerado com layout automático no editor principal.")
            messagebox.showinfo("Importar Modelo Conceitual", msg, parent=self)
            self.status.config(text=f"Modelo conceitual importado ({len(new_project.entities)} entidades).")
            self.destroy()

        self._bg(work, done, f"Importando {len(table_names)} tabelas de {owner} para o modelo conceitual…")

    def ex_export_dictionary(self):
        if not self._need_conn():
            return
        if not self.ex_tables:
            messagebox.showinfo("Exportar", "Nenhuma tabela carregada para exportar.", parent=self)
            return
        import csv
        from tkinter import filedialog
        owner = self.ex_schema.get() or "ORACLE"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Exportar Dicionário de Dados",
            defaultextension=".csv",
            initialfile=f"dicionario_{owner.lower()}.csv",
            filetypes=[("CSV (Separado por ponto e vírgula)", "*.csv"), ("Todos os arquivos", "*.*")]
        )
        if not path:
            return

        def work():
            rows = [["SCHEMA", "TABELA", "COLUNA", "TIPO", "NULO", "PK", "FK", "COMENTARIO_COLUNA", "COMENTARIO_TABELA"]]
            conn, views = self.sess.conn, self.sess.views
            meta = O.read_table_metadata(conn, owner, [t["name"] for t in self.ex_tables], views)
            for t in self.ex_tables:
                tname = t["name"]
                tmeta = meta.get(tname, {})
                pk_set = set(tmeta.get("pk", []))
                for c in tmeta.get("columns", []):
                    cname = c.get("name", "")
                    rows.append([
                        owner,
                        tname,
                        cname,
                        c.get("type", ""),
                        "SIM" if c.get("nullable") else "NÃO",
                        "SIM" if cname in pk_set else "NÃO",
                        "",
                        c.get("comment", ""),
                        t.get("comment", ""),
                    ])
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerows(rows)
            return len(rows) - 1

        def done(count):
            messagebox.showinfo("Exportar Dicionário", f"Dicionário exportado com sucesso!\n\n{count} colunas salvas em:\n{path}", parent=self)
            self.status.config(text=f"Dicionário exportado para {path}")

        self._bg(work, done, "Exportando dicionário de dados…")

    # ------------------------------------------------------------ aba 3: verificação
    def _build_verify_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="3. Verificação do modelo")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.btn_analyze = ttk.Button(bar, text="🔎 Ler dicionário e comparar", command=self.analyze)
        self.btn_analyze.pack(side="left")
        ttk.Label(bar, text="  Filtro:").pack(side="left")
        self.filter_var = tk.StringVar(value=FILTERS[0])
        cb = ttk.Combobox(bar, textvariable=self.filter_var, values=FILTERS, state="readonly", width=24)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self._fill_verify())
        ttk.Label(bar, text="🔎").pack(side="left", padx=(8, 0))
        self.vsearch = tk.StringVar()
        ttk.Entry(bar, textvariable=self.vsearch, width=20).pack(side="left", padx=4)
        self.vsearch.trace_add("write", lambda *_: self._fill_verify())
        ttk.Button(bar, text="📄 Exportar CSV…", command=self.export_csv).pack(side="right")
        ttk.Button(bar, text="🔎 Ver no dicionário", command=self.goto_explorer).pack(side="right", padx=6)

        cards = tk.Frame(f, bg="#F4F6FC")
        cards.pack(fill="x", pady=(8, 4))
        self.cards = {}
        for key, label, color in (("total", "Tabelas do modelo", "#25304A"), ("NOVA", "Novas", "#15803d"),
                                  ("NO_DESTINO", "Já no destino", "#1d4ed8"), ("EM_OUTRO_SCHEMA", "Em outro schema", "#b45309"),
                                  ("diff", "Com diferenças", "#b91c1c")):
            c = tk.Frame(cards, bg="#FFFFFF", highlightbackground="#DCE3F1", highlightthickness=1)
            c.pack(side="left", padx=(0, 8), ipadx=10, ipady=2)
            n = tk.Label(c, text="—", bg="#FFFFFF", fg=color, font=("Segoe UI", 16, "bold"))
            n.pack()
            tk.Label(c, text=label, bg="#FFFFFF", fg="#73809B", font=("Segoe UI", 8)).pack()
            self.cards[key] = n

        cols = [("table", "Tabela do modelo", 230), ("state", "Situação", 190), ("where", "Onde existe", 120),
                ("diff", "Diferenças (colunas)", 290), ("action", "Ação (duplo-clique alterna)", 210)]
        box, self.vtree = make_tree(f, cols, height=9)
        box.pack(fill="x", pady=6)
        for tag, bg in (("NOVA", "#ECFDF5"), ("NO_DESTINO", "#EFF6FF"), ("EM_OUTRO_SCHEMA", "#FFF7ED"), ("CONFLITO", "#FEE2E2")):
            self.vtree.tag_configure(tag, background=bg)
        self.vtree.bind("<<TreeviewSelect>>", self._show_detail)
        self.vtree.bind("<Double-1>", self._vtree_dblclick)
        self.detail_lbl = ttk.Label(f, text="Selecione uma tabela para ver a comparação coluna a coluna.",
                                    foreground="#73809B", wraplength=1100, justify="left")
        self.detail_lbl.pack(anchor="w")
        box, self.dtree = make_tree(f, [("col", "Coluna", 200), ("model", "Tipo no modelo", 140), ("oracle", "Tipo no Oracle", 140),
                                        ("status", "Situação", 150), ("comment", "Comentário no dicionário", 380)], height=10)
        box.pack(fill="both", expand=True, pady=(4, 0))
        self.dtree.tag_configure("falta", foreground="#b91c1c")
        self.dtree.tag_configure("extra", foreground="#b45309")
        self.dtree.tag_configure("tipo", foreground="#7c3aed")
        self.action_buttons.append(self.btn_analyze)

    def _target(self):
        try:
            return O.check_ident(self.target_var.get().strip(), "schema de destino")
        except O.OracleError as ex:
            messagebox.showerror("Schema de destino", str(ex), parent=self)
            self.nb.select(0)
            return None

    def analyze(self):
        if not self._need_conn():
            return
        target = self._target()
        if not target:
            return
        try:
            self.statements = O.split_statements("\n".join(self.get_statements(self.kind_var.get())))
        except Exception as ex:
            messagebox.showerror("DDL", str(ex), parent=self)
            return
        if not O.expected_tables(self.statements):
            messagebox.showinfo("Oracle", "O modelo não gerou nenhuma tabela (no dimensional, marque entidades como "
                                          "Dimensão/Fato).", parent=self)
            return
        conn, stmts, views = self.sess.conn, self.statements, self.sess.views

        def work():
            statuses, expected, meta = O.analyze(conn, stmts, target, views)
            names = [s.name for s in statuses]
            refs = [(o, s.name) for s in statuses for o in s.owners]
            return {"statuses": statuses, "expected": expected, "meta": meta,
                    "grants": O.existing_grants(conn, target, refs, views) if refs else {},
                    "synonyms": O.existing_synonyms(conn, target, names, views)}

        def done(r):
            self.statuses, self.expected, self.meta = r["statuses"], r["expected"], r["meta"]
            self.grants, self.synonyms = r["grants"], r["synonyms"]
            self.owner_idx = {s.name: 0 for s in self.statuses}
            self._fill_verify()
            n = {k: sum(1 for s in self.statuses if s.state == k) for k in STATE_LABEL}
            self.status.config(text=f"{len(self.statuses)} tabelas do modelo: {n['NOVA']} novas, {n['NO_DESTINO']} já no "
                                    f"destino, {n['EM_OUTRO_SCHEMA']} em outro schema.")
            self.nb.select(2)
        self._bg(work, done, "Lendo o dicionário de dados…")

    def _has_diff(self, s):
        d = s.diff or {}
        return bool(d.get("missing") or d.get("type_diff") or d.get("extra"))

    def _fill_verify(self):
        for key, lbl in self.cards.items():
            if key == "total":
                lbl.config(text=str(len(self.statuses)))
            elif key == "diff":
                lbl.config(text=str(sum(1 for s in self.statuses if s.state != "NOVA" and self._has_diff(s))))
            else:
                lbl.config(text=str(sum(1 for s in self.statuses if s.state == key)))
        flt, q = self.filter_var.get(), self.vsearch.get().strip().upper()
        self.vtree.delete(*self.vtree.get_children())
        for s in self.statuses:
            if q and q not in s.name:
                continue
            if flt == "Novas" and s.state != "NOVA":
                continue
            if flt == "Já existem no destino" and s.state != "NO_DESTINO":
                continue
            if flt == "Existem em outro schema" and s.state != "EM_OUTRO_SCHEMA":
                continue
            if flt == "Com diferenças" and not (s.state != "NOVA" and self._has_diff(s)):
                continue
            tag = "CONFLITO" if s.conflict else s.state
            diff = ""
            if s.state != "NOVA":
                d, bits = s.diff, []
                if d.get("missing"):
                    bits.append(f"faltam {len(d['missing'])}")
                if d.get("type_diff"):
                    bits.append(f"tipo diferente {len(d['type_diff'])}")
                if d.get("extra"):
                    bits.append(f"a mais {len(d['extra'])}")
                diff = ", ".join(bits) or "✔ colunas iguais ao modelo"
            if s.conflict:
                diff = f"⚠ já existe {s.conflict} com esse nome no destino"
            where = "—"
            if s.owners:
                where = s.owners[self.owner_idx.get(s.name, 0) % len(s.owners)] + (f" (+{len(s.owners) - 1})" if len(s.owners) > 1 else "")
            elif s.state == "NO_DESTINO":
                where = self.target_var.get().upper()
            self.vtree.insert("", "end", iid=s.name, tags=(tag,),
                              values=(s.name, STATE_LABEL[s.state], where, diff, ACTION_LABEL[s.action]))

    def _vtree_dblclick(self, event):
        item = self.vtree.identify_row(event.y)
        col = self.vtree.identify_column(event.x)
        s = next((x for x in self.statuses if x.name == item), None)
        if not s:
            return
        if col == "#5":
            options = {"NOVA": ["criar", "ignorar"], "EM_OUTRO_SCHEMA": ["acessar", "criar", "ignorar"],
                       "NO_DESTINO": ["ignorar"]}[s.state]
            if s.conflict:
                options = ["ignorar"]
            s.action = options[(options.index(s.action) + 1) % len(options)] if s.action in options else options[0]
        elif col == "#3" and len(s.owners) > 1:
            self.owner_idx[s.name] = (self.owner_idx.get(s.name, 0) + 1) % len(s.owners)
        self._fill_verify()
        if self.vtree.exists(item):
            self.vtree.selection_set(item)

    def _owner_of(self, s):
        if s.state == "NO_DESTINO":
            return self.target_var.get().upper()
        return s.owners[self.owner_idx.get(s.name, 0) % len(s.owners)] if s.owners else None

    def _show_detail(self, _e=None):
        sel = self.vtree.selection()
        self.dtree.delete(*self.dtree.get_children())
        if not sel:
            return
        s = next(x for x in self.statuses if x.name == sel[0])
        exp_cols = self.expected[s.name]["columns"]
        owner = self._owner_of(s)
        m = self.meta.get((owner, s.name)) if owner else None
        head = f"{s.name}"
        if m is not None:
            head += f"  ·  {owner}" + (f"  ·  comentário: {m['comment']}" if m["comment"] else "")
            if m["pk"]:
                head += f"  ·  PK: {', '.join(m['pk'])}"
            if m["fks"]:
                head += "  ·  FKs: " + "; ".join(f"{'/'.join(f['columns'])} → {f['ref_table']}" for f in m["fks"])
        self.detail_lbl.config(text=head)
        act = {c["name"]: c for c in (m["columns"] if m else [])}
        seen = set()
        for c in exp_cols:
            seen.add(c["name"])
            a = act.get(c["name"])
            if a is None:
                st, tag, ot, cm = ("não existe no Oracle" if m else "a criar"), ("falta" if m else ""), "", ""
            else:
                same = O._norm_type(c["type"]) == O._norm_type(a["type"])
                st, tag, ot, cm = ("igual" if same else "tipo diferente"), ("" if same else "tipo"), a["type"], a["comment"]
            self.dtree.insert("", "end", values=(c["name"], c["type"], ot, st, cm), tags=(tag,))
        for n, a in act.items():
            if n not in seen:
                self.dtree.insert("", "end", values=(n, "", a["type"], "só no Oracle", a["comment"]), tags=("extra",))

    def goto_explorer(self):
        sel = self.vtree.selection()
        if not sel:
            messagebox.showinfo("Dicionário", "Selecione uma tabela da lista.", parent=self)
            return
        s = next(x for x in self.statuses if x.name == sel[0])
        owner = self._owner_of(s)
        if not owner or s.state == "NOVA":
            messagebox.showinfo("Dicionário", "Essa tabela ainda não existe no Oracle.", parent=self)
            return
        self.ex_schema.set(owner)
        self.nb.select(1)
        self.ex_load_tables()
        self.after(600, lambda: self._ex_pick(s.name))

    def _ex_pick(self, name):
        if self.ex_tree.exists(name):
            self.ex_tree.selection_set(name)
            self.ex_tree.see(name)
        else:
            self.after(400, lambda: self._ex_pick(name) if self.ex_tree.exists(name) else None)

    def export_csv(self):
        if not self.statuses:
            messagebox.showinfo("Exportar", "Faça a verificação antes.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                            initialfile=f"comparacao_{self.target_var.get().lower()}.csv")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Tabela", "Situação", "Schema", "Ação", "Colunas faltando", "Tipo diferente", "Colunas a mais"])
            for s in self.statuses:
                d = s.diff or {}
                w.writerow([s.name, STATE_LABEL[s.state], self._owner_of(s) or "", ACTION_LABEL[s.action],
                            ", ".join(d.get("missing", [])), ", ".join(f"{n}: {a} ≠ {b}" for n, a, b in d.get("type_diff", [])),
                            ", ".join(d.get("extra", []))])
        self.status.config(text=f"Comparação exportada para {path}")

    # ------------------------------------------------------------ aba 4: plano e execução
    def _build_plan_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="4. Plano e execução")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.btn_plan = ttk.Button(bar, text="📋 Gerar plano", command=self.make_plan)
        self.btn_plan.pack(side="left")
        ttk.Checkbutton(bar, text="Adicionar colunas que faltam nas tabelas do destino", variable=self.sync_var).pack(side="left", padx=12)
        ttk.Checkbutton(bar, text="Parar no primeiro erro", variable=self.stop_var).pack(side="left")
        self.btn_run = ttk.Button(bar, text="🚀 Executar no Oracle", command=self.run_plan)
        self.btn_run.pack(side="right")
        ttk.Button(bar, text="💾 Salvar script…", command=self.save_script).pack(side="right", padx=6)
        ttk.Button(bar, text="📋 Copiar", command=self.copy_script).pack(side="right")
        box, self.ptree = make_tree(f, [("on", "✔", 36), ("kind", "Tipo", 90), ("table", "Tabela", 200), ("sql", "Comando", 760)],
                                    height=12, anchors={"on": "center"})
        box.pack(fill="both", expand=True, pady=8)
        self.ptree.tag_configure("off", foreground="#9CA3AF")
        for k, bg in (("grant", "#FFF7ED"), ("synonym", "#FFF7ED"), ("schema", "#FEE2E2")):
            self.ptree.tag_configure(k, background=bg)
        self.ptree.bind("<Button-1>", self._ptree_click)
        self.plan_lbl = ttk.Label(f, text="Clique em ✔ para incluir/excluir um comando. Nada roda até você confirmar.",
                                  foreground="#73809B")
        self.plan_lbl.pack(anchor="w")
        self.txt = tk.Text(f, height=8, font=("Consolas", 9), wrap="word", bg="#F8F9FE")
        self.txt.pack(fill="x", pady=(6, 0))
        self.txt.tag_configure("warn", foreground="#b45309")
        self.txt.tag_configure("ok", foreground="#15803d")
        self.txt.tag_configure("err", foreground="#b91c1c")
        self.action_buttons += [self.btn_plan, self.btn_run]

    def _log(self, text, tag=None):
        self.txt.insert("end", text + "\n", tag or ())
        self.txt.see("end")

    def make_plan(self):
        if not self.statuses:
            messagebox.showinfo("Plano", "Faça a verificação (aba 3) antes.", parent=self)
            self.nb.select(2)
            return
        target = self._target()
        if not target:
            return
        create_schema = None
        exists = target in set(self.sess.schemas)
        if self.mkschema_var.get() and not exists:
            create_schema = {"password": self.newpwd_var.get(), "tablespace": self.ts_var.get().strip() or "USERS"}
        elif not exists:
            messagebox.showwarning("Schema de destino", f"O schema {target} não existe. Marque 'Criar o schema se não "
                                                        f"existir' na aba 1 ou escolha outro.", parent=self)
            return
        owner_choice = {s.name: s.owners[self.owner_idx.get(s.name, 0) % len(s.owners)] for s in self.statuses if s.owners}
        try:
            self.steps, self.notes = O.build_plan(
                self.statements, self.statuses, self.expected, self._connected_user(), target, owner_choice,
                self.grants, self.synonyms, self.sync_var.get(), self.meta, create_schema, self.sess.privs or None)
        except O.OracleError as ex:
            messagebox.showerror("Plano", str(ex), parent=self)
            return
        self._fill_plan()
        self.txt.delete("1.0", "end")
        for n in self.notes:
            self._log(n, "warn" if n.startswith("⚠") else None)
        if not self.steps:
            self._log("Nada a fazer: todas as tabelas já existem e o acesso está concedido.", "ok")
        self.nb.select(3)

    def _fill_plan(self):
        self.ptree.delete(*self.ptree.get_children())
        for i, st in enumerate(self.steps):
            sql = " ".join(st.shown().split())
            self.ptree.insert("", "end", iid=str(i), tags=((st.kind,) if st.enabled else ("off",)),
                              values=("☑" if st.enabled else "☐", st.kind, st.table, sql[:260]))
        n = sum(1 for s in self.steps if s.enabled)
        self.plan_lbl.config(text=f"{n} de {len(self.steps)} comandos selecionados. DDL no Oracle confirma "
                                  f"automaticamente (não há rollback).")

    def _ptree_click(self, event):
        if self.ptree.identify_column(event.x) != "#1":
            return
        item = self.ptree.identify_row(event.y)
        if item:
            st = self.steps[int(item)]
            st.enabled = not st.enabled
            self._fill_plan()

    def script_text(self):
        target = self.target_var.get().upper()
        head = (f"-- Script gerado pelo Modelador ER em {datetime.datetime.now():%Y-%m-%d %H:%M}\n"
                f"-- Modelo: {self.kind_var.get()} · Schema de destino: {target}\n"
                f"-- Execute conectado como {target} (ou usuário com os privilégios indicados).\nSET DEFINE OFF\n")
        return O.render_script(self.steps, head)

    def copy_script(self):
        if not self.steps:
            return
        self.clipboard_clear()
        self.clipboard_append(self.script_text())
        self.status.config(text="Script copiado para a área de transferência.")

    def save_script(self):
        if not self.steps:
            messagebox.showinfo("Script", "Gere o plano primeiro.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".sql",
                                            initialfile=f"criar_{self.target_var.get().lower()}_{self.kind_var.get()}.sql",
                                            filetypes=[("SQL", "*.sql")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.script_text())
            self.status.config(text=f"Script salvo em {path}")

    def run_plan(self):
        if not self._need_conn():
            return
        chosen = [s for s in self.steps if s.enabled]
        if not chosen:
            messagebox.showinfo("Executar", "Nenhum comando selecionado.", parent=self)
            return
        target = self._target()
        if not target:
            return
        cnt = lambda k: sum(1 for s in chosen if s.kind == k)
        msg = (f"Executar {len(chosen)} comandos no Oracle?\n\n"
               f"Schema de destino: {target}\nBanco: {self.sess.cfg.dsn}\n\n"
               f"• {cnt('create')} CREATE TABLE  • {cnt('grant')} GRANT  • {cnt('synonym')} CREATE SYNONYM\n"
               f"• {cnt('insert')} INSERT  • {cnt('addcol')} ADD COLUMN  • {cnt('schema')} de schema/usuário\n\n"
               f"Comandos DDL confirmam automaticamente e não podem ser desfeitos.\n"
               f"Nenhuma tabela existente será apagada ou sobrescrita.")
        if not messagebox.askyesno("Confirmar execução", msg, icon="warning", parent=self):
            return
        conn, connected, stop = self.sess.conn, self._connected_user(), self.stop_var.get()
        logs = []
        self.txt.delete("1.0", "end")

        def work():
            return O.execute_steps(conn, self.steps, target, connected, stop, log=logs.append)

        def done(results):
            ok = sum(1 for r in results if r[1])
            bad = [r for r in results if not r[1]]
            for line in logs:
                self._log(line, "err" if line.startswith("ERRO") else ("ok" if line.startswith("OK") else None))
            path = self._write_log(results, target)
            self._log(f"\nConcluído: {ok} ok, {len(bad)} com erro. Log: {path}", "err" if bad else "ok")
            self.status.config(text=f"Execução: {ok} ok, {len(bad)} erro(s).")
            if bad:
                messagebox.showwarning("Execução com erro", f"{len(bad)} comando(s) falharam.\n\nPrimeiro erro:\n{bad[0][2]}",
                                       parent=self)
            else:
                messagebox.showinfo("Execução concluída", f"{ok} comandos executados em {target}.\nA verificação será atualizada.",
                                    parent=self)
            self.analyze()
            if self.ex_schema.get():
                self.ex_load_tables()
        self._bg(work, done, "Executando no Oracle…")

    def _write_log(self, results, target):
        try:
            d = os.path.join(os.path.dirname(O.profile_path()), "oracle_logs")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"exec_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"Schema: {target}  DSN: {self.sess.cfg.dsn}  Usuário: {self._connected_user()}\n")
                for st, ok, msg in results:
                    f.write(f"{'OK  ' if ok else 'ERRO'} [{st.kind}] {st.table}: {msg}\n    {' '.join(st.shown().split())[:300]}\n")
            return p
        except OSError:
            return "(não foi possível gravar o log)"
