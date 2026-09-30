"""Janela 'Oracle': conectar, ler o dicionário de dados, comparar com o modelo e criar as tabelas."""
import datetime
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import oracle_tools as O

STATE_LABEL = {"NOVA": "Nova (não existe)", "NO_DESTINO": "Já existe no destino",
               "EM_OUTRO_SCHEMA": "Existe em outro schema"}
ACTION_LABEL = {"criar": "Criar no destino", "acessar": "Conceder acesso + sinônimo", "ignorar": "Ignorar"}


class OracleDialog(tk.Toplevel):
    def __init__(self, app, get_statements, default_kind="transacional"):
        super().__init__(app)
        self.title("Oracle — dicionário de dados e criação de tabelas")
        self.geometry("1080x740")
        self.minsize(900, 600)
        self.transient(app)
        self.app = app
        self.get_statements = get_statements          # callable(kind) -> lista de comandos SQL (dialeto Oracle)
        self.conn = None
        self.views = "all"
        self.privs = set()
        self.info = {}
        self.statuses, self.expected, self.meta, self.statements = [], {}, {}, []
        self.owner_idx = {}
        self.steps, self.notes = [], []
        self.busy = False
        cfg = O.load_profile()

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

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._build_conn_tab()
        self._build_verify_tab()
        self._build_plan_tab()
        self.status = ttk.Label(self, text="Desconectado", anchor="w", padding=(10, 4))
        self.status.pack(fill="x")
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ------------------------------------------------------------ utilidades
    def destroy(self):
        self.pwd_var.set("")
        self.newpwd_var.set("")
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        super().destroy()

    def _set_busy(self, busy, msg=""):
        self.busy = busy
        self.config(cursor="watch" if busy else "")
        for b in getattr(self, "action_buttons", []):
            b.state(["disabled"] if busy else ["!disabled"])
        if msg:
            self.status.config(text=msg)

    def _bg(self, fn, done, msg):
        """Executa `fn` numa thread (a interface não trava) e chama `done(resultado)` na thread da UI."""
        if self.busy:
            return
        self._set_busy(True, msg)
        q = queue.Queue()

        def work():
            try:
                q.put(("ok", fn()))
            except Exception as ex:      # noqa: BLE001 — repassa qualquer erro para a UI
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
                messagebox.showerror("Oracle", str(val), parent=self)
            else:
                done(val)
        self.after(80, poll)

    def _cfg(self):
        return O.ConnConfig(user=self.user_var.get().strip(), dsn=self.dsn_var.get().strip(),
                            tns_dir=self.tns_var.get().strip())

    def _connected_user(self):
        return (self.info.get("user") or self.user_var.get()).upper()

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
            ttk.Entry(box, textvariable=var, width=46, show=show or "").grid(row=i, column=1, sticky="w", padx=8)
        ttk.Label(box, text="Ex.: localhost:1521/FREEPDB1  ·  a senha nunca é gravada em disco", foreground="#73809B").grid(
            row=4, column=1, sticky="w", padx=8)
        bar = ttk.Frame(box)
        bar.grid(row=5, column=1, sticky="w", padx=8, pady=(8, 0))
        self.btn_connect = ttk.Button(bar, text="🔌 Conectar", command=self.connect)
        self.btn_connect.pack(side="left")
        ttk.Button(bar, text="💾 Salvar perfil (sem senha)", command=self.save_profile).pack(side="left", padx=6)
        self.conn_info = ttk.Label(f, text="", foreground="#0f766e", justify="left")
        self.conn_info.pack(anchor="w", pady=(8, 4))

        dest = ttk.LabelFrame(f, text="O que criar e onde", padding=10)
        dest.pack(fill="x", pady=6)
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
                  foreground="#73809B").pack(anchor="w", pady=(10, 0))
        self.action_buttons = [self.btn_connect]

    def _toggle_mkschema(self):
        st = "normal" if self.mkschema_var.get() else "disabled"
        for w in (self.newpwd_entry, self.ts_cb):
            w.config(state=st)

    def save_profile(self):
        O.save_profile(self._cfg())
        self.status.config(text=f"Perfil salvo em {O.profile_path()} (sem a senha)")

    def connect(self):
        cfg, pwd = self._cfg(), self.pwd_var.get()

        def work():
            conn = O.connect(cfg, pwd)
            try:
                info = O.session_info(conn)
                return {"conn": conn, "info": info, "views": O.detect_views(conn), "privs": O.session_privileges(conn),
                        "schemas": O.list_schemas(conn), "tablespaces": O.list_tablespaces(conn)}
            except Exception:
                conn.close()
                raise

        def done(r):
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn, self.info, self.views, self.privs = r["conn"], r["info"], r["views"], r["privs"]
            self.target_cb.config(values=r["schemas"])
            if r["tablespaces"]:
                self.ts_cb.config(values=r["tablespaces"])
            if not self.target_var.get():
                self.target_var.set(r["info"]["current_schema"])
            scope = "dicionário completo (DBA_*)" if self.views == "dba" else "dicionário restrito ao que o usuário enxerga (ALL_*)"
            self.conn_info.config(text=f"✔ Conectado como {r['info']['user']}  ·  container {r['info']['container'] or '—'}  ·  "
                                       f"{len(r['schemas'])} schemas  ·  {scope}")
            self.status.config(text="Conectado. Escolha o schema de destino e vá para '2. Verificação'.")
            self.pwd_var.set("")
        self._bg(work, done, "Conectando…")

    # ------------------------------------------------------------ aba 2: verificação
    def _build_verify_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="2. Verificação")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.btn_analyze = ttk.Button(bar, text="🔎 Ler dicionário de dados e comparar", command=self.analyze)
        self.btn_analyze.pack(side="left")
        ttk.Label(bar, text="  duplo-clique em 'Ação' (ou em 'Onde existe' quando houver vários schemas) para alterar",
                  foreground="#73809B").pack(side="left")
        cols = ("table", "state", "where", "diff", "action")
        self.vtree = ttk.Treeview(f, columns=cols, show="headings", height=9)
        for c, t, w in (("table", "Tabela do modelo", 220), ("state", "Situação", 190), ("where", "Onde existe", 120),
                        ("diff", "Diferenças (colunas)", 300), ("action", "Ação", 220)):
            self.vtree.heading(c, text=t)
            self.vtree.column(c, width=w, anchor="w")
        self.vtree.tag_configure("NOVA", background="#ECFDF5")
        self.vtree.tag_configure("NO_DESTINO", background="#EFF6FF")
        self.vtree.tag_configure("EM_OUTRO_SCHEMA", background="#FFF7ED")
        self.vtree.tag_configure("CONFLITO", background="#FEE2E2")
        self.vtree.pack(fill="x", pady=8)
        self.vtree.bind("<<TreeviewSelect>>", self._show_detail)
        self.vtree.bind("<Double-1>", self._vtree_dblclick)
        self.detail_lbl = ttk.Label(f, text="Selecione uma tabela para ver a comparação coluna a coluna.", foreground="#73809B")
        self.detail_lbl.pack(anchor="w")
        dc = ("col", "model", "oracle", "status", "comment")
        self.dtree = ttk.Treeview(f, columns=dc, show="headings")
        for c, t, w in (("col", "Coluna", 200), ("model", "Tipo no modelo", 140), ("oracle", "Tipo no Oracle", 140),
                        ("status", "Situação", 150), ("comment", "Comentário no dicionário", 380)):
            self.dtree.heading(c, text=t)
            self.dtree.column(c, width=w, anchor="w")
        self.dtree.tag_configure("falta", foreground="#b91c1c")
        self.dtree.tag_configure("extra", foreground="#b45309")
        self.dtree.tag_configure("tipo", foreground="#7c3aed")
        self.dtree.pack(fill="both", expand=True, pady=(4, 0))
        self.action_buttons.append(self.btn_analyze)

    def _need_conn(self):
        if self.conn is None:
            messagebox.showinfo("Oracle", "Conecte-se primeiro (aba 1).", parent=self)
            self.nb.select(0)
            return False
        return True

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
        conn, stmts, views = self.conn, self.statements, self.views

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
            self.nb.select(1)
        self._bg(work, done, "Lendo o dicionário de dados…")

    def _fill_verify(self):
        self.vtree.delete(*self.vtree.get_children())
        for s in self.statuses:
            tag = "CONFLITO" if s.conflict else s.state
            diff = ""
            if s.state != "NOVA":
                d = s.diff
                bits = []
                if d.get("missing"):
                    bits.append(f"faltam {len(d['missing'])}")
                if d.get("type_diff"):
                    bits.append(f"tipo diferente {len(d['type_diff'])}")
                if d.get("extra"):
                    bits.append(f"a mais {len(d['extra'])}")
                diff = ", ".join(bits) or "colunas iguais ao modelo"
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
        self.vtree.selection_set(item)

    def _show_detail(self, _e=None):
        sel = self.vtree.selection()
        self.dtree.delete(*self.dtree.get_children())
        if not sel:
            return
        s = next(x for x in self.statuses if x.name == sel[0])
        exp_cols = self.expected[s.name]["columns"]
        owner = self.target_var.get().upper() if s.state == "NO_DESTINO" else (s.owners[self.owner_idx.get(s.name, 0) % len(s.owners)] if s.owners else None)
        m = self.meta.get((owner, s.name)) if owner else None
        head = f"{s.name}"
        if m is not None:
            head += f"  ·  {owner}" + (f"  ·  comentário: {m['comment']}" if m["comment"] else "")
            if m["pk"]:
                head += f"  ·  PK: {', '.join(m['pk'])}"
            if m["fks"]:
                head += f"  ·  FKs: " + "; ".join(f"{'/'.join(f['columns'])} → {f['ref_table']}" for f in m["fks"])
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

    # ------------------------------------------------------------ aba 3: plano e execução
    def _build_plan_tab(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="3. Plano e execução")
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
        cols = ("on", "kind", "table", "sql")
        self.ptree = ttk.Treeview(f, columns=cols, show="headings", height=11)
        for c, t, w in (("on", "✔", 36), ("kind", "Tipo", 90), ("table", "Tabela", 190), ("sql", "Comando", 640)):
            self.ptree.heading(c, text=t)
            self.ptree.column(c, width=w, anchor="w" if c != "on" else "center", stretch=(c == "sql"))
        self.ptree.tag_configure("off", foreground="#9CA3AF")
        self.ptree.tag_configure("grant", background="#FFF7ED")
        self.ptree.tag_configure("synonym", background="#FFF7ED")
        self.ptree.tag_configure("schema", background="#FEE2E2")
        self.ptree.pack(fill="both", expand=True, pady=8)
        self.ptree.bind("<Button-1>", self._ptree_click)
        self.plan_lbl = ttk.Label(f, text="Clique em ✔ para incluir/excluir um comando. Nada roda até você confirmar.",
                                  foreground="#73809B")
        self.plan_lbl.pack(anchor="w")
        self.txt = tk.Text(f, height=9, font=("Consolas", 9), wrap="word", bg="#F8F9FE")
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
            messagebox.showinfo("Plano", "Faça a verificação (aba 2) antes.", parent=self)
            self.nb.select(1)
            return
        target = self._target()
        if not target:
            return
        create_schema = None
        exists = target in set(self._schemas())
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
                self.grants, self.synonyms, self.sync_var.get(), self.meta, create_schema, self.privs or None)
        except O.OracleError as ex:
            messagebox.showerror("Plano", str(ex), parent=self)
            return
        self._fill_plan()
        self.txt.delete("1.0", "end")
        for n in self.notes:
            self._log(n, "warn" if n.startswith("⚠") else None)
        if not self.steps:
            self._log("Nada a fazer: todas as tabelas já existem e o acesso está concedido.", "ok")
        self.nb.select(2)

    def _schemas(self):
        return list(self.target_cb.cget("values"))

    def _fill_plan(self):
        self.ptree.delete(*self.ptree.get_children())
        for i, st in enumerate(self.steps):
            sql = " ".join(st.shown().split())
            tags = (st.kind,) if st.enabled else ("off",)
            self.ptree.insert("", "end", iid=str(i), tags=tags,
                              values=("☑" if st.enabled else "☐", st.kind, st.table, sql[:220]))
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
               f"Schema de destino: {target}\nBanco: {self.dsn_var.get()}\n\n"
               f"• {cnt('create')} CREATE TABLE  • {cnt('grant')} GRANT  • {cnt('synonym')} CREATE SYNONYM\n"
               f"• {cnt('insert')} INSERT  • {cnt('addcol')} ADD COLUMN  • {cnt('schema')} de schema/usuário\n\n"
               f"Comandos DDL confirmam automaticamente e não podem ser desfeitos.\n"
               f"Nenhuma tabela existente será apagada ou sobrescrita.")
        if not messagebox.askyesno("Confirmar execução", msg, icon="warning", parent=self):
            return
        conn, connected, stop = self.conn, self._connected_user(), self.stop_var.get()
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
            self.analyze()          # relê o dicionário: confirma o que existe agora
        self._bg(work, done, "Executando no Oracle…")

    def _write_log(self, results, target):
        try:
            d = os.path.join(os.path.dirname(O.profile_path()), "oracle_logs")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"exec_{datetime.datetime.now():%Y%m%d_%H%M%S}.log")
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"Schema: {target}  DSN: {self.dsn_var.get()}  Usuário: {self._connected_user()}\n")
                for st, ok, msg in results:
                    f.write(f"{'OK  ' if ok else 'ERRO'} [{st.kind}] {st.table}: {msg}\n    {' '.join(st.shown().split())[:300]}\n")
            return p
        except OSError:
            return "(não foi possível gravar o log)"
