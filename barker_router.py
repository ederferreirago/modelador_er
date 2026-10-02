"""Roteamento ortogonal de conectores para a notação de Barker.

Cada conector sai perpendicular à borda da entidade, segue por corredores livres (grade de
visibilidade formada pelas bordas das caixas) e contorna as demais entidades, com poucas curvas.
Conectores que chegam ao mesmo lado de uma entidade recebem portas distintas e igualmente
espaçadas; trechos coincidentes com outros conectores são penalizados para que corram em paralelo
em corredores diferentes. Sem dependências de Tk: pode ser testado isoladamente.
"""
import heapq
import math

STUB = 16          # distância mínima em linha reta ao sair da entidade
MARGIN = 12        # folga entre um conector e as caixas que ele contorna
BEND_COST = 18.0
REUSE_COST = 3.0   # por unidade de comprimento sobreposta a outro conector
MAX_NODES = 9000   # acima disso usa o roteador rápido (cotovelos simples)

NORMALS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]


def _center(r):
    return r[0] + r[2] / 2.0, r[1] + r[3] / 2.0


def side_points(rect, side):
    """(início, fim) do lado da caixa (sem os cantos arredondados)."""
    x, y, w, h = rect
    c = min(10.0, w / 4.0, h / 4.0)
    if side == "top":
        return (x + c, y), (x + w - c, y)
    if side == "bottom":
        return (x + c, y + h), (x + w - c, y + h)
    if side == "left":
        return (x, y + c), (x, y + h - c)
    return (x + w, y + c), (x + w, y + h - c)


def port_at(rect, side, frac):
    a, b = side_points(rect, side)
    return a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac


def preferred_sides(rect, other_rect, allowed=("top", "bottom", "left", "right")):
    """Lados ordenados pela direção do outro elemento (o mais alinhado primeiro)."""
    cx, cy = _center(rect)
    ox, oy = _center(other_rect)
    dx, dy = ox - cx, oy - cy
    d = math.hypot(dx, dy) or 1.0
    scored = sorted(allowed, key=lambda s: -(NORMALS[s][0] * dx + NORMALS[s][1] * dy) / d)
    return scored


def _polyline_length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _simplify(pts):
    """Remove pontos repetidos e colineares."""
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        out.append(p)
    changed = True
    while changed and len(out) > 2:
        changed = False
        for i in range(1, len(out) - 1):
            a, b, c = out[i - 1], out[i], out[i + 1]
            if (abs(a[0] - b[0]) < 0.01 and abs(b[0] - c[0]) < 0.01) or \
               (abs(a[1] - b[1]) < 0.01 and abs(b[1] - c[1]) < 0.01):
                del out[i]
                changed = True
                break
    return out


def bends(pts):
    return max(0, len(pts) - 2)


def elbow_route(pa, na, pb, nb):
    """Roteamento rápido (ignora obstáculos): linha reta direta quando alinhado, ou L/Z entre duas portas."""
    horiz_a, horiz_b = na[1] == 0, nb[1] == 0
    if horiz_a and horiz_b:
        # Portas opostas no mesmo eixo Y apontando uma para a outra: linha reta horizontal pura
        if na[0] * nb[0] == -1 and abs(pa[1] - pb[1]) < 0.5:
            if (na[0] > 0 and pa[0] <= pb[0]) or (na[0] < 0 and pa[0] >= pb[0]):
                return [pa, (pb[0], pa[1])]
        sa = (pa[0] + na[0] * STUB, pa[1] + na[1] * STUB)
        sb = (pb[0] + nb[0] * STUB, pb[1] + nb[1] * STUB)
        mx = (sa[0] + sb[0]) / 2.0
        mid = [(mx, sa[1]), (mx, sb[1])]
    elif not horiz_a and not horiz_b:
        # Portas opostas no mesmo eixo X apontando uma para a outra: linha reta vertical pura
        if na[1] * nb[1] == -1 and abs(pa[0] - pb[0]) < 0.5:
            if (na[1] > 0 and pa[1] <= pb[1]) or (na[1] < 0 and pa[1] >= pb[1]):
                return [pa, (pa[0], pb[1])]
        sa = (pa[0] + na[0] * STUB, pa[1] + na[1] * STUB)
        sb = (pb[0] + nb[0] * STUB, pb[1] + nb[1] * STUB)
        my = (sa[1] + sb[1]) / 2.0
        mid = [(sa[0], my), (sb[0], my)]
    elif horiz_a:
        sa = (pa[0] + na[0] * STUB, pa[1] + na[1] * STUB)
        sb = (pb[0] + nb[0] * STUB, pb[1] + nb[1] * STUB)
        mid = [(sb[0], sa[1])]
    else:
        sa = (pa[0] + na[0] * STUB, pa[1] + na[1] * STUB)
        sb = (pb[0] + nb[0] * STUB, pb[1] + nb[1] * STUB)
        mid = [(sa[0], sb[1])]
    return _simplify([pa, sa] + mid + [sb, pb])


class _Grid:
    def __init__(self, obstacles, extra_x, extra_y, margin):
        self.margin = margin
        self.inflated = [(x - margin, y - margin, x + w + margin, y + h + margin) for x, y, w, h in obstacles]
        ox, oy = set(), set()
        for x1, y1, x2, y2 in self.inflated:
            ox.update((x1, x2))
            oy.update((y1, y2))
        xs, ys = set(extra_x) | ox, set(extra_y) | oy
        allx = sorted(ox | set(extra_x))
        ally = sorted(oy | set(extra_y))
        # corredores no meio dos vãos entre caixas (linhas centralizadas, visual mais limpo)
        for coords, target in ((allx, xs), (ally, ys)):
            for p, q in zip(coords, coords[1:]):
                if q - p > 3 * MARGIN:
                    target.add((p + q) / 2.0)
        if allx:
            xs.update((allx[0] - 3 * MARGIN, allx[-1] + 3 * MARGIN))
        if ally:
            ys.update((ally[0] - 3 * MARGIN, ally[-1] + 3 * MARGIN))
        self.xs = sorted(round(v, 3) for v in xs)
        self.ys = sorted(round(v, 3) for v in ys)
        self.ix = {v: i for i, v in enumerate(self.xs)}
        self.iy = {v: i for i, v in enumerate(self.ys)}
        self._block_nodes()

    def _block_nodes(self):
        """Nós e arestas estritamente dentro de uma caixa inflada ficam bloqueados."""
        from bisect import bisect_left, bisect_right
        xs, ys = self.xs, self.ys
        self.node_blocked, self.h_blocked, self.v_blocked = set(), set(), set()
        for x1, y1, x2, y2 in self.inflated:
            x1, y1, x2, y2 = round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)
            ia, ib = bisect_right(xs, x1), bisect_left(xs, x2)      # xs estritamente entre x1 e x2
            ja, jb = bisect_right(ys, y1), bisect_left(ys, y2)
            for i in range(ia, ib):
                for j in range(ja, jb):
                    self.node_blocked.add((i, j))
            # arestas horizontais (i -> i+1) cujo vão está dentro de [x1, x2] e y estritamente dentro
            ia2, ib2 = bisect_left(xs, x1), bisect_right(xs, x2) - 1
            for i in range(ia2, ib2):
                for j in range(ja, jb):
                    self.h_blocked.add((i, j))
            ja2, jb2 = bisect_left(ys, y1), bisect_right(ys, y2) - 1
            for j in range(ja2, jb2):
                for i in range(ia, ib):
                    self.v_blocked.add((i, j))

    def inside(self, x, y):
        i, j = self.ix.get(round(x, 3)), self.iy.get(round(y, 3))
        return (i, j) in self.node_blocked if i is not None and j is not None else False

    def size(self):
        return len(self.xs) * len(self.ys)


class _Used:
    """Segmentos já ocupados por outros conectores (para penalizar sobreposição)."""

    def __init__(self):
        self.h = {}   # y -> [(x1, x2)]
        self.v = {}   # x -> [(y1, y2)]

    def add_path(self, pts):
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            if abs(ay - by) < 1e-6:
                self.h.setdefault(round(ay, 2), []).append((min(ax, bx), max(ax, bx)))
            elif abs(ax - bx) < 1e-6:
                self.v.setdefault(round(ax, 2), []).append((min(ay, by), max(ay, by)))

    def overlap(self, ax, ay, bx, by):
        if abs(ay - by) < 1e-6:
            lo, hi = min(ax, bx), max(ax, bx)
            for s, e in self.h.get(round(ay, 2), ()):
                o = min(hi, e) - max(lo, s)
                if o > 0:
                    return o
        elif abs(ax - bx) < 1e-6:
            lo, hi = min(ay, by), max(ay, by)
            for s, e in self.v.get(round(ax, 2), ()):
                o = min(hi, e) - max(lo, s)
                if o > 0:
                    return o
        return 0.0


def _dijkstra(grid, start, goal, used, start_dir):
    """A* ortogonal (comprimento + curvas + reuso) entre dois nós da grade."""
    xs, ys = grid.xs, grid.ys
    nx_max, ny_max = len(xs), len(ys)
    sx, sy = grid.ix[start[0]], grid.iy[start[1]]
    gx, gy = grid.ix[goal[0]], grid.iy[goal[1]]
    goal_x, goal_y = xs[gx], ys[gy]
    nb, hb, vb = grid.node_blocked, grid.h_blocked, grid.v_blocked
    start_state = (sx, sy, start_dir)
    g = {start_state: 0.0}
    prev = {}
    heap = [(abs(xs[sx] - goal_x) + abs(ys[sy] - goal_y), 0.0, sx, sy, start_dir)]
    best = None
    has_used = bool(used.h or used.v)
    while heap:
        _f, d, ix, iy, di = heapq.heappop(heap)
        if g.get((ix, iy, di), 1e18) < d - 1e-9:
            continue
        if ix == gx and iy == gy:
            best = (ix, iy, di)
            break
        ax, ay = xs[ix], ys[iy]
        for nd, (dx, dy) in enumerate(DIRS):
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < nx_max and 0 <= jy < ny_max) or (jx, jy) in nb:
                continue
            if dy == 0 and (min(ix, jx), iy) in hb:
                continue
            if dx == 0 and ix in () or (dx == 0 and (ix, min(iy, jy)) in vb):
                continue
            bx, by = xs[jx], ys[jy]
            cost = abs(bx - ax) + abs(by - ay)
            if di != nd and di != -1:
                cost += BEND_COST
            if has_used:
                cost += REUSE_COST * used.overlap(ax, ay, bx, by)
            nxt = (jx, jy, nd)
            total = d + cost
            if total < g.get(nxt, 1e18) - 1e-9:
                g[nxt] = total
                prev[nxt] = (ix, iy, di)
                heapq.heappush(heap, (total + abs(bx - goal_x) + abs(by - goal_y), total, jx, jy, nd))
    if best is None:
        return None
    path, cur = [], best
    while True:
        path.append((xs[cur[0]], ys[cur[1]]))
        if cur == start_state:
            break
        cur = prev[cur]
    path.reverse()
    return path, g[best]


def _route_between(pa, na, pb, nb, obstacles, used, margin_list=(MARGIN, MARGIN / 2.0, 2.0)):
    """Rota ortogonal entre duas portas contornando `obstacles` (lista de (x, y, w, h))."""
    sa = (round(pa[0] + na[0] * STUB, 3), round(pa[1] + na[1] * STUB, 3))
    sb = (round(pb[0] + nb[0] * STUB, 3), round(pb[1] + nb[1] * STUB, 3))
    sa_exact = (pa[0] + na[0] * STUB, pa[1] + na[1] * STUB)
    sb_exact = (pb[0] + nb[0] * STUB, pb[1] + nb[1] * STUB)
    start_dir = DIRS.index(na) if na in DIRS else -1
    for m in margin_list:
        grid = _Grid(obstacles, [sa[0], sb[0]], [sa[1], sb[1]], m)
        if grid.size() > MAX_NODES:
            return None
        if grid.inside(*sa) or grid.inside(*sb):
            continue
        res = _dijkstra(grid, sa, sb, used, start_dir)
        if res:
            path = list(res[0])
            path[0], path[-1] = sa_exact, sb_exact      # alinha exatamente com as portas
            return _simplify([pa] + path + [pb]), res[1]
    return None


def route_edges(rects, edges, fast=False):
    """Calcula rotas ortogonais.

    rects: {id: (x, y, w, h)} de todos os elementos (entidades, caixas de interseção).
    edges: lista de dicts {key, a, b, ignore (set de ids que não são obstáculo), sides_a, sides_b}.
    Retorna {key: {"pts": [...], "na": (nx, ny), "nb": (nx, ny)}}.
    """
    # 1) escolha dos lados
    choice = {}
    used = _Used()
    for e in edges:
        ra, rb = rects[e["a"]], rects[e["b"]]
        sa_list = preferred_sides(ra, rb, e.get("sides_a", tuple(NORMALS)))
        sb_list = preferred_sides(rb, ra, e.get("sides_b", tuple(NORMALS)))
        obstacles = [r for k, r in rects.items() if k not in (e["a"], e["b"]) and k not in e.get("ignore", ())]
        best, best_cost = None, None
        combos = [(sa_list[0], sb_list[0])]
        if not fast:
            sa2 = sa_list[1] if len(sa_list) > 1 else sa_list[0]
            sb2 = sb_list[1] if len(sb_list) > 1 else sb_list[0]
            for cand in ((sa_list[0], sb2), (sa2, sb_list[0]), (sa2, sb2)):
                if cand not in combos:
                    combos.append(cand)
        for i, (sa, sb) in enumerate(combos):
            pa, pb = port_at(ra, sa, 0.5), port_at(rb, sb, 0.5)
            na, nb = NORMALS[sa], NORMALS[sb]
            if fast:
                pts = elbow_route(pa, na, pb, nb)
                cost = _polyline_length(pts) + BEND_COST * bends(pts)
            else:
                res = _route_between(pa, na, pb, nb, obstacles, used)
                if res is None:
                    pts = elbow_route(pa, na, pb, nb)
                    cost = _polyline_length(pts) + BEND_COST * bends(pts) + 5000
                else:
                    pts, cost = res
            if best is None or cost < best_cost:
                best, best_cost = (sa, sb, pts), cost
            if i == 0 and not fast:
                manh = abs(pa[0] - pb[0]) + abs(pa[1] - pb[1])
                if bends(pts) <= 2 and _polyline_length(pts) <= 1.35 * manh + 2 * STUB:
                    break            # o caminho preferido já é bom: não testa os outros lados
        choice[e["key"]] = (best[0], best[1])
        used.add_path(best[2])
    # 2) portas distintas por (elemento, lado), ordenadas pela posição do outro extremo
    groups = {}
    for e in edges:
        sa, sb = choice[e["key"]]
        groups.setdefault((e["a"], sa), []).append((e["key"], "a", e["b"]))
        groups.setdefault((e["b"], sb), []).append((e["key"], "b", e["a"]))
    frac = {}
    for (eid, side), items in groups.items():
        horiz = side in ("top", "bottom")
        items.sort(key=lambda it: (_center(rects[it[2]])[0] if horiz else _center(rects[it[2]])[1]))
        n = len(items)
        for i, (key, end, _other) in enumerate(items):
            frac[(key, end)] = (i + 1) / (n + 1)
    # 3) rota final com as portas definitivas
    result = {}
    used = _Used()
    for e in edges:
        sa, sb = choice[e["key"]]
        ra, rb = rects[e["a"]], rects[e["b"]]
        pa = port_at(ra, sa, frac[(e["key"], "a")])
        pb = port_at(rb, sb, frac[(e["key"], "b")])
        na, nb = NORMALS[sa], NORMALS[sb]
        obstacles = [r for k, r in rects.items() if k not in (e["a"], e["b"]) and k not in e.get("ignore", ())]

        # Alinhamento fino para linha reta direta quando caixas estão alinhadas ou quase alinhadas
        if sa in ("left", "right") and sb in ("left", "right") and na[0] * nb[0] == -1:
            if abs(pa[1] - pb[1]) < 18.0:
                y_align = (pa[1] + pb[1]) / 2.0
                (ax1, ay1), (ax2, ay2) = side_points(ra, sa)
                (bx1, by1), (bx2, by2) = side_points(rb, sb)
                if min(ay1, ay2) <= y_align <= max(ay1, ay2) and min(by1, by2) <= y_align <= max(by1, by2):
                    t_pa, t_pb = (pa[0], y_align), (pb[0], y_align)
                    if not any(segment_hits_rect(t_pa, t_pb, obs) for obs in obstacles):
                        pa, pb = t_pa, t_pb
        elif sa in ("top", "bottom") and sb in ("top", "bottom") and na[1] * nb[1] == -1:
            if abs(pa[0] - pb[0]) < 18.0:
                x_align = (pa[0] + pb[0]) / 2.0
                (ax1, ay1), (ax2, ay2) = side_points(ra, sa)
                (bx1, by1), (bx2, by2) = side_points(rb, sb)
                if min(ax1, ax2) <= x_align <= max(ax1, ax2) and min(bx1, bx2) <= x_align <= max(bx1, bx2):
                    t_pa, t_pb = (x_align, pa[1]), (x_align, pb[1])
                    if not any(segment_hits_rect(t_pa, t_pb, obs) for obs in obstacles):
                        pa, pb = t_pa, t_pb

        if fast:
            pts = elbow_route(pa, na, pb, nb)
        else:
            res = _route_between(pa, na, pb, nb, obstacles, used)
            pts = res[0] if res else elbow_route(pa, na, pb, nb)
        used.add_path(pts)
        result[e["key"]] = {"pts": pts, "na": na, "nb": nb}
    return result


def polyline_midpoint(pts):
    """(ponto, índice do segmento, posição) na metade do comprimento do caminho."""
    total = _polyline_length(pts)
    half, acc = total / 2.0, 0.0
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if acc + seg >= half and seg > 0:
            t = (half - acc) / seg
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), i
        acc += seg
    return pts[-1], max(0, len(pts) - 2)


def split_at_midpoint(pts):
    """Divide o caminho em duas metades [P1..M] e [P2..M] (a segunda invertida)."""
    m, i = polyline_midpoint(pts)
    first = pts[:i + 1] + [m]
    second = list(reversed(pts[i + 1:])) + [m]
    return _simplify(first), _simplify(second), m


def segment_hits_rect(a, b, rect, pad=0.0):
    """True se o segmento ortogonal a-b atravessa o interior do retângulo (x, y, w, h)."""
    x1, y1, x2, y2 = rect[0] - pad, rect[1] - pad, rect[0] + rect[2] + pad, rect[1] + rect[3] + pad
    lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
    lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
    return hi_x > x1 + 1e-6 and lo_x < x2 - 1e-6 and hi_y > y1 + 1e-6 and lo_y < y2 - 1e-6
