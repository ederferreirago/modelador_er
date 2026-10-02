"""Validação do modelo ER: aponta problemas de modelagem antes de gerar o DDL."""

ERROR, WARN, INFO = "erro", "aviso", "info"


def validate_project(project):
    """Retorna lista de (severidade, mensagem) ordenada por gravidade."""
    issues = []
    add = lambda sev, msg: issues.append((sev, msg))

    names = {}
    for e in project.entities:
        names.setdefault(e.name.strip().lower(), []).append(e)
    for n, lst in names.items():
        if len(lst) > 1:
            add(ERROR, f"Nome de entidade duplicado: '{lst[0].name}' ({len(lst)} vezes).")

    child_ids = {i for sp in project.specs for i in sp.child_ids}
    identifying = [r for r in project.rels if r.is_identifying and not r.is_nary]

    for e in project.entities:
        if not e.attrs:
            add(ERROR, f"Entidade '{e.name}' não tem atributos.")
            continue
        attr_names = {}
        for a in e.attrs:
            attr_names.setdefault(a.name.strip().lower(), 0)
            attr_names[a.name.strip().lower()] += 1
        for n, cnt in attr_names.items():
            if cnt > 1:
                add(ERROR, f"Entidade '{e.name}': atributo '{n}' duplicado.")
        if e.id not in child_ids:
            if e.is_weak:
                if not e.partial_key_attrs():
                    add(WARN, f"Entidade fraca '{e.name}' sem chave parcial (discriminador).")
                if not any(e.id in (r.entity1_id, r.entity2_id) for r in identifying):
                    add(ERROR, f"Entidade fraca '{e.name}' sem relacionamento identificador (losango duplo).")
            elif not e.pk_attrs():
                add(ERROR, f"Entidade forte '{e.name}' sem chave primária.")
            elif len(e.pk_attrs()) > 1:
                add(INFO, f"'{e.name}': {len(e.pk_attrs())} atributos marcados como chave formam uma chave "
                          f"composta. Se forem chaves candidatas independentes, marque apenas uma como "
                          f"chave e as demais como 'Único'.")
        for a in e.attrs:
            if a.is_composite and not a.sub_attrs:
                add(WARN, f"'{e.name}.{a.name}' é composto mas não tem sub-atributos.")
            if a.domain_id and not project.find_domain(a.domain_id):
                add(ERROR, f"'{e.name}.{a.name}' referencia um domínio inexistente.")
        missing = [a.name for a in e.attrs if not a.description and not a.is_derived]
        if missing:
            add(INFO, f"'{e.name}': {len(missing)} atributo(s) sem descrição (o DDL não terá COMMENT): "
                      f"{', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}.")

    for r in project.rels:
        for eid, *_ in r.participants():
            if not project.find_entity(eid):
                add(ERROR, f"Relacionamento '{r.name}' aponta para entidade inexistente.")
                break
        if r.is_identifying:
            weak = [project.find_entity(i) for i in (r.entity1_id, r.entity2_id)]
            if not any(w and w.is_weak for w in weak):
                add(WARN, f"Relacionamento identificador '{r.name}' não liga nenhuma entidade fraca.")
        if r.is_nary and len(r.participants()) < 3:
            add(WARN, f"Relacionamento '{r.name}' n-ário com menos de 3 participantes.")
        if r.entity1_id == r.entity2_id and not (r.role1 and r.role2) and not r.is_nary:
            add(INFO, f"Auto-relacionamento '{r.name}' sem papéis (roles) definidos.")

    linked = set()
    for r in project.rels:
        linked.update(eid for eid, *_ in r.participants())
    for sp in project.specs:
        linked.update(sp.parent_ids)
        linked.update(sp.child_ids)
    for e in project.entities:
        if e.id not in linked and len(project.entities) > 1:
            add(WARN, f"Entidade '{e.name}' isolada (sem relacionamentos nem especialização).")

    for sp in project.specs:
        if sp.is_union and len(sp.parent_ids) < 2:
            add(WARN, f"Categoria (união) precisa de 2 ou mais superclasses.")
        if not sp.is_union and len(sp.parent_ids) != 1:
            add(ERROR, f"{sp.label()} deve ter exatamente 1 superclasse.")
        if not sp.child_ids:
            add(ERROR, f"{sp.label()} sem subclasses.")
        if sp.defining_attr:
            par = project.find_entity(sp.parent_ids[0]) if sp.parent_ids else None
            if par and not any(a.name == sp.defining_attr for a in par.attrs):
                add(WARN, f"Atributo definidor '{sp.defining_attr}' não existe na superclasse '{par.name}'.")
    seen_child = {}
    for sp in project.specs:
        if sp.is_union:
            continue
        for cid in sp.child_ids:
            if cid in seen_child:
                ent = project.find_entity(cid)
                add(WARN, f"'{ent.name if ent else cid}' é subclasse em mais de uma especialização "
                          f"(herança múltipla não é mapeada no DDL).")
            seen_child[cid] = sp.id

    for d in project.domains:
        if not d.values:
            add(WARN, f"Domínio '{d.name}' sem valores.")
        codes = [v["code"] for v in d.values]
        if len(set(codes)) != len(codes):
            add(ERROR, f"Domínio '{d.name}' com códigos repetidos.")
        if d.is_numeric():
            for v in d.values:
                try:
                    float(v["code"])
                except ValueError:
                    add(ERROR, f"Domínio '{d.name}': código '{v['code']}' não é numérico.")
        if not any(a.domain_id == d.id for a in project.iter_attributes()):
            add(INFO, f"Domínio '{d.name}' não é usado por nenhum atributo.")

    for arc in getattr(project, "arcs", []):
        ent = project.find_entity(arc.entity_id)
        arc_lbl = f"Arco '{arc.name}'" if arc.name else "Arco de exclusividade"
        if not ent:
            add(ERROR, f"{arc_lbl} aponta para entidade inexistente.")
            continue
        if len(arc.rel_ids) < 2:
            add(ERROR, f"{arc_lbl} na entidade '{ent.name}' deve ter pelo menos 2 relacionamentos.")
        for rid in arc.rel_ids:
            rel = project.find_rel(rid)
            if not rel:
                add(ERROR, f"{arc_lbl} em '{ent.name}' referencia relacionamento inexistente.")
            elif ent.id not in (rel.entity1_id, rel.entity2_id):
                add(ERROR, f"{arc_lbl} em '{ent.name}': relacionamento '{rel.name}' não envolve a entidade.")
            elif rel.is_nary or (rel.card1.upper() in ("M", "N") and rel.card2.upper() in ("M", "N")):
                add(WARN, f"{arc_lbl} em '{ent.name}': relacionamento '{rel.name}' é M:N ou N-ário "
                          f"(arcos no Oracle Designer requerem chave estrangeira direta).")

    order = {ERROR: 0, WARN: 1, INFO: 2}
    issues.sort(key=lambda x: order[x[0]])
    return issues
