"""Modelos de dados do Modelador ER - Padrão Notação de Chen (Navathe 6ª Edição)."""
import json
import uuid


def new_id(prefix="e"):
    # Evita colisões ao criar objetos depois de reabrir um projeto salvo.
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Attribute:
    """Atributo conforme a notação de Chen (Elmasri & Navathe 6ª Ed.).
    
    Tipos de atributo (attr_type):
    - "simple": atributo atômico / simples (elipse simples)
    - "pk": atributo chave / identificador (elipse simples com texto sublinhado contínuo)
    - "partial_key": chave parcial / discriminador de entidade fraca (elipse com sublinhado tracejado)
    - "multivalued": atributo multivalorado (elipse dupla)
    - "derived": atributo derivado (elipse com borda tracejada)
    - "composite": atributo composto (elipse que se ramifica em sub-atributos)
    """

    def __init__(self, name="novo_campo", type="STRING", pk=False, nn=False,
                 attr_type=None, sub_attrs=None, id=None, offset_x=None, offset_y=None,
                 description="", domain_id=None, unique=False):
        self.id = id or new_id("a")
        self.name = name
        self.type = type
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.description = description or ""   # descrição do atributo (vai para o DDL como COMMENT)
        self.domain_id = domain_id             # id de um Domain do projeto (valores definidos pelo usuário)
        self.unique = bool(unique)             # chave candidata / restrição UNIQUE

        # Determina o tipo de atributo Chen
        if attr_type is not None:
            self.attr_type = attr_type
        elif pk:
            self.attr_type = "pk"
        else:
            self.attr_type = "simple"

        self.nn = nn or (self.attr_type == "pk")
        self.sub_attrs = [
            a if isinstance(a, Attribute) else Attribute.from_dict(a)
            for a in (sub_attrs or [])
        ]

    @property
    def pk(self):
        return self.attr_type == "pk"

    @pk.setter
    def pk(self, value):
        if value:
            self.attr_type = "pk"
            self.nn = True
        elif self.attr_type == "pk":
            self.attr_type = "simple"

    @property
    def is_pk(self):
        return self.attr_type == "pk"

    @property
    def is_partial_key(self):
        return self.attr_type == "partial_key"

    @property
    def is_multivalued(self):
        return self.attr_type == "multivalued"

    @property
    def is_derived(self):
        return self.attr_type == "derived"

    @property
    def is_composite(self):
        return self.attr_type == "composite"

    def to_dict(self):
        d = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "pk": self.pk,
            "nn": self.nn,
            "attr_type": self.attr_type,
            "sub_attrs": [a.to_dict() for a in self.sub_attrs],
        }
        if self.description:
            d["description"] = self.description
        if self.domain_id:
            d["domain_id"] = self.domain_id
        if self.unique:
            d["unique"] = True
        if self.offset_x is not None:
            d["offset_x"] = self.offset_x
        if self.offset_y is not None:
            d["offset_y"] = self.offset_y
        return d

    @staticmethod
    def from_dict(d):
        return Attribute(
            name=d.get("name", "novo_campo"),
            type=d.get("type", "STRING"),
            pk=d.get("pk", False),
            nn=d.get("nn", False),
            attr_type=d.get("attr_type"),
            sub_attrs=d.get("sub_attrs"),
            id=d.get("id"),
            offset_x=d.get("offset_x"),
            offset_y=d.get("offset_y"),
            description=d.get("description", ""),
            domain_id=d.get("domain_id"),
            unique=d.get("unique", False),
        )


class Domain:
    """Domínio de valores definido pelo usuário (ex.: Status: 1-Atualizado, 2-Desatualizado, 3-Fechado).

    - type: tipo de dado do código (INTEGER, STRING, ...).
    - impl: como o DDL implementa o domínio:
        "lookup" -> tabela de domínio `dom_<nome>` + FK + INSERTs dos valores;
        "check"  -> apenas CHECK (coluna IN (...)).
    - values: lista de dicts {"code": "1", "label": "Atualizado", "description": "..."}.
    """

    def __init__(self, name="novo_dominio", type="INTEGER", impl="lookup", description="",
                 values=None, id=None):
        self.id = id or new_id("d")
        self.name = name
        self.type = type
        self.impl = impl if impl in ("lookup", "check") else "lookup"
        self.description = description or ""
        self.values = [dict(code=str(v.get("code", "")), label=str(v.get("label", "")),
                            description=str(v.get("description", "")))
                       for v in (values or [])]

    def is_numeric(self):
        return self.type in ("INTEGER", "DECIMAL")

    def sql_literal(self, code):
        """Literal SQL de um código do domínio (aspas apenas para tipos não numéricos)."""
        code = str(code).strip()
        if self.is_numeric():
            return code
        return "'" + code.replace("'", "''") + "'"

    def summary(self):
        return "; ".join(f"{v['code']} = {v['label']}" for v in self.values)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "type": self.type, "impl": self.impl,
                "description": self.description, "values": [dict(v) for v in self.values]}

    @staticmethod
    def from_dict(d):
        return Domain(name=d.get("name", "dominio"), type=d.get("type", "INTEGER"),
                      impl=d.get("impl", "lookup"), description=d.get("description", ""),
                      values=d.get("values"), id=d.get("id"))


class Entity:
    """Entidade na notação de Chen (Navathe 6ª Ed.).
    
    Pode ser Regular / Forte (retângulo simples) ou Fraca (retângulo duplo).
    """

    def __init__(self, name="NovaEntidade", x=40, y=40, attrs=None, is_weak=False, attr_spacing=85.0, id=None,
                 kimball_role=None, scd_type="scd1", description=""):
        self.id = id or new_id("e")
        self.name = name
        self.x = x
        self.y = y
        self.is_weak = is_weak
        self.attr_spacing = attr_spacing
        self.attrs = attrs if attrs is not None else [Attribute("id", "INTEGER", pk=True)]
        self.description = description or ""

        # Modelagem dimensional (Kimball): papel do "entidade" no esquema estrela.
        # kimball_role: None (não classificado), "fact" (tabela fato) ou "dimension" (tabela dimensão)
        # scd_type: "scd1" (sobrescreve histórico) ou "scd2" (mantém histórico com versionamento)
        self.kimball_role = kimball_role if kimball_role in ("fact", "dimension") else None
        self.scd_type = scd_type if scd_type in ("scd1", "scd2") else "scd1"

    def pk_attrs(self):
        return [a for a in self.attrs if a.is_pk]

    def partial_key_attrs(self):
        return [a for a in self.attrs if a.is_partial_key]

    def pk_attr(self):
        pks = self.pk_attrs()
        if pks:
            return pks[0]
        partials = self.partial_key_attrs()
        if partials:
            return partials[0]
        return self.attrs[0] if self.attrs else None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "is_weak": self.is_weak,
            "attr_spacing": self.attr_spacing,
            "attrs": [a.to_dict() for a in self.attrs],
            "kimball_role": self.kimball_role,
            "scd_type": self.scd_type,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d):
        return Entity(
            name=d.get("name", "NovaEntidade"),
            x=d.get("x", 40),
            y=d.get("y", 40),
            attrs=[Attribute.from_dict(a) for a in d.get("attrs", [])],
            is_weak=d.get("is_weak", False),
            attr_spacing=d.get("attr_spacing", 85.0),
            id=d.get("id"),
            kimball_role=d.get("kimball_role"),
            scd_type=d.get("scd_type", "scd1"),
            description=d.get("description", ""),
        )

    def measure_attrs(self):
        """Atributos numéricos não-chave: candidatos naturais a métricas de fato (Kimball)."""
        return [a for a in self.attrs if not a.is_pk and not a.is_partial_key and not a.is_multivalued
                and a.type in ("INTEGER", "DECIMAL")]


class Relationship:
    """Relacionamento na notação de Chen (Navathe 6ª Ed.).
    
    Elementos suportados:
    - Losango simples (relacionamento regular)
    - Losango duplo (relacionamento identificador de entidade fraca: is_identifying=True)
    - Cardinalidades: 1:1, 1:N, N:1, M:N (card1 e card2)
    - Restrições de participação: Parcial (linha simples) ou Total (linha dupla) para cada lado
    - Papéis (Roles) nas linhas para auto-relacionamentos ou relacionamentos com papéis explícitos
    - Atributos próprios do relacionamento (ex: Horas, Data_inicio)
    - Posição livre personalizável (pos_x, pos_y)
    """

    def __init__(self, one_id=None, many_id=None, kind="1N", name="relaciona",
                 entity1_id=None, entity2_id=None, card1=None, card2=None,
                 part1="partial", part2="partial", role1="", role2="",
                 is_identifying=False, attrs=None, pos_x=None, pos_y=None, id=None,
                 description="", extra_parts=None):
        self.id = id or new_id("r")
        self.description = description or ""
        # Participantes adicionais (relacionamento n-ário / ternário). Cada item:
        # {"entity_id", "card", "part", "role"}. Vazio => relacionamento binário.
        self.extra_parts = [dict(entity_id=p.get("entity_id"), card=p.get("card", "N"),
                                 part=p.get("part", "partial"), role=p.get("role", ""))
                            for p in (extra_parts or [])]
        self.name = name
        
        # Suporte bidirecional para entity1_id / entity2_id e legados one_id / many_id
        self.entity1_id = entity1_id if entity1_id is not None else one_id
        self.entity2_id = entity2_id if entity2_id is not None else many_id
        
        # Cardinalidade Chen: card1 e card2 ("1", "N", "M")
        if card1 is not None and card2 is not None:
            self.card1 = str(card1)
            self.card2 = str(card2)
        elif kind == "NN":
            self.card1 = "M"
            self.card2 = "N"
        elif kind == "11":
            self.card1 = "1"
            self.card2 = "1"
        elif kind == "N1":
            self.card1 = "N"
            self.card2 = "1"
        else: # "1N"
            self.card1 = "1"
            self.card2 = "N"

        # Participação: "partial" (linha simples) ou "total" (linha dupla)
        self.part1 = part1  # lado entity1
        self.part2 = part2  # lado entity2

        # Papéis nas conexões (ex: "Supervisor", "Supervisionado")
        self.role1 = role1
        self.role2 = role2

        # Relacionamento identificador de entidade fraca (losango duplo)
        self.is_identifying = is_identifying

        # Atributos do próprio relacionamento (ex: Horas, Data_inicio)
        self.attrs = [
            a if isinstance(a, Attribute) else Attribute.from_dict(a)
            for a in (attrs or [])
        ]

        # Posição personalizada do losango (se None, calculado dinamicamente)
        self.pos_x = pos_x
        self.pos_y = pos_y

    @property
    def is_nary(self):
        return bool(self.extra_parts)

    def participants(self):
        """Todos os participantes [(entity_id, card, part, role)], incluindo os extras (n-ário)."""
        base = [(self.entity1_id, self.card1, self.part1, self.role1),
                (self.entity2_id, self.card2, self.part2, self.role2)]
        base += [(p["entity_id"], p["card"], p["part"], p["role"]) for p in self.extra_parts]
        return base

    @property
    def one_id(self):
        return self.entity1_id

    @one_id.setter
    def one_id(self, val):
        self.entity1_id = val

    @property
    def many_id(self):
        return self.entity2_id

    @many_id.setter
    def many_id(self, val):
        self.entity2_id = val

    @property
    def kind(self):
        if self.card1 == "1" and self.card2 == "1":
            return "11"
        if self.card1 == "1" and self.card2 in ("N", "M"):
            return "1N"
        if self.card1 in ("N", "M") and self.card2 == "1":
            return "N1"
        return "NN"

    @kind.setter
    def kind(self, val):
        if val == "11":
            self.card1, self.card2 = "1", "1"
        elif val == "1N":
            self.card1, self.card2 = "1", "N"
        elif val == "N1":
            self.card1, self.card2 = "N", "1"
        else:
            self.card1, self.card2 = "M", "N"

    def to_dict(self):
        d = {
            "id": self.id,
            "name": self.name,
            "entity1_id": self.entity1_id,
            "entity2_id": self.entity2_id,
            # Campos legados para compatibilidade
            "one_id": self.entity1_id,
            "many_id": self.entity2_id,
            "kind": self.kind,
            # Novos campos Chen Navathe
            "card1": self.card1,
            "card2": self.card2,
            "part1": self.part1,
            "part2": self.part2,
            "role1": self.role1,
            "role2": self.role2,
            "is_identifying": self.is_identifying,
            "attrs": [a.to_dict() for a in self.attrs]
        }
        if self.description:
            d["description"] = self.description
        if self.extra_parts:
            d["extra_parts"] = [dict(p) for p in self.extra_parts]
        if self.pos_x is not None:
            d["pos_x"] = self.pos_x
        if self.pos_y is not None:
            d["pos_y"] = self.pos_y
        return d

    @staticmethod
    def from_dict(d):
        e1 = d.get("entity1_id", d.get("one_id"))
        e2 = d.get("entity2_id", d.get("many_id"))
        return Relationship(
            entity1_id=e1,
            entity2_id=e2,
            one_id=e1,
            many_id=e2,
            kind=d.get("kind", "1N"),
            name=d.get("name", "relaciona"),
            card1=d.get("card1"),
            card2=d.get("card2"),
            part1=d.get("part1", "partial"),
            part2=d.get("part2", "partial"),
            role1=d.get("role1", ""),
            role2=d.get("role2", ""),
            is_identifying=d.get("is_identifying", False),
            attrs=[Attribute.from_dict(a) for a in d.get("attrs", [])],
            pos_x=d.get("pos_x"),
            pos_y=d.get("pos_y"),
            id=d.get("id"),
            description=d.get("description", ""),
            extra_parts=d.get("extra_parts"),
        )


class Specialization:
    """Especialização / Generalização / Categoria (união) — Modelo ER Estendido (EER), Navathe cap. 8.

    - kind: "specialization" (top-down), "generalization" (bottom-up; mesma forma gráfica) ou
      "union" (categoria: subclasse que é união de superclasses distintas).
    - parent_ids: superclasse(s) (mais de uma somente em "union").
    - child_ids: subclasse(s) (uma só em "union").
    - disjointness: "d" (disjunta) ou "o" (sobreposta); em "union" é sempre "u".
    - completeness: "total" (linha dupla) ou "partial" (linha simples).
    - defining_attr: atributo definidor (especialização definida por atributo) — rótulo na linha.
    - mapping: "multi" (tabela por classe, Navathe 8A) ou "single" (tabela única, 8C/8D).
    """

    def __init__(self, kind="specialization", parent_ids=None, child_ids=None, disjointness="d",
                 completeness="partial", defining_attr="", mapping="multi", pos_x=None, pos_y=None,
                 description="", id=None):
        self.id = id or new_id("s")
        self.kind = kind if kind in ("specialization", "generalization", "union") else "specialization"
        self.parent_ids = list(parent_ids or [])
        self.child_ids = list(child_ids or [])
        self.disjointness = "u" if self.kind == "union" else (disjointness if disjointness in ("d", "o") else "d")
        self.completeness = completeness if completeness in ("total", "partial") else "partial"
        self.defining_attr = defining_attr or ""
        self.mapping = mapping if mapping in ("multi", "single") else "multi"
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.description = description or ""

    @property
    def is_union(self):
        return self.kind == "union"

    def label(self):
        return {"specialization": "Especialização", "generalization": "Generalização",
                "union": "Categoria (União)"}[self.kind]

    def to_dict(self):
        d = {"id": self.id, "kind": self.kind, "parent_ids": list(self.parent_ids),
             "child_ids": list(self.child_ids), "disjointness": self.disjointness,
             "completeness": self.completeness, "defining_attr": self.defining_attr,
             "mapping": self.mapping, "description": self.description}
        if self.pos_x is not None:
            d["pos_x"] = self.pos_x
        if self.pos_y is not None:
            d["pos_y"] = self.pos_y
        return d

    @staticmethod
    def from_dict(d):
        return Specialization(kind=d.get("kind", "specialization"), parent_ids=d.get("parent_ids"),
                              child_ids=d.get("child_ids"), disjointness=d.get("disjointness", "d"),
                              completeness=d.get("completeness", "partial"),
                              defining_attr=d.get("defining_attr", ""), mapping=d.get("mapping", "multi"),
                              pos_x=d.get("pos_x"), pos_y=d.get("pos_y"),
                              description=d.get("description", ""), id=d.get("id"))


class RelationshipArc:
    """Arco de Relacionamento (Relationship Arc / Exclusive Arc) — Notação de Barker & Oracle Designer.

    No Oracle Designer / Notação de Barker, um arco agrupa dois ou mais relacionamentos pertencentes
    à mesma entidade (âncora).
    Indica exclusividade mútua (XOR): uma ocorrência da entidade só pode participar de no máximo
    um (se opcional) ou exatamente um (se obrigatório/mandatory) dos relacionamentos do arco.
    """

    def __init__(self, entity_id, rel_ids=None, name="", mandatory=True, description="", id=None):
        self.id = id or new_id("arc")
        self.entity_id = entity_id
        self.rel_ids = list(rel_ids or [])
        self.name = name or ""
        self.mandatory = bool(mandatory)
        self.description = description or ""

    def to_dict(self):
        return {
            "id": self.id,
            "entity_id": self.entity_id,
            "rel_ids": list(self.rel_ids),
            "name": self.name,
            "mandatory": self.mandatory,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d):
        return RelationshipArc(
            entity_id=d.get("entity_id"),
            rel_ids=d.get("rel_ids", []),
            name=d.get("name", ""),
            mandatory=d.get("mandatory", True),
            description=d.get("description", ""),
            id=d.get("id"),
        )


class Project:
    """Gerencia entidades, relacionamentos, especializações, arcos e domínios do modelo ER."""

    NOTATIONS = ("chen", "barker")

    def __init__(self, notation="chen"):
        self.entities = []
        self.rels = []
        self.specs = []
        self.domains = []
        self.arcs = []
        self.notation = notation if notation in self.NOTATIONS else "chen"
        # "ratio" -> 1 / N / M ; "minmax" -> (min,max) derivado da participação
        self.card_style = "ratio"

    def find_entity(self, eid):
        return next((e for e in self.entities if e.id == eid), None)

    def find_rel(self, rid):
        return next((r for r in self.rels if r.id == rid), None)

    def find_spec(self, sid):
        return next((s for s in self.specs if s.id == sid), None)

    def find_domain(self, did):
        return next((d for d in self.domains if d.id == did), None)

    def find_arc(self, aid):
        return next((a for a in self.arcs if a.id == aid), None)

    def add_entity(self, entity):
        self.entities.append(entity)

    def add_arc(self, arc):
        self.arcs.append(arc)

    def remove_arc(self, aid):
        self.arcs = [a for a in self.arcs if a.id != aid]

    def arcs_of_entity(self, eid):
        return [a for a in self.arcs if a.entity_id == eid]

    def arcs_of_rel(self, rid):
        return [a for a in self.arcs if rid in a.rel_ids]

    def remove_entity(self, eid):
        self.entities = [e for e in self.entities if e.id != eid]
        self.rels = [r for r in self.rels if r.entity1_id != eid and r.entity2_id != eid]
        for r in self.rels:
            r.extra_parts = [p for p in r.extra_parts if p["entity_id"] != eid]
        for s in self.specs:
            s.parent_ids = [i for i in s.parent_ids if i != eid]
            s.child_ids = [i for i in s.child_ids if i != eid]
        self.specs = [s for s in self.specs if s.parent_ids and s.child_ids]
        self.arcs = [a for a in self.arcs if a.entity_id != eid]
        # remove relacionamentos deletados dos arcos restantes
        valid_rel_ids = {r.id for r in self.rels}
        for a in self.arcs:
            a.rel_ids = [rid for rid in a.rel_ids if rid in valid_rel_ids]
        self.arcs = [a for a in self.arcs if len(a.rel_ids) >= 2]

    def remove_rel(self, rid):
        self.rels = [r for r in self.rels if r.id != rid]
        for a in self.arcs:
            a.rel_ids = [r_id for r_id in a.rel_ids if r_id != rid]
        self.arcs = [a for a in self.arcs if len(a.rel_ids) >= 2]

    def remove_spec(self, sid):
        self.specs = [s for s in self.specs if s.id != sid]

    def remove_domain(self, did):
        """Remove o domínio e desvincula os atributos que o usavam."""
        self.domains = [d for d in self.domains if d.id != did]
        for a in self.iter_attributes():
            if a.domain_id == did:
                a.domain_id = None

    def iter_attributes(self):
        """Todos os atributos do modelo (entidades, sub-atributos e atributos de relacionamento)."""
        def walk(attrs):
            for a in attrs:
                yield a
                yield from walk(a.sub_attrs)
        for e in self.entities:
            yield from walk(e.attrs)
        for r in self.rels:
            yield from walk(r.attrs)

    def spec_of_child(self, entity_id):
        """Especialização em que a entidade é subclasse (ou None)."""
        return next((s for s in self.specs if entity_id in s.child_ids), None)

    def specs_of_parent(self, entity_id):
        return [s for s in self.specs if entity_id in s.parent_ids]

    def to_dict(self):
        return {
            "notation": self.notation,
            "card_style": self.card_style,
            "domains": [d.to_dict() for d in self.domains],
            "entities": [e.to_dict() for e in self.entities],
            "rels": [r.to_dict() for r in self.rels],
            "specs": [s.to_dict() for s in self.specs],
            "arcs": [a.to_dict() for a in self.arcs],
        }

    @staticmethod
    def from_dict(d):
        notation = d.get("notation", "chen")
        if notation == "table":          # compatibilidade: antigo "Diagrama de Tabelas" -> Barker
            notation = "barker"
        p = Project(notation=notation)
        p.card_style = d.get("card_style", "ratio") if d.get("card_style") in ("ratio", "minmax") else "ratio"
        p.domains = [Domain.from_dict(x) for x in d.get("domains", [])]
        p.entities = [Entity.from_dict(e) for e in d.get("entities", [])]
        p.rels = [Relationship.from_dict(r) for r in d.get("rels", [])]
        p.specs = [Specialization.from_dict(s) for s in d.get("specs", [])]
        p.arcs = [RelationshipArc.from_dict(a) for a in d.get("arcs", [])]
        return p

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path):
        with open(path, "r", encoding="utf-8") as f:
            return Project.from_dict(json.load(f))

    @staticmethod
    def create_navathe_company_example():
        """Cria o esquema clássico 'Empresa' do livro Sistemas de Banco de Dados (Navathe 6ª Ed., Fig. 3.2)."""
        p = Project()

        # 1. FUNCIONARIO (Entidade Forte)
        func = Entity(
            name="FUNCIONARIO",
            x=200,
            y=260,
            is_weak=False,
            attrs=[
                Attribute(
                    name="Nome",
                    type="STRING",
                    attr_type="composite",
                    sub_attrs=[
                        Attribute("Pnome", "STRING", attr_type="simple", description="Primeiro nome"),
                        Attribute("Minicial", "STRING", attr_type="simple", description="Inicial do nome do meio"),
                        Attribute("Unome", "STRING", attr_type="simple", description="Último nome"),
                    ],
                    offset_x=-30,
                    offset_y=-90
                ),
                Attribute("Cpf", "STRING", attr_type="pk", offset_x=-150, offset_y=-50, description="CPF do funcionário (identificador)"),
                Attribute("Datanasc", "DATE", attr_type="simple", offset_x=-150, offset_y=-110, description="Data de nascimento"),
                Attribute("Endereco", "STRING", attr_type="simple", offset_x=60, offset_y=-90, description="Endereço residencial"),
                Attribute("Salario", "DECIMAL", attr_type="simple", offset_x=140, offset_y=-90, description="Salário mensal"),
                Attribute("Sexo", "STRING", attr_type="simple", offset_x=140, offset_y=-40, description="Sexo do funcionário"),
            ]
        )

        # 2. DEPARTAMENTO (Entidade Forte)
        depto = Entity(
            name="DEPARTAMENTO",
            x=850,
            y=260,
            is_weak=False,
            attrs=[
                Attribute("Nome", "STRING", attr_type="pk", offset_x=-50, offset_y=-80, description="Nome do departamento"),
                Attribute("Localizacoes", "STRING", attr_type="multivalued", offset_x=60, offset_y=-100, description="Localizações (cidades) do departamento"),
                Attribute("Numero", "INTEGER", attr_type="pk", offset_x=140, offset_y=-80, description="Número do departamento"),
                Attribute("Numero_funcionarios", "INTEGER", attr_type="derived", offset_x=-180, offset_y=10, description="Quantidade de funcionários (derivado)"),
            ]
        )

        # 3. PROJETO (Entidade Forte)
        proj = Entity(
            name="PROJETO",
            x=850,
            y=560,
            is_weak=False,
            attrs=[
                Attribute("Nome", "STRING", attr_type="pk", offset_x=-80, offset_y=70, description="Nome do projeto"),
                Attribute("Numero", "INTEGER", attr_type="pk", offset_x=-40, offset_y=110, description="Número do projeto"),
                Attribute("Localizacao", "STRING", attr_type="simple", offset_x=80, offset_y=80, description="Localização do projeto"),
            ]
        )

        # 4. DEPENDENTE (Entidade Fraca - retângulo duplo)
        dep = Entity(
            name="DEPENDENTE",
            x=440,
            y=800,
            is_weak=True,
            attrs=[
                Attribute("Nome", "STRING", attr_type="partial_key", offset_x=-130, offset_y=70, description="Nome do dependente (chave parcial)"),
                Attribute("Sexo", "STRING", attr_type="simple", offset_x=-50, offset_y=70, description="Sexo do dependente"),
                Attribute("Data_nascimento", "DATE", attr_type="simple", offset_x=50, offset_y=70, description="Data de nascimento do dependente"),
                Attribute("Parentesco", "STRING", attr_type="simple", offset_x=140, offset_y=70, description="Grau de parentesco com o funcionário"),
            ]
        )

        p.add_entity(func)
        p.add_entity(depto)
        p.add_entity(proj)
        p.add_entity(dep)

        # 5. Relacionamento Recursivo: SUPERVISAO em FUNCIONARIO
        p.rels.append(
            Relationship(
                name="SUPERVISAO",
                entity1_id=func.id,
                entity2_id=func.id,
                card1="1",
                card2="N",
                part1="partial",
                part2="partial",
                role1="Supervisor",
                role2="Supervisionado",
                pos_x=120,
                pos_y=550
            )
        )

        # 6. Relacionamento: TRABALHA_PARA (FUNCIONARIO - DEPARTAMENTO)
        p.rels.append(
            Relationship(
                name="TRABALHA_PARA",
                entity1_id=func.id,
                entity2_id=depto.id,
                card1="N",
                card2="1",
                part1="partial",
                part2="partial",
                pos_x=530,
                pos_y=160
            )
        )

        # 7. Relacionamento: GERENCIA (FUNCIONARIO - DEPARTAMENTO com Data_inicio)
        p.rels.append(
            Relationship(
                name="GERENCIA",
                entity1_id=func.id,
                entity2_id=depto.id,
                card1="1",
                card2="1",
                part1="partial",
                part2="total",  # Linha dupla no lado DEPARTAMENTO
                attrs=[Attribute("Data_inicio", "DATE", attr_type="simple", description="Data em que o gerente assumiu o departamento")],
                pos_x=530,
                pos_y=360
            )
        )

        # 8. Relacionamento: CONTROLA (DEPARTAMENTO - PROJETO)
        p.rels.append(
            Relationship(
                name="CONTROLA",
                entity1_id=depto.id,
                entity2_id=proj.id,
                card1="1",
                card2="N",
                part1="partial",
                part2="total",  # Linha dupla no lado PROJETO
                pos_x=930,
                pos_y=430
            )
        )

        # 9. Relacionamento: TRABALHA_EM (FUNCIONARIO - PROJETO com Horas)
        p.rels.append(
            Relationship(
                name="TRABALHA_EM",
                entity1_id=func.id,
                entity2_id=proj.id,
                card1="M",
                card2="N",
                part1="partial",
                part2="total",
                attrs=[Attribute("Horas", "DECIMAL", attr_type="simple", description="Horas semanais dedicadas ao projeto")],
                pos_x=620,
                pos_y=520
            )
        )

        # 10. Relacionamento Identificador: DEPENDENTES_DE (FUNCIONARIO - DEPENDENTE)
        p.rels.append(
            Relationship(
                name="DEPENDENTES_DE",
                entity1_id=func.id,
                entity2_id=dep.id,
                card1="1",
                card2="N",
                part1="partial",
                part2="total",  # Linha dupla no lado DEPENDENTE
                is_identifying=True,  # Losango duplo
                pos_x=450,
                pos_y=660
            )
        )

        return p

    @staticmethod
    def create_eer_example():
        """Exemplo EER (Navathe cap. 8): especialização disjunta com atributo definidor,
        categoria (união) e domínio definido pelo usuário."""
        p = Project()
        status = Domain(
            name="Status_Cadastro", type="INTEGER", impl="lookup",
            description="Situação do cadastro do funcionário",
            values=[{"code": "1", "label": "Atualizado", "description": "Dados conferidos no último ciclo"},
                    {"code": "2", "label": "Desatualizado", "description": "Pendente de recadastramento"},
                    {"code": "3", "label": "Fechado", "description": "Cadastro encerrado"}],
        )
        p.domains.append(status)

        emp = Entity("EMPREGADO", x=420, y=120, attrs=[
            Attribute("Cpf", "STRING", attr_type="pk", description="CPF do empregado (somente dígitos)"),
            Attribute("Nome", "STRING", description="Nome completo"),
            Attribute("Tipo_trabalho", "STRING", description="Atributo definidor da especialização"),
            Attribute("Status", "INTEGER", domain_id=status.id, description="Situação do cadastro"),
        ])
        sec = Entity("SECRETARIA", x=120, y=430, attrs=[Attribute("Vel_digitacao", "INTEGER")])
        tec = Entity("TECNICO", x=420, y=430, attrs=[Attribute("Grau_tecnico", "STRING")])
        eng = Entity("ENGENHEIRO", x=720, y=430, attrs=[Attribute("Tipo_eng", "STRING")])
        pes = Entity("PESSOA", x=120, y=760, attrs=[Attribute("Cpf", "STRING", attr_type="pk"),
                                                   Attribute("Nome", "STRING")])
        emp2 = Entity("EMPRESA", x=720, y=760, attrs=[Attribute("Cnpj", "STRING", attr_type="pk"),
                                                    Attribute("Razao_social", "STRING")])
        prop = Entity("PROPRIETARIO", x=420, y=1010, attrs=[Attribute("Participacao", "DECIMAL")])
        for e in (emp, sec, tec, eng, pes, emp2, prop):
            p.add_entity(e)
        p.specs.append(Specialization("specialization", [emp.id], [sec.id, tec.id, eng.id], "d", "partial",
                                      defining_attr="Tipo_trabalho", pos_x=480, pos_y=290))
        p.specs.append(Specialization("union", [pes.id, emp2.id], [prop.id], completeness="partial",
                                      pos_x=480, pos_y=890))
        return p
