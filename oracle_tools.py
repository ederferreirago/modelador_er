"""Integração com o Oracle: leitura do dicionário de dados, comparação com o modelo e plano de criação.

Somente leitura nas views ALL_* (não exige privilégio de DBA). Nada é removido ou sobrescrito:
o plano só cria tabelas que não existem, concede acesso (GRANT) e cria sinônimos para tabelas que já
existem em outro schema, e opcionalmente adiciona colunas que faltam (nunca altera/remove colunas).

Sem dependência de Tk. O driver `oracledb` (modo thin, sem Oracle Client) é importado sob demanda.
"""
import json
import os
import re
from dataclasses import dataclass, field

IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
TABLE_PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "REFERENCES")


class OracleError(Exception):
    """Erro amigável (conexão, permissão, identificador inválido...)."""


def check_ident(name, what="identificador"):
    if not name or not IDENT_RE.match(name):
        raise OracleError(f"{what} inválido: {name!r} (use letras, números, _ $ #; começando por letra)")
    return name.upper()


# ---------------------------------------------------------------- conexão
@dataclass
class ConnConfig:
    user: str = ""
    dsn: str = "localhost:1521/FREEPDB1"
    tns_dir: str = ""          # pasta com tnsnames.ora / wallet (opcional)
    timeout: int = 10

    def to_public_dict(self):
        """Perfil salvo em disco: NUNCA inclui a senha."""
        return {"user": self.user, "dsn": self.dsn, "tns_dir": self.tns_dir}


def profile_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ModeladorER", "oracle_profile.json")


def load_profile():
    try:
        with open(profile_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return ConnConfig(user=d.get("user", ""), dsn=d.get("dsn", ConnConfig.dsn), tns_dir=d.get("tns_dir", ""))
    except (OSError, ValueError):
        return ConnConfig()


def save_profile(cfg):
    p = profile_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg.to_public_dict(), f, indent=2)


def connect(cfg, password):
    try:
        import oracledb
    except ImportError as ex:
        raise OracleError("Driver não instalado. Execute: pip install oracledb") from ex
    if not cfg.user or not cfg.dsn:
        raise OracleError("Informe usuário e DSN (ex.: host:1521/servico).")
    kw = dict(user=cfg.user, password=password, dsn=cfg.dsn, tcp_connect_timeout=cfg.timeout)
    if cfg.tns_dir:
        kw["config_dir"] = cfg.tns_dir
    try:
        return oracledb.connect(**kw)
    except Exception as ex:     # oracledb.Error e erros de rede
        raise OracleError(_clean_ora(ex)) from ex


def is_alive(conn):
    """True se a conexão ainda responde (ping leve; não lança exceção)."""
    if conn is None:
        return False
    try:
        conn.ping()
        return True
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM dual")
            cur.fetchall()
            cur.close()
            return True
        except Exception:
            return False


def _clean_ora(ex):
    msg = str(ex).strip().splitlines()[0] if str(ex).strip() else ex.__class__.__name__
    return msg


class OracleSession:
    """Conexão Oracle que vive enquanto o app estiver aberto (não morre ao fechar a janela do Oracle).

    A senha NÃO é guardada: se a conexão cair, é preciso informá-la de novo."""

    def __init__(self):
        self.conn = None
        self.cfg = ConnConfig()
        self.info = {}
        self.views = "all"
        self.privs = set()
        self.schemas = []
        self.tablespaces = []

    @property
    def connected(self):
        return self.conn is not None

    def check(self):
        """Confere se ainda está viva; se caiu, limpa o estado e devolve False."""
        if self.conn is None:
            return False
        if is_alive(self.conn):
            return True
        self._drop()
        return False

    def open(self, cfg, password):
        conn = connect(cfg, password)
        try:
            info = session_info(conn)
            data = dict(views=detect_views(conn), privs=session_privileges(conn), schemas=list_schemas(conn),
                        tablespaces=list_tablespaces(conn))
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        self.close()
        self.conn, self.cfg, self.info = conn, cfg, info
        self.views, self.privs = data["views"], data["privs"]
        self.schemas, self.tablespaces = data["schemas"], data["tablespaces"]
        return self

    def _drop(self):
        self.conn = None
        self.info = {}

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self._drop()

    def label(self):
        if not self.conn:
            return "Oracle: desconectado"
        return f"Oracle: {self.info.get('user', '')}@{self.cfg.dsn}"


# ---------------------------------------------------------------- consultas ao dicionário
def _rows(conn, sql, binds=None):
    cur = conn.cursor()
    try:
        cur.execute(sql, binds or {})
        return cur.fetchall()
    except Exception as ex:
        raise OracleError(_clean_ora(ex)) from ex
    finally:
        try:
            cur.close()
        except Exception:
            pass


def session_info(conn):
    """{'user': ..., 'current_schema': ..., 'container': ..., 'version': ...}"""
    rows = _rows(conn, "SELECT SYS_CONTEXT('USERENV','SESSION_USER'), SYS_CONTEXT('USERENV','CURRENT_SCHEMA'), "
                       "SYS_CONTEXT('USERENV','CON_NAME') FROM dual")
    user, schema, con = rows[0] if rows else ("", "", "")
    try:
        ver = getattr(conn, "version", "")
    except Exception:
        ver = ""
    return {"user": user, "current_schema": schema, "container": con or "", "version": ver}


def detect_views(conn):
    """'dba' se o usuário enxerga o dicionário completo (DBA_*); senão 'all' (só o que tem acesso)."""
    try:
        _rows(conn, "SELECT 1 FROM dba_tables WHERE ROWNUM = 1")
        return "dba"
    except OracleError:
        return "all"


def session_privileges(conn):
    """Privilégios de sistema habilitados na sessão (inclui os vindos de roles)."""
    try:
        return {r[0] for r in _rows(conn, "SELECT privilege FROM session_privs")}
    except OracleError:
        return set()


def list_schemas(conn):
    """Schemas de usuário (exclui os mantidos pelo Oracle), em ordem alfabética."""
    rows = _rows(conn, "SELECT username FROM all_users WHERE oracle_maintained = 'N' ORDER BY username")
    return [r[0] for r in rows]


def list_tablespaces(conn):
    try:
        return [r[0] for r in _rows(conn, "SELECT tablespace_name FROM user_tablespaces "
                                          "WHERE contents = 'PERMANENT' ORDER BY tablespace_name")]
    except OracleError:
        return []


def _in_clause(prefix, values):
    names = [f"{prefix}{i}" for i in range(len(values))]
    return ", ".join(":" + n for n in names), dict(zip(names, values))


def _chunks(seq, n=500):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def find_tables(conn, table_names, views="all"):
    """Onde cada tabela já existe: {NOME: [{'owner','comment'}]} (todos os schemas visíveis ao usuário)."""
    names = sorted({n.upper() for n in table_names})
    found = {}
    for part in _chunks(names):
        in_sql, binds = _in_clause("t", part)
        rows = _rows(conn,
                     "SELECT t.owner, t.table_name, c.comments "
                     f"FROM {views}_tables t "
                     f"LEFT JOIN {views}_tab_comments c ON c.owner = t.owner AND c.table_name = t.table_name "
                     "WHERE t.table_name IN (" + in_sql + ") "
                     "AND t.owner IN (SELECT username FROM all_users WHERE oracle_maintained = 'N') "
                     "ORDER BY t.table_name, t.owner", binds)
        for owner, tname, comment in rows:
            found.setdefault(tname, []).append({"owner": owner, "comment": comment or ""})
    return found


def _fmt_type(dtype, char_len, data_len, prec, scale, char_used):
    d = (dtype or "").upper()
    if d in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        n = char_len if char_len else data_len
        return f"{d}({int(n)})" if n else d
    if d == "NUMBER":
        if prec is None:
            return "NUMBER"
        if scale in (None, 0):
            return f"NUMBER({int(prec)})"
        return f"NUMBER({int(prec)},{int(scale)})"
    if d.startswith("TIMESTAMP") or d in ("DATE", "CLOB", "BLOB", "NCLOB", "FLOAT", "RAW"):
        return d
    return d


def read_table_metadata(conn, owner, table_names, views="all"):
    """Metadados de tabelas de um schema: colunas, PK, FKs, comentários e privilégios.

    Retorna {NOME: {'comment', 'columns': [...], 'pk': [...], 'fks': [...], 'privs': {grantee: [priv]}}}.
    """
    owner = check_ident(owner, "schema")
    names = sorted({n.upper() for n in table_names})
    meta = {n: {"comment": "", "columns": [], "pk": [], "fks": [], "privs": {}} for n in names}
    for part in _chunks(names):
        in_sql, binds = _in_clause("t", part)
        b = dict(binds, owner=owner)
        for tname, comment in _rows(conn, f"SELECT table_name, comments FROM {views}_tab_comments "
                                          "WHERE owner = :owner AND table_name IN (" + in_sql + ")", b):
            if tname in meta:
                meta[tname]["comment"] = comment or ""
        col_comments = {(t, c): (cm or "") for t, c, cm in _rows(
            conn, f"SELECT table_name, column_name, comments FROM {views}_col_comments "
                  "WHERE owner = :owner AND table_name IN (" + in_sql + ")", b)}
        for row in _rows(conn, "SELECT table_name, column_name, data_type, char_length, data_length, data_precision, "
                               f"data_scale, nullable, column_id, char_used, identity_column "
                               f"FROM {views}_tab_columns WHERE owner = :owner AND table_name IN (" + in_sql + ") "
                               "ORDER BY table_name, column_id", b):
            t, c, dt, cl, dl, dp, ds, nl, cid, cu, ident = row
            if t in meta:
                meta[t]["columns"].append({
                    "name": c, "type": _fmt_type(dt, cl, dl, dp, ds, cu), "nullable": nl == "Y",
                    "identity": (ident or "NO") == "YES", "comment": col_comments.get((t, c), "")})
        cons = _rows(conn, "SELECT c.table_name, c.constraint_name, c.constraint_type, c.r_owner, c.r_constraint_name, "
                           f"cc.column_name, cc.position FROM {views}_constraints c "
                           f"JOIN {views}_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
                           "WHERE c.owner = :owner AND c.table_name IN (" + in_sql + ") AND c.constraint_type IN ('P','R') "
                           "ORDER BY c.table_name, c.constraint_name, cc.position", b)
        fk_map = {}
        for t, cname, ctype, r_owner, r_cname, col, pos in cons:
            if t not in meta:
                continue
            if ctype == "P":
                meta[t]["pk"].append(col)
            else:
                fk_map.setdefault((t, cname), {"cols": [], "r_owner": r_owner, "r_cname": r_cname})["cols"].append(col)
        for (t, cname), fk in fk_map.items():
            ref = _rows(conn, f"SELECT table_name FROM {views}_constraints WHERE owner = :o AND constraint_name = :c",
                        {"o": fk["r_owner"], "c": fk["r_cname"]})
            meta[t]["fks"].append({"name": cname, "columns": fk["cols"], "ref_owner": fk["r_owner"],
                                   "ref_table": ref[0][0] if ref else "?"})
        ocol = "table_schema" if views == "all" else "owner"
        for grantee, t, priv in _rows(conn, f"SELECT grantee, table_name, privilege FROM {views}_tab_privs "
                                            f"WHERE {ocol} = :owner AND table_name IN (" + in_sql + ")", b):
            if t in meta:
                meta[t]["privs"].setdefault(grantee, []).append(priv)
    return meta


def objects_in_schema(conn, schema, names, views="all"):
    """Objetos (tabela, view, sinônimo...) do schema com esses nomes: {NOME: TIPO}."""
    schema = check_ident(schema, "schema")
    out = {}
    for part in _chunks(sorted({n.upper() for n in names})):
        in_sql, binds = _in_clause("t", part)
        for name, otype in _rows(conn, f"SELECT object_name, object_type FROM {views}_objects WHERE owner = :owner "
                                       "AND object_name IN (" + in_sql + ")", dict(binds, owner=schema)):
            out[name] = otype
    return out


def existing_grants(conn, grantee, refs, views="all"):
    """{(OWNER, TABELA): {privilégios}} já concedidos a `grantee` (inclui PUBLIC)."""
    grantee = check_ident(grantee, "schema")
    result = {}
    for part in _chunks(sorted(set(refs)), 200):
        conds, binds = [], {"g": grantee}
        for i, (o, t) in enumerate(part):
            ocol = "table_schema" if views == "all" else "owner"
            conds.append(f"({ocol} = :o{i} AND table_name = :t{i})")
            binds[f"o{i}"], binds[f"t{i}"] = o, t
        ocol = "table_schema" if views == "all" else "owner"
        for o, t, priv in _rows(conn, f"SELECT {ocol}, table_name, privilege FROM {views}_tab_privs "
                                      "WHERE grantee IN (:g, 'PUBLIC') AND (" + " OR ".join(conds) + ")", binds):
            result.setdefault((o, t), set()).add(priv)
    return result


def existing_synonyms(conn, schema, names, views="all"):
    """{NOME_SINONIMO: (table_owner, table_name)} no schema informado."""
    schema = check_ident(schema, "schema")
    out = {}
    for part in _chunks(sorted({n.upper() for n in names})):
        in_sql, binds = _in_clause("t", part)
        for s, to, tn in _rows(conn, f"SELECT synonym_name, table_owner, table_name FROM {views}_synonyms "
                                     "WHERE owner = :owner AND synonym_name IN (" + in_sql + ")", dict(binds, owner=schema)):
            out[s] = (to, tn)
    return out


# ---------------------------------------------------------------- explorador do dicionário
def list_tables(conn, owner, views="all"):
    """Tabelas de um schema com resumo: [{'name','rows','analyzed','tablespace','comment','partitioned','temporary'}]."""
    owner = check_ident(owner, "schema")
    rows = _rows(conn,
                 "SELECT t.table_name, t.num_rows, t.last_analyzed, t.tablespace_name, c.comments, "
                 "t.partitioned, t.temporary "
                 f"FROM {views}_tables t LEFT JOIN {views}_tab_comments c "
                 "ON c.owner = t.owner AND c.table_name = t.table_name "
                 "WHERE t.owner = :owner ORDER BY t.table_name", {"owner": owner})
    return [{"name": r[0], "rows": r[1], "analyzed": r[2], "tablespace": r[3] or "", "comment": r[4] or "",
             "partitioned": (r[5] or "NO") == "YES", "temporary": (r[6] or "N") == "Y"} for r in rows]


def table_details(conn, owner, table, views="all"):
    """Tudo sobre uma tabela: resumo, colunas, constraints, índices, privilégios e FKs que a referenciam."""
    owner, table = check_ident(owner, "schema"), check_ident(table, "tabela")
    b = {"owner": owner, "t": table}
    summary = {}
    for r in _rows(conn, "SELECT t.num_rows, t.last_analyzed, t.tablespace_name, t.partitioned, t.temporary, c.comments "
                         f"FROM {views}_tables t LEFT JOIN {views}_tab_comments c "
                         "ON c.owner = t.owner AND c.table_name = t.table_name "
                         "WHERE t.owner = :owner AND t.table_name = :t", b):
        summary = {"rows": r[0], "analyzed": r[1], "tablespace": r[2] or "", "partitioned": (r[3] or "NO") == "YES",
                   "temporary": (r[4] or "N") == "Y", "comment": r[5] or ""}
    meta = read_table_metadata(conn, owner, [table], views).get(table, {})
    # constraints com colunas (P, U, R, C)
    cons = {}
    for name, ctype, status, rule, r_owner, r_name, cond in _rows(
            conn, "SELECT constraint_name, constraint_type, status, delete_rule, r_owner, r_constraint_name, "
                  f"search_condition FROM {views}_constraints WHERE owner = :owner AND table_name = :t "
                  "AND constraint_type IN ('P','U','R','C') ORDER BY constraint_type, constraint_name", b):
        cons[name] = {"name": name, "type": ctype, "status": status, "delete_rule": rule or "",
                      "r_owner": r_owner, "r_name": r_name, "columns": [], "ref_table": "",
                      "condition": (str(cond) if cond is not None else "")}
    for name, col in _rows(conn, f"SELECT constraint_name, column_name FROM {views}_cons_columns "
                                 "WHERE owner = :owner AND table_name = :t ORDER BY constraint_name, position", b):
        if name in cons:
            cons[name]["columns"].append(col)
    for c in cons.values():
        if c["type"] == "R" and c["r_name"]:
            ref = _rows(conn, f"SELECT table_name FROM {views}_constraints WHERE owner = :o AND constraint_name = :c",
                        {"o": c["r_owner"], "c": c["r_name"]})
            c["ref_table"] = ref[0][0] if ref else "?"
    # índices
    idx = {}
    for name, uniq, itype, status in _rows(conn, "SELECT index_name, uniqueness, index_type, status "
                                                 f"FROM {views}_indexes WHERE table_owner = :owner AND table_name = :t "
                                                 "ORDER BY index_name", b):
        idx[name] = {"name": name, "unique": uniq == "UNIQUE", "type": itype, "status": status, "columns": []}
    for name, col in _rows(conn, f"SELECT index_name, column_name FROM {views}_ind_columns "
                                 "WHERE table_owner = :owner AND table_name = :t ORDER BY index_name, column_position", b):
        if name in idx:
            idx[name]["columns"].append(col)
    # quem referencia esta tabela (FKs filhas)
    children = []
    for tname, cname, rule in _rows(
            conn, "SELECT c.table_name, c.constraint_name, c.delete_rule "
                  f"FROM {views}_constraints c JOIN {views}_constraints p "
                  "ON p.owner = c.r_owner AND p.constraint_name = c.r_constraint_name "
                  "WHERE c.constraint_type = 'R' AND p.owner = :owner AND p.table_name = :t "
                  "ORDER BY c.table_name, c.constraint_name", b):
        children.append({"table": tname, "constraint": cname, "delete_rule": rule or ""})
    # privilégios concedidos sobre a tabela
    ocol = "table_schema" if views == "all" else "owner"
    privs = [{"grantee": g, "privilege": p, "grantable": gr == "YES"} for g, p, gr in _rows(
        conn, f"SELECT grantee, privilege, grantable FROM {views}_tab_privs "
              f"WHERE {ocol} = :owner AND table_name = :t ORDER BY grantee, privilege", b)]
    return {"owner": owner, "table": table, "summary": summary, "columns": meta.get("columns", []),
            "pk": meta.get("pk", []), "constraints": list(cons.values()), "indexes": list(idx.values()),
            "children": children, "privileges": privs}


def get_ddl(conn, owner, table):
    """DDL real da tabela via DBMS_METADATA (pode exigir privilégio); devolve texto ou levanta OracleError."""
    owner, table = check_ident(owner, "schema"), check_ident(table, "tabela")
    cur = conn.cursor()
    try:
        cur.execute("SELECT DBMS_METADATA.GET_DDL('TABLE', :t, :o) FROM dual", {"t": table, "o": owner})
        row = cur.fetchone()
        val = row[0] if row else ""
        return val.read() if hasattr(val, "read") else str(val or "")
    except Exception as ex:
        raise OracleError(_clean_ora(ex)) from ex
    finally:
        try:
            cur.close()
        except Exception:
            pass


def sample_rows(conn, owner, table, limit=50):
    """Amostra somente-leitura: (colunas, linhas). Identificadores validados e entre aspas."""
    owner, table = check_ident(owner, "schema"), check_ident(table, "tabela")
    limit = max(1, min(int(limit), 500))
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT * FROM "{owner}"."{table}" FETCH FIRST {limit} ROWS ONLY')
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return cols, rows
    except Exception as ex:
        raise OracleError(_clean_ora(ex)) from ex
    finally:
        try:
            cur.close()
        except Exception:
            pass


def count_objects(conn, owner, views="all"):
    """Contagem de objetos do schema por tipo: {'TABLE': n, 'VIEW': n, ...}."""
    owner = check_ident(owner, "schema")
    return {t: n for t, n in _rows(conn, f"SELECT object_type, COUNT(*) FROM {views}_objects WHERE owner = :owner "
                                         "GROUP BY object_type ORDER BY object_type", {"owner": owner})}


# ---------------------------------------------------------------- análise do script DDL
def split_statements(sql):
    """Divide o script em comandos, respeitando aspas simples e comentários `--`."""
    stmts, buf, i, n = [], [], 0, len(sql)
    in_str = False
    while i < n:
        ch = sql[i]
        if in_str:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            buf.append(ch)
        elif ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j       # descarta o comentário até o fim da linha
            continue
        elif ch == ";":
            text = "".join(buf).strip()
            if text:
                stmts.append(text)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


_TARGET_RES = [
    (re.compile(r"^CREATE\s+TABLE\s+([A-Za-z0-9_$#]+)", re.I), "create"),
    (re.compile(r"^ALTER\s+TABLE\s+([A-Za-z0-9_$#]+)", re.I), "alter"),
    (re.compile(r"^COMMENT\s+ON\s+TABLE\s+([A-Za-z0-9_$#]+)", re.I), "comment"),
    (re.compile(r"^COMMENT\s+ON\s+COLUMN\s+([A-Za-z0-9_$#]+)\.", re.I), "comment"),
    (re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+[A-Za-z0-9_$#]+\s+ON\s+([A-Za-z0-9_$#]+)", re.I), "index"),
    (re.compile(r"^INSERT\s+INTO\s+([A-Za-z0-9_$#]+)", re.I), "insert"),
]


def classify(stmt):
    """(TABELA_MAIÚSCULA, tipo) do comando; (None, 'other') se não for reconhecido."""
    for rx, kind in _TARGET_RES:
        m = rx.match(stmt)
        if m:
            return m.group(1).upper(), kind
    return None, "other"


def _split_top(body):
    parts, depth, cur, in_str = [], 0, [], False
    for ch in body:
        if ch == "'":
            in_str = not in_str
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


_COL_RE = re.compile(r"^([A-Za-z0-9_$#]+)\s+([A-Za-z0-9_]+(?:\s*\([^)]*\))?)", re.I)
_SKIP = ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK")


def _parse_col(item):
    first = item.split(None, 1)[0].upper() if item.split() else ""
    if first in _SKIP:
        return None
    m = _COL_RE.match(item)
    if not m:
        return None
    return {"name": m.group(1).upper(), "type": re.sub(r"\s+", "", m.group(2).upper()),
            "not_null": bool(re.search(r"\bNOT\s+NULL\b", item, re.I)), "def": item.strip()}


def expected_tables(statements):
    """{TABELA: {'columns': [col...], 'order': n}} obtido do CREATE TABLE e dos ALTER ... ADD [COLUMN]."""
    tables = {}
    for st in statements:
        name, kind = classify(st)
        if kind == "create":
            body = st[st.index("(") + 1: st.rindex(")")]
            cols = [c for c in (_parse_col(x) for x in _split_top(body)) if c]
            tables[name] = {"columns": cols, "order": len(tables)}
        elif kind == "alter" and name in tables:
            m = re.match(r"^ALTER\s+TABLE\s+\S+\s+ADD\s+(?:COLUMN\s+)?(.*)$", st, re.I | re.S)
            if m and not re.match(r"^\s*CONSTRAINT\b", m.group(1), re.I):
                inner = m.group(1).strip()
                if inner.startswith("(") and inner.endswith(")"):
                    inner = inner[1:-1]
                for x in _split_top(inner):
                    col = _parse_col(x)
                    if col:
                        tables[name]["columns"].append(col)
    return tables


def strip_column_modifiers(col_def):
    """`nome TIPO ... [NOT NULL]` -> definição usada em ALTER TABLE ADD (sem NOT NULL: a tabela pode ter linhas)."""
    d = re.sub(r"\s+NOT\s+NULL\b", "", col_def, flags=re.I)
    d = re.sub(r"\s+COMMENT\s+'.*$", "", d, flags=re.I | re.S)
    return d.strip()


def compare_columns(expected_cols, actual_cols):
    """Diferenças entre o modelo e o Oracle: faltando, a mais e tipo diferente."""
    exp = {c["name"]: c for c in expected_cols}
    act = {c["name"]: c for c in actual_cols}
    missing = [n for n in exp if n not in act]
    extra = [n for n in act if n not in exp]
    diff = []
    for n, c in exp.items():
        if n in act and _norm_type(c["type"]) != _norm_type(act[n]["type"]):
            diff.append((n, c["type"], act[n]["type"]))
    return {"missing": missing, "extra": extra, "type_diff": diff}


def _norm_type(t):
    t = re.sub(r"\s+", "", (t or "").upper())
    t = t.replace("VARCHAR2(", "VARCHAR2(").replace("CHAR)", ")")
    return {"INTEGER": "NUMBER(38)", "INT": "NUMBER(38)"}.get(t, t)


# ---------------------------------------------------------------- plano
@dataclass
class Step:
    kind: str                    # schema | grant | synonym | create | alter | comment | index | insert | addcol
    table: str
    sql: str
    note: str = ""
    display_sql: str = ""        # versão exibida (senha mascarada)
    enabled: bool = True
    risky: bool = False

    def shown(self):
        return self.display_sql or self.sql


@dataclass
class TableStatus:
    name: str
    state: str                   # NOVA | NO_DESTINO | EM_OUTRO_SCHEMA
    owners: list = field(default_factory=list)     # schemas onde existe (fora do destino)
    action: str = "criar"        # criar | acessar | ignorar
    diff: dict = field(default_factory=dict)
    conflict: str = ""           # objeto de outro tipo com o mesmo nome no destino
    comment: str = ""
    n_stmts: int = 0


def analyze(conn, statements, target_schema, views="all"):
    """Compara as tabelas do script com o Oracle e devolve (lista de TableStatus, expected, existing_meta).

    existing_meta traz os metadados das tabelas encontradas: {(OWNER, TABELA): {...}}."""
    target = check_ident(target_schema, "schema de destino")
    exp = expected_tables(statements)
    names = list(exp)
    found = find_tables(conn, names, views)
    clash = objects_in_schema(conn, target, names, views)
    counts = {}
    for st in statements:
        t, _ = classify(st)
        if t:
            counts[t] = counts.get(t, 0) + 1
    meta = {}
    by_owner = {}
    for n in names:
        for o in found.get(n, []):
            by_owner.setdefault(o["owner"], []).append(n)
    for owner, tnames in by_owner.items():
        for tn, m in read_table_metadata(conn, owner, tnames, views).items():
            meta[(owner, tn)] = m
    result = []
    for n in sorted(names, key=lambda k: exp[k]["order"]):
        owners = [o["owner"] for o in found.get(n, [])]
        comment = next((o["comment"] for o in found.get(n, []) if o["comment"]), "")
        if target in owners:
            ts = TableStatus(n, "NO_DESTINO", [o for o in owners if o != target], "ignorar",
                             compare_columns(exp[n]["columns"], meta[(target, n)]["columns"]), comment=comment)
        elif owners:
            ts = TableStatus(n, "EM_OUTRO_SCHEMA", owners, "acessar",
                             compare_columns(exp[n]["columns"], meta[(owners[0], n)]["columns"]), comment=comment)
        else:
            ts = TableStatus(n, "NOVA", [], "criar")
            if n in clash:
                ts.conflict = clash[n]
                ts.action = "ignorar"
        ts.n_stmts = counts.get(n, 0)
        result.append(ts)
    return result, exp, meta


def build_plan(statements, statuses, expected, connected_user, target_schema, owner_choice=None,
               grants=None, synonyms=None, sync_columns=False, meta_target=None,
               create_schema=None, privs=None):
    """Monta os passos a executar (ordenados pelas dependências).

    statuses: lista de TableStatus (com `action` escolhida). owner_choice: {TABELA: OWNER} quando
    a tabela existe em mais de um schema. grants/synonyms: o que já existe (para não repetir).
    create_schema: None ou {'password', 'tablespace'} para criar o schema/usuário de destino.
    """
    target = check_ident(target_schema, "schema de destino")
    connected = (connected_user or "").upper()
    owner_choice = owner_choice or {}
    grants, synonyms = grants or {}, synonyms or {}
    by_name = {s.name: s for s in statuses}
    steps, notes = [], []

    privs = {p.upper() for p in privs} if privs is not None else None
    has = (lambda p: True) if privs is None else (lambda p: p in privs)
    if connected != target and not create_schema and not has("CREATE ANY TABLE"):
        notes.append(f"⚠ Conectado como {connected}: criar tabelas no schema {target} exige CREATE ANY TABLE "
                     f"(ou conecte diretamente como {target}).")
    if connected == target and not has("CREATE TABLE"):
        notes.append(f"⚠ O schema {target} não tem CREATE TABLE.")
    if create_schema and not has("CREATE USER"):
        notes.append(f"⚠ Criar o schema {target} exige o privilégio CREATE USER.")
    if create_schema:
        pwd, ts = create_schema.get("password", ""), create_schema.get("tablespace", "USERS")
        if not pwd or '"' in pwd or ";" in pwd:
            raise OracleError("Senha do novo schema ausente ou com caracteres inválidos (\" ;).")
        check_ident(ts, "tablespace")
        steps.append(Step("schema", target, f'CREATE USER {target} IDENTIFIED BY "{pwd}" DEFAULT TABLESPACE {ts} '
                                            f'QUOTA UNLIMITED ON {ts}',
                          "Cria o schema de destino", f"CREATE USER {target} IDENTIFIED BY \"********\" "
                                                      f"DEFAULT TABLESPACE {ts} QUOTA UNLIMITED ON {ts}", risky=True))
        steps.append(Step("schema", target, f"GRANT CREATE SESSION, CREATE TABLE, CREATE SYNONYM, CREATE VIEW, "
                                            f"CREATE SEQUENCE TO {target}", "Privilégios básicos do schema"))

    # 1) acesso às tabelas de outros schemas: GRANT + sinônimo
    for s in statuses:
        if s.action != "acessar" or s.state != "EM_OUTRO_SCHEMA":
            continue
        owner = (owner_choice.get(s.name) or s.owners[0]).upper()
        if owner == target:
            continue
        have = grants.get((owner, s.name), set())
        need = [p for p in TABLE_PRIVS if p not in have]
        if need:
            if connected not in (owner, "SYS", "SYSTEM") and not has("GRANT ANY OBJECT PRIVILEGE"):
                notes.append(f"⚠ GRANT em {owner}.{s.name}: conecte como {owner} ou use um usuário com "
                             f"GRANT ANY OBJECT PRIVILEGE.")
            steps.append(Step("grant", s.name, f"GRANT {', '.join(need)} ON {owner}.{s.name} TO {target}",
                              f"Concede acesso ao schema {target}"))
        if s.name not in synonyms:
            if connected != target and not has("CREATE ANY SYNONYM"):
                notes.append(f"⚠ Sinônimo {target}.{s.name}: exige CREATE ANY SYNONYM (ou conecte como {target}).")
            steps.append(Step("synonym", s.name, f"CREATE SYNONYM {target}.{s.name} FOR {owner}.{s.name}",
                              f"{target}.{s.name} passa a apontar para {owner}.{s.name}"))
        else:
            notes.append(f"Sinônimo {target}.{s.name} já existe -> {synonyms[s.name][0]}.{synonyms[s.name][1]}.")

    # 2) criação das tabelas novas (na ordem do script, que respeita as dependências)
    create_set = {s.name for s in statuses if s.action == "criar" and s.state in ("NOVA", "EM_OUTRO_SCHEMA")}
    skipped_alters = []
    for st in statements:
        t, kind = classify(st)
        if t is None:
            continue
        if t in create_set:
            steps.append(Step(kind, t, st))
        elif kind == "alter" and by_name.get(t) and by_name[t].action != "criar":
            skipped_alters.append((t, st))
    for t, st in skipped_alters:
        notes.append(f"{t} já existe: '{st[:70]}...' não foi aplicado (não alteramos tabelas existentes).")

    # 3) colunas que faltam em tabelas já existentes no destino (opcional, só adiciona)
    if sync_columns:
        for s in statuses:
            if s.state == "NO_DESTINO" and s.diff.get("missing"):
                cols = {c["name"]: c for c in expected[s.name]["columns"]}
                for cn in s.diff["missing"]:
                    steps.append(Step("addcol", s.name, f"ALTER TABLE {target}.{s.name} ADD ({strip_column_modifiers(cols[cn]['def'])})",
                                      "Adiciona coluna do modelo que não existe no Oracle"))
    return steps, notes


# ---------------------------------------------------------------- execução
def execute_steps(conn, steps, target_schema, connected_user, stop_on_error=True, log=None):
    """Executa os passos habilitados. Retorna lista de (Step, ok, mensagem). DDL do Oracle confirma sozinho."""
    target = check_ident(target_schema, "schema de destino")
    connected = (connected_user or "").upper()
    log = log or (lambda msg: None)
    results = []
    cur = conn.cursor()
    switched = connected == target      # já estamos no schema de destino

    def switch():
        nonlocal switched
        if not switched:
            cur.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {target}")
            switched = True
            log(f"Sessão apontada para o schema {target}")
    try:
        for step in steps:
            if not step.enabled:
                continue
            try:
                # passos de schema rodam como o usuário conectado; os demais já no schema de destino
                if step.kind != "schema":
                    switch()
                cur.execute(step.sql)
                results.append((step, True, "OK"))
                log(f"OK    {step.kind:8s} {step.table}")
            except Exception as ex:
                msg = _clean_ora(ex)
                results.append((step, False, msg))
                log(f"ERRO  {step.kind:8s} {step.table}: {msg}")
                if stop_on_error:
                    break
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return results


def render_script(steps, header=""):
    """Script SQL dos passos habilitados (senha mascarada) para revisar ou salvar."""
    out = [header.rstrip()] if header else []
    for s in steps:
        if s.enabled:
            out.append(f"-- [{s.kind}] {s.table}" + (f" — {s.note}" if s.note else ""))
            out.append(s.shown().rstrip(";") + ";")
    return "\n".join(out) + "\n"
