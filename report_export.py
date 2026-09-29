"""Exportação do modelo para relatório HTML e PDF com visual clean, moderno e suporte completo a Chen (Navathe 6ª Ed.)."""
import datetime
import html as _html


def esc(text):
    return _html.escape(str(text if text is not None else ""))


def _attr_type_badge(attr):
    if attr.is_pk:
        return '<span class="badge badge-pk" title="Chave Primária: Elipse simples com texto sublinhado sólido">🔑 Chave Primária (PK)</span>'
    if attr.is_partial_key:
        return '<span class="badge badge-partial" title="Chave Parcial / Discriminador: Elipse simples com sublinhado tracejado">🏷️ Chave Parcial</span>'
    if attr.is_multivalued:
        return '<span class="badge badge-multi" title="Multivalorado: Elipse dupla">⭕⭕ Multivalorado</span>'
    if attr.is_derived:
        return '<span class="badge badge-derived" title="Derivado: Elipse com borda tracejada">⚡ Derivado</span>'
    if attr.is_composite:
        subs = ", ".join(f"<code>{esc(s.name)}</code>" for s in attr.sub_attrs) if attr.sub_attrs else "sem sub-atributos"
        return f'<span class="badge badge-comp" title="Composto: Ramificado">🌳 Composto ({subs})</span>'
    return '<span class="badge badge-simple">Atômico / Simples</span>'


def _attr_type_text(attr):
    if attr.is_pk:
        return "Chave Primária (PK)"
    if attr.is_partial_key:
        return "Chave Parcial (Discriminador)"
    if attr.is_multivalued:
        return "Multivalorado"
    if attr.is_derived:
        return "Derivado"
    if attr.is_composite:
        subs = ", ".join(s.name for s in attr.sub_attrs) if attr.sub_attrs else "sem sub-atributos"
        return f"Composto ({subs})"
    return "Simples"


def build_html(project):
    now_str = datetime.datetime.now().strftime("%d/%m/%Y às %H:%M")
    total_entities = len(project.entities)
    total_rels = len(project.rels)
    weak_count = sum(1 for e in project.entities if e.is_weak)

    html = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Modelo de Dados — Notação de Chen (Navathe 6ª Edição)</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --surface: #ffffff;
      --border: #e2e8f0;
      --border-focus: #cbd5e1;
      --text: #0f172a;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-light: #eff6ff;
      --primary-border: #bfdbfe;
      --amber: #d97706;
      --amber-light: #fefce8;
      --amber-border: #fef08a;
      --slate: #334155;
      --radius: 8px;
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 32px 16px;
    }}

    .container {{
      max-width: 1040px;
      margin: 0 auto;
    }}

    /* Header / Hero */
    .hero {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 24px 28px;
      margin-bottom: 24px;
      box-shadow: var(--shadow-sm);
    }}
    .hero-title {{
      font-size: 22px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .hero-subtitle {{
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 16px;
    }}
    .meta-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .meta-pill {{
      font-size: 12px;
      padding: 4px 10px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 9999px;
      color: var(--slate);
      font-weight: 500;
    }}

    /* Section Headings */
    .section-header {{
      font-size: 16px;
      font-weight: 600;
      color: var(--slate);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin: 28px 0 14px 4px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    /* Cards */
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      margin-bottom: 18px;
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      transition: border-color 0.2s;
    }}
    .card:hover {{
      border-color: var(--border-focus);
    }}
    .card-header {{
      padding: 12px 18px;
      background: #fafbfc;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.02em;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    /* Badges */
    .badge {{
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.01em;
      white-space: nowrap;
    }}
    .badge-strong {{
      background: var(--primary-light);
      color: var(--primary);
      border: 1px solid var(--primary-border);
    }}
    .badge-weak {{
      background: var(--amber-light);
      color: var(--amber);
      border: 1px solid var(--amber-border);
    }}
    .badge-pk {{
      background: #eef2ff;
      color: #3730a3;
      border: 1px solid #c7d2fe;
    }}
    .badge-partial {{
      background: #fffbeb;
      color: #92400e;
      border: 1px dashed #fcd34d;
    }}
    .badge-multi {{
      background: #faf5ff;
      color: #6b21a8;
      border: 1px solid #e9d5ff;
    }}
    .badge-derived {{
      background: #f0fdf4;
      color: #166534;
      border: 1px dashed #86efac;
    }}
    .badge-comp {{
      background: #f8fafc;
      color: #334155;
      border: 1px solid #cbd5e1;
    }}
    .badge-simple {{
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #e2e8f0;
    }}
    .badge-total {{
      background: #fef2f2;
      color: #991b1b;
      border: 1px solid #fecaca;
    }}
    .badge-partial-part {{
      background: #f1f5f9;
      color: #475569;
      border: 1px solid #e2e8f0;
    }}

    /* Tables */
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 13px;
    }}
    th {{
      padding: 8px 16px;
      background: #f8fafc;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border);
    }}
    td {{
      padding: 10px 16px;
      border-bottom: 1px solid #f1f5f9;
      color: var(--text);
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    tr:hover td {{
      background-color: #fafbfc;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      background: #f1f5f9;
      padding: 1px 5px;
      border-radius: 4px;
      color: #0f172a;
    }}

    /* Rel Diagrammatic Row */
    .rel-flow {{
      padding: 14px 18px;
      display: flex;
      align-items: center;
      gap: 12px;
      background: #fdfefe;
      font-size: 13px;
      flex-wrap: wrap;
    }}
    .entity-box {{
      padding: 6px 14px;
      background: var(--surface);
      border: 1.5px solid #334155;
      border-radius: 4px;
      font-weight: 700;
      font-size: 12px;
      letter-spacing: 0.03em;
    }}
    .entity-box.weak {{
      border: 2px double #334155;
    }}
    .diamond-box {{
      padding: 5px 12px;
      background: var(--surface);
      border: 1.5px solid var(--amber);
      font-weight: 700;
      font-size: 11px;
      letter-spacing: 0.04em;
      transform: skewX(-12deg);
      border-radius: 3px;
    }}
    .diamond-box.identifying {{
      border: 2px double var(--amber);
    }}
    .diamond-box span {{
      display: inline-block;
      transform: skewX(12deg);
    }}
    .line-indicator {{
      display: flex;
      flex-direction: column;
      align-items: center;
      font-size: 11px;
      color: var(--text-muted);
    }}
    .line-bar {{
      height: 2px;
      background: #94a3b8;
      width: 48px;
      margin: 2px 0;
    }}
    .line-bar.double {{
      height: 4px;
      background: transparent;
      border-top: 2px solid #94a3b8;
      border-bottom: 2px solid #94a3b8;
    }}
    .rel-meta {{
      padding: 8px 18px 12px;
      font-size: 12px;
      color: var(--text-muted);
      border-top: 1px solid #f1f5f9;
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
    }}

    @media print {{
      body {{ padding: 0; background: white; }}
      .card {{ box-shadow: none; break-inside: avoid; }}
      .hero {{ box-shadow: none; border-color: #ccc; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="hero">
      <h1 class="hero-title">
        <span>📐 Modelo de Dados Conceitual</span>
        <span class="badge badge-strong">Chen / EER</span>
      </h1>
      <p class="hero-subtitle">
        Conforme a especificação formal de <em>Sistemas de Banco de Dados</em> (Ramez Elmasri &amp; Shamkant B. Navathe, 6ª Edição).
      </p>
      <div class="meta-pills">
        <span class="meta-pill">🏛 <strong>{total_entities}</strong> Entidades ({weak_count} fraca)</span>
        <span class="meta-pill">🔗 <strong>{total_rels}</strong> Relacionamentos</span>
        <span class="meta-pill">△ <strong>{len(project.specs)}</strong> Especializações/Categorias</span>
        <span class="meta-pill">📚 <strong>{len(project.domains)}</strong> Domínios</span>
        <span class="meta-pill">🕒 Gerado em: {now_str}</span>
      </div>
    </div>

    <div class="section-header">
      <span>🏛 Tipos de Entidade e Atributos</span>
    </div>
"""

    for e in project.entities:
        tipo_badge = '<span class="badge badge-weak">Entidade Fraca (Retângulo Duplo)</span>' if e.is_weak else '<span class="badge badge-strong">Entidade Regular / Forte</span>'
        html += f"""
    <div class="card">
      <div class="card-header">
        <div class="card-title">
          <span>{esc(e.name)}</span>
        </div>
        {tipo_badge}
      </div>
      {('<div style="padding:8px 18px;font-size:13px;color:#475569;border-bottom:1px solid #f1f5f9;">' + esc(e.description) + '</div>') if e.description else ''}
      <table>
        <thead>
          <tr>
            <th>Atributo</th>
            <th>Tipo de Dado</th>
            <th>Notação de Chen (Navathe)</th>
            <th>Restrição</th>
            <th>Domínio</th>
            <th>Descrição</th>
          </tr>
        </thead>
        <tbody>
"""
        for a in e.attrs:
            badge_html = _attr_type_badge(a)
            not_null_html = '<span style="color:#15803d;font-weight:600;">NOT NULL</span>' if a.nn else '<span style="color:#94a3b8;">NULL</span>'
            dom = project.find_domain(a.domain_id) if a.domain_id else None
            dom_html = f'<span class="badge badge-multi" title="{esc(dom.summary())}">{esc(dom.name)}</span>' if dom else '<span style="color:#94a3b8;">—</span>'
            uq = ' <span class="badge badge-simple">ÚNICO</span>' if a.unique else ''
            html += f"""
          <tr>
            <td><code>{esc(a.name)}</code></td>
            <td><code>{esc(a.type)}</code></td>
            <td>{badge_html}</td>
            <td>{not_null_html}{uq}</td>
            <td>{dom_html}</td>
            <td>{esc(a.description) or '<span style="color:#94a3b8;">—</span>'}</td>
          </tr>
"""
        html += """
        </tbody>
      </table>
    </div>
"""

    html += """
    <div class="section-header">
      <span>🔗 Tipos de Relacionamento e Restrições</span>
    </div>
"""

    for r in project.rels:
        e1 = project.find_entity(r.entity1_id)
        e2 = project.find_entity(r.entity2_id)
        if not e1 or not e2:
            continue
        if r.is_nary:
            names = [project.find_entity(i) for i, *_ in r.participants()]
            flow = " ".join(f'<div class="entity-box">{esc(x.name)}</div>' for x in names if x)
            html += f"""
    <div class="card">
      <div class="card-header"><div class="card-title"><span>{esc(r.name)}</span>
        <span class="badge badge-comp">{len(names)}-ário</span></div><span class="badge badge-simple">Relacionamento n-ário</span></div>
      <div class="rel-flow">{flow}</div>
      {('<div class="rel-meta">' + esc(r.description) + '</div>') if r.description else ''}
    </div>
"""
            continue

        ident_badge = '<span class="badge badge-weak">Identificador (Losango Duplo)</span>' if r.is_identifying else '<span class="badge badge-simple">Regular</span>'
        diamond_class = "diamond-box identifying" if r.is_identifying else "diamond-box"
        e1_class = "entity-box weak" if e1.is_weak else "entity-box"
        e2_class = "entity-box weak" if e2.is_weak else "entity-box"

        p1_bar = "line-bar double" if r.part1 == "total" else "line-bar"
        p2_bar = "line-bar double" if r.part2 == "total" else "line-bar"

        p1_lbl = "Total (dupla)" if r.part1 == "total" else "Parcial"
        p2_lbl = "Total (dupla)" if r.part2 == "total" else "Parcial"

        r1_txt = f"{r.role1} " if r.role1 else ""
        r2_txt = f"{r.role2} " if r.role2 else ""

        html += f"""
    <div class="card">
      <div class="card-header">
        <div class="card-title">
          <span>{esc(r.name)}</span>
          <span class="badge badge-comp">Razão: {r.card1}:{r.card2}</span>
        </div>
        {ident_badge}
      </div>
      <div class="rel-flow">
        <div class="{e1_class}">{esc(e1.name)}</div>
        <div class="line-indicator">
          <span>{r1_txt}({r.card1})</span>
          <div class="{p1_bar}"></div>
          <span>{p1_lbl}</span>
        </div>
        <div class="{diamond_class}">
          <span>{esc(r.name)}</span>
        </div>
        <div class="line-indicator">
          <span>{r2_txt}({r.card2})</span>
          <div class="{p2_bar}"></div>
          <span>{p2_lbl}</span>
        </div>
        <div class="{e2_class}">{esc(e2.name)}</div>
      </div>
"""
        if r.attrs:
            attrs_badges = " ".join(f'<span class="badge badge-simple" title="{esc(a.description)}"><code>{esc(a.name)}: {esc(a.type)}</code></span>' for a in r.attrs)
            html += f"""
      <div class="rel-meta">
        <span><strong>Atributos do Relacionamento:</strong> {attrs_badges}</span>
      </div>
"""
        html += """
    </div>
"""

    if project.specs:
        html += """
    <div class="section-header"><span>△ Especialização, Generalização e Categorias (EER)</span></div>
    <div class="card"><table><thead><tr><th>Tipo</th><th>Superclasse(s)</th><th>Subclasse(s)</th><th>Restrições</th><th>Definidor</th></tr></thead><tbody>
"""
        for sp in project.specs:
            par = ", ".join(esc(x.name) for x in (project.find_entity(i) for i in sp.parent_ids) if x)
            kid = ", ".join(esc(x.name) for x in (project.find_entity(i) for i in sp.child_ids) if x)
            restr = ("união (u)" if sp.is_union else ("disjunta (d)" if sp.disjointness == "d" else "sobreposta (o)")) + \
                ", " + ("total" if sp.completeness == "total" else "parcial")
            html += f"<tr><td>{esc(sp.label())}</td><td>{par}</td><td>{kid}</td><td>{restr}</td><td>{esc(sp.defining_attr) or '—'}</td></tr>\n"
        html += "</tbody></table></div>\n"

    if project.domains:
        html += """
    <div class="section-header"><span>📚 Domínios de Valores</span></div>
"""
        for d in project.domains:
            used = [f"{e.name}.{a.name}" for e in project.entities for a in e.attrs if a.domain_id == d.id]
            used += [f"{r.name}.{a.name}" for r in project.rels for a in r.attrs if a.domain_id == d.id]
            rows = "".join(f"<tr><td><code>{esc(v['code'])}</code></td><td>{esc(v['label'])}</td><td>{esc(v.get('description', ''))}</td></tr>"
                           for v in d.values)
            html += f"""
    <div class="card">
      <div class="card-header"><div class="card-title"><span>{esc(d.name)}</span><span class="badge badge-comp">{esc(d.type)}</span></div>
        <span class="badge badge-simple">{'Tabela de domínio' if d.impl == 'lookup' else 'CHECK'}</span></div>
      {('<div class="rel-meta">' + esc(d.description) + '</div>') if d.description else ''}
      <table><thead><tr><th>Código</th><th>Descrição</th><th>Detalhamento</th></tr></thead><tbody>{rows}</tbody></table>
      <div class="rel-meta"><span><strong>Usado em:</strong> {esc(', '.join(used)) or 'nenhum atributo'}</span></div>
    </div>
"""

    html += """
  </div>
</body>
</html>
"""
    return html


def export_html(project, path):
    html = build_html(project)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def export_pdf(project, path):
    """Exporta um relatório em PDF (texto, com quebra de linha). Requer reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("Para exportar em PDF instale a dependência opcional: pip install reportlab") from exc

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    left, right = 2 * cm, width - 2 * cm
    state = {"y": height - 2 * cm}

    def ensure(space):
        if state["y"] < 2 * cm + space:
            c.showPage()
            state["y"] = height - 2 * cm

    def line(text, x=left, font="Helvetica", size=9, gap=0.42):
        """Escreve `text` quebrando em várias linhas para caber na página."""
        c.setFont(font, size)
        words, cur = str(text).split(), ""
        rows = []
        for w in words:
            trial = (cur + " " + w).strip()
            if stringWidth(trial, font, size) <= right - x:
                cur = trial
            else:
                rows.append(cur)
                cur = w
        rows.append(cur)
        for r in rows:
            ensure(gap * cm)
            c.setFont(font, size)
            c.drawString(x, state["y"], r)
            state["y"] -= gap * cm

    line("Modelo de Dados — Chen / EER (Navathe 6ª Ed.)", font="Helvetica-Bold", size=16, gap=0.8)
    line(f"Entidades: {len(project.entities)} | Relacionamentos: {len(project.rels)} | "
         f"Especializações: {len(project.specs)} | Domínios: {len(project.domains)}", gap=0.8)

    line("Entidades e Atributos", font="Helvetica-Bold", size=12, gap=0.6)
    for e in project.entities:
        ensure(2 * cm)
        line(f"{e.name}{' [Entidade Fraca]' if e.is_weak else ' [Entidade Forte]'}", font="Helvetica-Bold", size=11, gap=0.5)
        if e.description:
            line(e.description, x=left + 0.3 * cm, font="Helvetica-Oblique", size=9)
        for a in e.attrs:
            dom = project.find_domain(a.domain_id) if a.domain_id else None
            txt = f"• {a.name} ({a.type}) — {_attr_type_text(a)}" + (" [NOT NULL]" if a.nn else "") + \
                  (" [ÚNICO]" if a.unique else "") + (f" [Domínio: {dom.name}]" if dom else "")
            line(txt, x=left + 0.5 * cm)
            if a.description:
                line(a.description, x=left + 1.0 * cm, font="Helvetica-Oblique", size=8, gap=0.38)
        state["y"] -= 0.3 * cm

    line("Relacionamentos", font="Helvetica-Bold", size=12, gap=0.6)
    for r in project.rels:
        parts = []
        for eid, card, part, role in r.participants():
            ent = project.find_entity(eid)
            if ent:
                parts.append(f"{ent.name} (card: {card}, part: {part}" + (f", papel: {role}" if role else "") + ")")
        if len(parts) < 2:
            continue
        kind = f"{len(parts)}-ário" if r.is_nary else f"{r.card1}:{r.card2}"
        line(f"• {r.name}{' [Identificador]' if r.is_identifying else ''} ({kind})", font="Helvetica-Bold", size=10, gap=0.45)
        line("Ligação: " + "  <-->  ".join(parts), x=left + 0.5 * cm)
        if r.description:
            line(r.description, x=left + 0.5 * cm, font="Helvetica-Oblique", size=8, gap=0.38)
        if r.attrs:
            line("Atributos: " + ", ".join(f"{a.name}: {a.type}" for a in r.attrs), x=left + 0.5 * cm)
        state["y"] -= 0.25 * cm

    if project.specs:
        line("Especialização / Generalização / Categorias", font="Helvetica-Bold", size=12, gap=0.6)
        for sp in project.specs:
            par = ", ".join(x.name for x in (project.find_entity(i) for i in sp.parent_ids) if x)
            kid = ", ".join(x.name for x in (project.find_entity(i) for i in sp.child_ids) if x)
            restr = "união" if sp.is_union else ("disjunta" if sp.disjointness == "d" else "sobreposta")
            line(f"• {sp.label()}: {par} -> {kid} ({restr}, {'total' if sp.completeness == 'total' else 'parcial'})"
                 + (f", definidor: {sp.defining_attr}" if sp.defining_attr else ""), x=left + 0.3 * cm)
        state["y"] -= 0.3 * cm

    if project.domains:
        line("Domínios de Valores", font="Helvetica-Bold", size=12, gap=0.6)
        for d in project.domains:
            line(f"{d.name} ({d.type}) — {'tabela de domínio' if d.impl == 'lookup' else 'CHECK'}", font="Helvetica-Bold", size=10, gap=0.45)
            for v in d.values:
                line(f"{v['code']} = {v['label']}" + (f" — {v['description']}" if v.get("description") else ""), x=left + 0.5 * cm)
            state["y"] -= 0.2 * cm
    c.save()
