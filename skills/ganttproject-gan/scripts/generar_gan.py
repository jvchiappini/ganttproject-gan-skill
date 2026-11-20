#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
generar_gan.py - Genera cronogramas .gan (GanttProject) desde una especificacion JSON.

Calcula todo lo que GanttProject necesita para mostrar un cronograma de obra completo:

  * Calendario laboral: dias habiles, feriados por pais (PY / AR, Semana Santa
    calculada), feriados extra, recesos (p. ej. fin de anio).
  * Duraciones directas, por computo (cantidad / rendimiento x cuadrillas) o
    tareas que abarcan toda la obra (direccion, sereno, limpieza periodica).
  * CPM: dependencias FS/SS/FF/SF con desfasajes, holgura total y libre,
    camino critico.
  * EDT/WBS con tareas resumen e hitos.
  * Recursos (mano de obra y equipos) con rol, costo diario y asignaciones:
    GanttProject dibuja el Diagrama de Recursos con esto.
      - modo "individual": personas concretas sin solapes.
      - modo "agrupado": un recurso por tipo con carga = cantidad x 100 %.
  * Nivelacion de recursos (metodo serie) con topes por tipo.
  * Costos por tarea (mano de obra + materiales), columnas personalizadas,
    linea base, notas automaticas.
  * Salidas extra: resumen Markdown y CSV (tareas, histograma semanal, curva S).

Uso:
    python generar_gan.py cronograma.json salida.gan [--resumen r.md] [--csv carpeta]

Sin dependencias externas (solo biblioteca estandar, Python >= 3.8).
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from xml.sax.saxutils import quoteattr

__version__ = "2.0.0"

# --------------------------------------------------------------------------- constantes

DIAS = {"lun": 0, "mar": 1, "mie": 2, "mié": 2, "jue": 3, "vie": 4, "sab": 5, "sáb": 5, "dom": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
GAN_DIAS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TIPOS_DEP = {"SS": 1, "FS": 2, "FF": 3, "SF": 4}
ALIAS_DEP = {"CF": "FS", "CC": "SS", "FC": "SF"}  # Comienzo/Fin en castellano
PALETA = ["#8cb6ce", "#a5d6a7", "#ffcc80", "#ce93d8", "#90caf9", "#f48fb1",
          "#bcaaa4", "#80cbc4", "#fff59d", "#b0bec5", "#ffab91", "#c5e1a5"]

# columnas personalizadas (id GanttProject, clave, titulo, ancho)
COLUMNAS = [
    ("tpc0", "computo", "Cómputo", 80),
    ("tpc1", "rendimiento", "Rendimiento", 110),
    ("tpc2", "cuadrilla", "Cuadrilla", 130),
    ("tpc3", "costo_mo", "Costo MO", 90),
    ("tpc4", "costo_mat", "Costo materiales", 90),
    ("tpc5", "costo_total", "Costo total", 95),
    ("tpc6", "holgura", "Holgura", 70),
]


class SpecError(Exception):
    """Error en la especificacion JSON."""


def fecha(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        raise SpecError("fecha invalida: %r (use AAAA-MM-DD)" % s)


def fmt_num(x):
    x = float(x)
    if x.is_integer():
        return str(int(x))
    return ("%.2f" % x).rstrip("0").rstrip(".")


def fmt_dinero(x):
    return "{:,.0f}".format(x).replace(",", ".")


# --------------------------------------------------------------------------- feriados

def pascua(y):
    """Domingo de Pascua (algoritmo anonimo gregoriano)."""
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(y, mes, dia)


def feriados_paraguay(y):
    p = pascua(y)
    return [
        (dt.date(y, 1, 1), "Año Nuevo"),
        (dt.date(y, 3, 1), "Día de los Héroes"),
        (p - dt.timedelta(days=3), "Jueves Santo"),
        (p - dt.timedelta(days=2), "Viernes Santo"),
        (dt.date(y, 5, 1), "Día del Trabajador"),
        (dt.date(y, 5, 14), "Independencia Nacional"),
        (dt.date(y, 5, 15), "Independencia Nacional / Día de la Madre"),
        (dt.date(y, 6, 12), "Paz del Chaco"),
        (dt.date(y, 8, 15), "Fundación de Asunción"),
        (dt.date(y, 9, 29), "Victoria de Boquerón"),
        (dt.date(y, 12, 8), "Virgen de Caacupé"),
        (dt.date(y, 12, 25), "Navidad"),
    ]


def feriados_argentina(y):
    p = pascua(y)
    return [
        (dt.date(y, 1, 1), "Año Nuevo"),
        (p - dt.timedelta(days=48), "Carnaval"),
        (p - dt.timedelta(days=47), "Carnaval"),
        (dt.date(y, 3, 24), "Día de la Memoria"),
        (dt.date(y, 4, 2), "Malvinas"),
        (p - dt.timedelta(days=2), "Viernes Santo"),
        (dt.date(y, 5, 1), "Día del Trabajador"),
        (dt.date(y, 5, 25), "Revolución de Mayo"),
        (dt.date(y, 6, 20), "Día de la Bandera"),
        (dt.date(y, 7, 9), "Independencia"),
        (dt.date(y, 12, 8), "Inmaculada Concepción"),
        (dt.date(y, 12, 25), "Navidad"),
    ]


PRESETS = {"PY": feriados_paraguay, "AR": feriados_argentina}


# --------------------------------------------------------------------------- calendario

class Calendario:
    """Calendario de dias habiles indexado: el dia habil i es self.lista[i]."""

    def __init__(self, cfg, inicio):
        dias = cfg.get("dias_laborables", ["lun", "mar", "mie", "jue", "vie", "sab"])
        self.laborables = set()
        for d in dias:
            k = str(d).lower()
            k = k if k in DIAS else k[:3]
            if k not in DIAS:
                raise SpecError("dia laborable desconocido: %r" % d)
            self.laborables.add(DIAS[k])
        if not self.laborables:
            raise SpecError("calendario.dias_laborables esta vacio")
        self.feriados = {}
        anios = range(inicio.year - 1, inicio.year + 8)
        preset = cfg.get("feriados_preset")
        if preset:
            if str(preset).upper() not in PRESETS:
                raise SpecError("feriados_preset desconocido: %s (use PY, AR o null)" % preset)
            for y in anios:
                for f, n in PRESETS[str(preset).upper()](y):
                    self.feriados[f] = n
        for item in cfg.get("feriados", []):
            if isinstance(item, str):
                self.feriados[fecha(item)] = "Feriado"
            else:
                self.feriados[fecha(item["fecha"])] = item.get("nombre", "Feriado")
        for p in cfg.get("periodos_no_laborables", []):
            d, h = fecha(p["desde"]), fecha(p["hasta"])
            if h < d:
                raise SpecError("periodo no laborable con 'hasta' anterior a 'desde': %r" % p)
            while d <= h:
                if d.weekday() in self.laborables:
                    self.feriados.setdefault(d, p.get("nombre", "Receso"))
                d += dt.timedelta(days=1)
        for f in cfg.get("excluir_feriados", []):
            self.feriados.pop(fecha(f), None)
        self.lista = []
        d = inicio - dt.timedelta(days=60)
        fin = inicio + dt.timedelta(days=365 * 8)
        while d <= fin:
            if self.es_habil(d):
                self.lista.append(d)
            d += dt.timedelta(days=1)
        self.idx = {d: i for i, d in enumerate(self.lista)}

    def es_habil(self, d):
        return d.weekday() in self.laborables and d not in self.feriados

    def indice(self, d):
        """Indice del primer dia habil >= d."""
        guard = 0
        while d not in self.idx:
            d += dt.timedelta(days=1)
            guard += 1
            if guard > 400:
                raise SpecError("no hay dias habiles cerca de %s" % d)
        return self.idx[d]

    def dia(self, i):
        if i < 0 or i >= len(self.lista):
            raise SpecError("el cronograma excede el horizonte de 8 años del calendario")
        return self.lista[i]


# --------------------------------------------------------------------------- modelo

class Tarea:
    def __init__(self, raw, padre, nivel):
        self.raw = raw
        self.codigo = str(raw.get("id") or raw.get("codigo") or "").strip()
        self.nombre = str(raw.get("nombre", "")).strip()
        self.padre = padre
        self.nivel = nivel
        self.hijos = []
        self.gid = None
        self.pred = []
        self.recursos = {}
        self.es = self.dur = self.es_cpm = None
        self.hito = False
        self.abarca = bool(raw.get("abarca_obra"))
        self.critica = False
        self.retrasada = False
        self.holgura = self.holgura_libre = 0
        self.contenedor = bool(raw.get("_contenedor"))

    @property
    def resumen(self):
        return bool(self.hijos)

    @property
    def ef(self):
        return self.es + self.dur


PRED_RE = re.compile(r"^\s*(.+?)\s*(FS|SS|FF|SF|CF|CC|FC)?\s*([+-]\s*\d+)?\s*(d|dias|días)?\s*$", re.I)


def parse_pred(p):
    if isinstance(p, dict):
        if "id" not in p:
            raise SpecError("predecesora sin 'id': %r" % p)
        tipo = str(p.get("tipo", "FS")).upper()
        tipo = ALIAS_DEP.get(tipo, tipo)
        if tipo not in TIPOS_DEP:
            raise SpecError("tipo de dependencia invalido: %r" % p)
        return str(p["id"]), tipo, int(p.get("desfase", p.get("lag", 0)))
    m = PRED_RE.match(str(p))
    if not m:
        raise SpecError("predecesora invalida: %r" % p)
    cod, tipo, lag = m.group(1), (m.group(2) or "FS").upper(), m.group(3)
    return cod.strip(), ALIAS_DEP.get(tipo, tipo), int(lag.replace(" ", "")) if lag else 0


def construir(tareas_raw):
    todas, hojas, codigos = [], [], {}

    def rec(lista, padre, nivel, prefijo):
        for n, raw in enumerate(lista, 1):
            if not isinstance(raw, dict):
                raise SpecError("cada tarea debe ser un objeto JSON: %r" % (raw,))
            t = Tarea(raw, padre, nivel)
            if not t.nombre:
                raise SpecError("tarea sin 'nombre' (posicion %s%d)" % (prefijo + "." if prefijo else "", n))
            if not t.codigo:
                t.codigo = (prefijo + "." if prefijo else "") + str(n)
            if t.codigo in codigos:
                raise SpecError("id de tarea duplicado: %s" % t.codigo)
            codigos[t.codigo] = t
            todas.append(t)
            if padre:
                padre.hijos.append(t)
            sub = raw.get("hijos") or raw.get("subtareas")
            if sub:
                rec(sub, t, nivel + 1, "" if t.contenedor else t.codigo)
            else:
                hojas.append(t)
    rec(tareas_raw, None, 0, "")
    return todas, hojas, codigos


def duracion_de(t):
    r = t.raw
    if r.get("hito") or r.get("milestone"):
        t.hito = True
        return 0
    if t.abarca:
        return 0  # se calcula despues de programar
    if "duracion" in r:
        d = int(r["duracion"])
        if d < 0:
            raise SpecError("duracion negativa en %s" % t.codigo)
        if d == 0:
            t.hito = True
        return d
    if "cantidad" in r and "rendimiento" in r:
        rend = float(r["rendimiento"]) * float(r.get("cuadrillas", 1))
        if rend <= 0:
            raise SpecError("rendimiento/cuadrillas deben ser > 0 en %s" % t.codigo)
        return max(1, math.ceil(float(r["cantidad"]) / rend - 1e-9))
    raise SpecError("la tarea %s '%s' necesita 'duracion', 'cantidad'+'rendimiento', 'hito' o 'abarca_obra'"
                    % (t.codigo, t.nombre))


# --------------------------------------------------------------------------- programacion

def topologico(hojas):
    succ = defaultdict(list)
    grado = {t.codigo: 0 for t in hojas}
    for t in hojas:
        for c, _, _ in t.pred:
            succ[c].append(t)
            grado[t.codigo] += 1
    orden, cola = [], [t for t in hojas if grado[t.codigo] == 0]
    while cola:
        t = cola.pop(0)
        orden.append(t)
        for s in succ[t.codigo]:
            grado[s.codigo] -= 1
            if grado[s.codigo] == 0:
                cola.append(s)
    if len(orden) != len(hojas):
        raise SpecError("hay un ciclo en las dependencias entre: "
                        + ", ".join(t.codigo for t in hojas if grado[t.codigo] > 0))
    return orden, succ


def restriccion(pred, tipo, lag, dur_succ):
    if tipo == "FS":
        return pred.ef + lag
    if tipo == "SS":
        return pred.es + lag
    if tipo == "FF":
        return pred.ef + lag - dur_succ
    return pred.es + lag - dur_succ  # SF


def inicio_minimo(t, codigos, cal, inicio_idx):
    es = inicio_idx
    if t.raw.get("inicio_no_antes"):
        es = max(es, cal.indice(fecha(t.raw["inicio_no_antes"])))
    for c, tipo, lag in t.pred:
        es = max(es, restriccion(codigos[c], tipo, lag, t.dur))
    return es


def pasada_atras(orden, succ, fin):
    """Fin mas tardio de cada tarea (incluye vinculos de recurso si existen).
    Se itera hasta estabilizar porque los vinculos de recurso no siguen el orden topologico."""
    lf = {t.codigo: fin for t in orden}
    cambio = True
    while cambio:
        cambio = False
        for t in reversed(orden):
            antes = lf[t.codigo]
            for s in succ[t.codigo]:
                for c, tipo, lag in s.pred + getattr(s, "pred_recurso", []):
                    if c != t.codigo:
                        continue
                    ls_s = lf[s.codigo] - s.dur
                    if tipo == "FS":
                        lim = ls_s - lag
                    elif tipo == "SS":
                        lim = ls_s - lag + t.dur
                    elif tipo == "FF":
                        lim = lf[s.codigo] - lag
                    else:
                        lim = lf[s.codigo] - lag + t.dur
                    lf[t.codigo] = min(lf[t.codigo], lim)
            if lf[t.codigo] != antes:
                cambio = True
    return lf


def programar(hojas, codigos, cal, inicio_idx, limites, nivelar):
    activas = [t for t in hojas if not t.abarca]
    orden, succ = topologico(activas)
    for t in orden:
        t.es_cpm = t.es = inicio_minimo(t, codigos, cal, inicio_idx)
    implicitos = []
    if nivelar and limites:
        base = defaultdict(float)
        for t in hojas:
            if t.abarca:
                for r, q in t.recursos.items():
                    base[r] += q
        # prioridad = inicio mas tardio del CPM sin limites (menor holgura primero)
        fin0 = max([t.ef for t in activas] + [inicio_idx + 1])
        lf0 = pasada_atras(orden, succ, fin0)
        for t in activas:
            t.ls_cpm = lf0[t.codigo] - t.dur
        nivelar_recursos(orden, codigos, cal, inicio_idx, limites, base)
        # vinculos de recurso: la tarea retrasada espera a las que liberan su recurso
        for t in activas:
            if not t.retrasada:
                continue
            for o in activas:
                if o is not t and o.dur > 0 and o.ef == t.es and set(o.recursos) & set(t.recursos):
                    implicitos.append((o, t))
        for o, t in implicitos:
            succ[o.codigo].append(t)
            t.pred_recurso = getattr(t, "pred_recurso", []) + [(o.codigo, "FS", 0)]
    fin = max([t.ef for t in activas] + [inicio_idx + 1])
    lf = pasada_atras(orden, succ, fin)
    for t in activas:
        t.holgura = lf[t.codigo] - t.ef
        t.critica = t.holgura <= 0
        libre = None
        for s in succ[t.codigo]:
            for c, tipo, lag in s.pred + getattr(s, "pred_recurso", []):
                if c == t.codigo:
                    h = s.es - restriccion(t, tipo, lag, s.dur)
                    libre = h if libre is None else min(libre, h)
        t.holgura_libre = (fin - t.ef) if libre is None else max(0, libre)
    for t in hojas:
        if t.abarca:
            t.es = t.es_cpm = inicio_idx
            t.dur = max(1, fin - inicio_idx)
            t.holgura = t.holgura_libre = 0
    return fin


def nivelar_recursos(orden, codigos, cal, inicio_idx, limites, base=None):
    """Metodo serie (serial SGS) con regla de prioridad "minimo inicio tardio" (LST):
    entre las tareas elegibles se programa primero la de menor holgura, en el primer
    dia en que la demanda entra dentro de los topes por tipo."""
    base = base or {}
    uso = defaultdict(lambda: defaultdict(float))
    pendientes = sorted(orden, key=lambda t: (getattr(t, "ls_cpm", t.es), t.es, t.codigo))
    hecho = set()
    while pendientes:
        t = next(x for x in pendientes if all(c in hecho for c, _, _ in x.pred))
        pendientes.remove(t)
        s = minimo = inicio_minimo(t, codigos, cal, inicio_idx)
        for r, q in t.recursos.items():
            if q + base.get(r, 0) > limites.get(r, float("inf")) + 1e-9:
                raise SpecError("la tarea %s pide %s de '%s' pero solo hay %s disponibles"
                                % (t.codigo, fmt_num(q), r, fmt_num(limites[r])))
        while not all(uso[r][d] + base.get(r, 0) + q <= limites.get(r, float("inf")) + 1e-9
                      for r, q in t.recursos.items() for d in range(s, s + t.dur)):
            s += 1
        t.es = s
        t.retrasada = s > minimo  # retraso propio (no heredado de predecesoras)
        for r, q in t.recursos.items():
            for d in range(s, s + t.dur):
                uso[r][d] += q
        hecho.add(t.codigo)


def resumir(t):
    if not t.hijos:
        return
    for h in t.hijos:
        resumir(h)
    t.es = min(h.es for h in t.hijos)
    t.dur = max(h.ef for h in t.hijos) - t.es
    t.critica = any(h.critica for h in t.hijos)


# --------------------------------------------------------------------------- recursos y costos

def asignar_recursos(hojas, tipos, modo):
    recursos, asign = [], []
    sig = [0]

    def nuevo(tp, k, nombre):
        r = {"id": sig[0], "nombre": nombre, "rol_id": tp["rol_id"], "costo": tp.get("costo_dia", 0),
             "tipo": k, "contacto": tp.get("contacto", ""), "telefono": tp.get("telefono", "")}
        sig[0] += 1
        recursos.append(r)
        return r

    def responsable_tipo(t):
        resp = t.raw.get("responsable")
        if resp and resp not in t.recursos:
            raise SpecError("la tarea %s tiene responsable '%s' que no esta entre sus recursos" % (t.codigo, resp))
        return resp or (next(iter(t.recursos)) if t.recursos else None)

    if modo == "agrupado":
        por_tipo = {k: nuevo(tp, k, tp["nombre"]) for k, tp in tipos.items()}
        for t in hojas:
            resp = responsable_tipo(t)
            for k, q in t.recursos.items():
                asign.append((t, por_tipo[k]["id"], round(q * 100.0, 2), k == resp))
    else:
        ocupado = defaultdict(list)
        personas = defaultdict(list)
        # primero las tareas permanentes, luego por inicio
        for t in sorted(hojas, key=lambda x: (not x.abarca, x.es, x.codigo)):
            if t.dur == 0 or not t.recursos:
                continue
            resp = responsable_tipo(t)
            for k, q in t.recursos.items():
                enteros = int(math.floor(q + 1e-9))
                fr = round(q - enteros, 4)
                nec = enteros + (1 if fr > 0 else 0)
                cargas = [100.0] * enteros + ([round(fr * 100, 2)] if fr > 0 else [])
                elegidos = []
                for c in cargas:
                    for p in personas[k]:
                        if p in elegidos:
                            continue
                        if all(sum(l for a, b, l in ocupado[p["id"]] if a <= d < b) + c <= 100.0 + 1e-6
                               for d in range(t.es, t.ef)):
                            elegidos.append(p)
                            break
                    else:
                        break
                while len(elegidos) < nec:
                    p = nuevo(tipos[k], k, "%s %d" % (tipos[k]["nombre"], len(personas[k]) + 1))
                    personas[k].append(p)
                    elegidos.append(p)
                for i, p in enumerate(elegidos):
                    ocupado[p["id"]].append((t.es, t.ef, cargas[i]))
                    asign.append((t, p["id"], cargas[i], k == resp and i == 0))
        orden_tipo = {k: i for i, k in enumerate(tipos)}
        recursos.sort(key=lambda r: (orden_tipo[r["tipo"]], r["id"]))
    renum = {r["id"]: i for i, r in enumerate(recursos)}
    for r in recursos:
        r["id"] = renum[r["id"]]
    asign = [(t, renum[rid], c, resp) for t, rid, c, resp in asign]
    return recursos, asign


def costos(t, tipos):
    mo = sum(q * float(tipos[k].get("costo_dia", 0)) * t.dur for k, q in t.recursos.items())
    r = t.raw
    if "costo_materiales" in r:
        mat = float(r["costo_materiales"])
    elif "precio_unitario_material" in r and "cantidad" in r:
        mat = float(r["precio_unitario_material"]) * float(r["cantidad"])
    else:
        mat = 0.0
    return mo, mat


# --------------------------------------------------------------------------- XML

def qa(v):
    return quoteattr(str(v))


def cdata(s):
    return "<![CDATA[%s]]>" % str(s).replace("]]>", "]]]]><![CDATA[>")


def escribir_gan(ctx):
    spec, cal, op = ctx["spec"], ctx["cal"], ctx["opciones"]
    todas, hojas, raices, tipos = ctx["todas"], ctx["hojas"], ctx["raices"], ctx["tipos"]
    recursos, asign, roles = ctx["recursos"], ctx["asign"], ctx["roles"]
    P = spec["proyecto"]
    I = "    "
    out = []
    w = out.append
    ini = min(t.es for t in hojas)
    fin = max(t.ef for t in hojas)
    vista = cal.dia(ini) - dt.timedelta(days=7)
    usar_cols = op.get("columnas_personalizadas", True)

    w('<?xml version="1.0" encoding="UTF-8"?><project name=%s company=%s webLink=%s view-date="%s" '
      'view-index="0" gantt-divider-location="%d" resource-divider-location="300" version=%s locale=%s>'
      % (qa(P.get("nombre", "Proyecto")), qa(P.get("empresa", "")), qa(P.get("web", "http://")),
         vista.isoformat(), 700 if usar_cols else 523, qa(op.get("version", "2.7.1")), qa(P.get("locale", "es_AR"))))
    desc = P.get("descripcion", "")
    w(I + ("<description>%s</description>" % cdata(desc) if desc else "<description/>"))
    w(I + '<view zooming-state="default:%s" id="gantt-chart">' % op.get("zoom", 9))
    w(I * 2 + '<field id="tpd3" name="Nombre" width="55" order="0"/>')
    w(I * 2 + '<field id="tpd4" name="Fecha de inicio" width="15" order="1"/>')
    w(I * 2 + '<field id="tpd5" name="Fecha de fin" width="15" order="2"/>')
    w(I * 2 + '<option id="color.recent">%s</option>' % cdata(" ".join(PALETA[:4])))
    w(I + "</view>")
    w(I + '<view id="resource-table">')
    w(I * 2 + '<field id="0" name="Nombre" width="50" order="0"/>')
    w(I * 2 + '<field id="1" name="Función" width="50" order="1"/>')
    w(I + "</view>")
    w(I + "<!-- -->")

    # calendario
    w(I + "<calendars>")
    w(I * 2 + "<day-types>")
    w(I * 3 + '<day-type id="0"/>')
    w(I * 3 + '<day-type id="1"/>')
    semana = " ".join('%s="%d"' % (GAN_DIAS[i], 0 if i in cal.laborables else 1) for i in [6, 0, 1, 2, 3, 4, 5])
    w(I * 3 + '<default-week id="1" name="default" %s/>' % semana)
    w(I * 3 + '<only-show-weekends value="false"/>')
    w(I * 3 + "<overriden-day-types/>")
    w(I * 3 + "<days/>")
    w(I * 2 + "</day-types>")
    desde = dt.date(cal.dia(ini).year, 1, 1)
    hasta = dt.date(cal.dia(fin - 1).year + 1, 12, 31)
    for f in sorted(cal.feriados):
        if desde <= f <= hasta:
            w(I * 2 + '<date year="%d" month="%d" date="%d" type="HOLIDAY" color="#ff3300">%s</date>'
              % (f.year, f.month, f.day, cdata(cal.feriados[f])))
    w(I + "</calendars>")

    # propiedades de tarea
    w(I + '<tasks empty-milestones="true">')
    w(I * 2 + "<taskproperties>")
    for i, (n, vt) in enumerate([("type", "icon"), ("priority", "icon"), ("info", "icon"), ("name", "text"),
                                 ("begindate", "date"), ("enddate", "date"), ("duration", "int"),
                                 ("completion", "int"), ("coordinator", "text"), ("predecessorsr", "text")]):
        w(I * 3 + '<taskproperty id="tpd%d" name="%s" type="default" valuetype="%s"/>' % (i, n, vt))
    if usar_cols:
        for pid, _, titulo, _ in COLUMNAS:
            w(I * 3 + '<taskproperty id="%s" name=%s type="custom" valuetype="text"/>' % (pid, qa(titulo)))
    w(I * 2 + "</taskproperties>")

    sucesores = defaultdict(list)
    for t in hojas:
        for c, tipo, lag in t.pred:
            sucesores[c].append((t, tipo, lag))

    colores = {}

    def grupo_de(t):
        g = t
        while g.padre is not None and not g.padre.contenedor:
            g = g.padre
        return g

    def color_de(t):
        if t.raw.get("color"):
            return t.raw["color"]
        if op.get("color_criticas") and t.critica:
            return op["color_criticas"]
        if t.abarca:
            return op.get("color_permanentes", "#cfd8dc")
        if op.get("color_por_grupo", True):
            g = grupo_de(t)
            if g.codigo not in colores:
                colores[g.codigo] = PALETA[len(colores) % len(PALETA)]
            return colores[g.codigo]
        return op.get("color_tareas", "#8cb6ce")

    def columnas_de(t):
        r = t.raw
        v = {}
        if t.resumen:
            mo = sum(ctx["costos"][h.codigo][0] for h in iter_hojas(t))
            mat = sum(ctx["costos"][h.codigo][1] for h in iter_hojas(t))
        else:
            mo, mat = ctx["costos"][t.codigo]
            if "cantidad" in r:
                v["computo"] = "%s %s" % (fmt_num(r["cantidad"]), r.get("unidad", ""))
            if "rendimiento" in r:
                v["rendimiento"] = "%s %s/día x %s" % (fmt_num(r["rendimiento"]), r.get("unidad", "u"),
                                                      fmt_num(r.get("cuadrillas", 1)))
            if t.recursos:
                v["cuadrilla"] = " + ".join("%s %s" % (fmt_num(q), tipos[k].get("abrev", k))
                                            for k, q in t.recursos.items())
            v["holgura"] = "%d%s" % (t.holgura, " ●" if t.critica else "")
        if mo:
            v["costo_mo"] = fmt_dinero(mo)
        if mat:
            v["costo_mat"] = fmt_dinero(mat)
        if mo or mat:
            v["costo_total"] = fmt_dinero(mo + mat)
        return v

    def notas_de(t):
        notas = t.raw.get("notas", "")
        if t.resumen or not op.get("notas_automaticas", True):
            return notas
        r = t.raw
        L = []
        if "cantidad" in r and "rendimiento" in r:
            L.append("Cómputo: %s %s | Rendimiento: %s %s/día por cuadrilla | Cuadrillas: %s | Duración: %d días hábiles"
                     % (fmt_num(r["cantidad"]), r.get("unidad", ""), fmt_num(r["rendimiento"]),
                        r.get("unidad", ""), fmt_num(r.get("cuadrillas", 1)), t.dur))
        if t.recursos:
            L.append("Recursos: " + ", ".join("%s x%s" % (tipos[k]["nombre"], fmt_num(q)) for k, q in t.recursos.items()))
        mo, mat = ctx["costos"][t.codigo]
        if mo or mat:
            L.append("Costo MO: %s | Materiales: %s | Total: %s" % (fmt_dinero(mo), fmt_dinero(mat), fmt_dinero(mo + mat)))
        if t.pred:
            L.append("Predecesoras: " + ", ".join("%s %s%s" % (c, tp, ("%+d" % lg) if lg else "") for c, tp, lg in t.pred))
        if not t.abarca:
            L.append("Holgura total: %d | Holgura libre: %d%s"
                     % (t.holgura, t.holgura_libre, " | TAREA CRÍTICA" if t.critica else ""))
        else:
            L.append("Tarea permanente: abarca toda la duración de la obra.")
        if t.retrasada:
            L.append("Retrasada por nivelación de recursos (%d días hábiles respecto del CPM sin límites)."
                     % (t.es - t.es_cpm))
        return (notas + "\n\n" if notas else "") + "\n".join(L)

    def emitir(t, nivel):
        sp = I * (nivel + 2)
        numerar = op.get("numerar", True) and not t.contenedor
        attrs = ['id="%d"' % t.gid, "name=" + qa(("%s %s" % (t.codigo, t.nombre)) if numerar else t.nombre)]
        if not t.resumen:
            attrs.append('color="%s"' % color_de(t))
        attrs += ['meeting="%s"' % ("true" if t.hito else "false"),
                  'start="%s"' % cal.dia(t.es).isoformat(),
                  'duration="%d"' % t.dur,
                  'complete="%d"' % max(0, min(100, int(t.raw.get("avance", 0))))]
        restr = None
        if t.retrasada:
            restr = cal.dia(t.es)
        elif t.raw.get("inicio_no_antes"):
            restr = cal.dia(cal.indice(fecha(t.raw["inicio_no_antes"])))
        if restr and not t.resumen:
            attrs += ['thirdDate="%s"' % restr.isoformat(), 'thirdDate-constraint="1"']
        if t.raw.get("prioridad") is not None:
            attrs.append('priority="%d"' % int(t.raw["prioridad"]))
        if t.raw.get("web"):
            attrs.append("webLink=" + qa(t.raw["web"]))
        attrs.append('expand="%s"' % ("true" if t.raw.get("expandir", True) else "false"))
        if not t.resumen:
            mo, mat = ctx["costos"][t.codigo]
            if mo or mat:
                attrs += ['cost-manual-value="%s"' % fmt_num(round(mo + mat, 2)), 'cost-calculated="false"']
        cuerpo = []
        n = notas_de(t)
        if n:
            cuerpo.append(sp + I + "<notes>%s</notes>" % cdata(n))
        for s, tipo, lag in sucesores.get(t.codigo, []):
            cuerpo.append(sp + I + '<depend id="%d" type="%d" difference="%d" hardness="Strong"/>'
                          % (s.gid, TIPOS_DEP[tipo], lag))
        if usar_cols and not t.contenedor:
            vals = columnas_de(t)
            for pid, clave, _, _ in COLUMNAS:
                if clave in vals:
                    cuerpo.append(sp + I + '<customproperty taskproperty-id="%s" value=%s/>' % (pid, qa(vals[clave])))
        if not cuerpo and not t.hijos:
            w(sp + "<task " + " ".join(attrs) + "/>")
            return
        w(sp + "<task " + " ".join(attrs) + ">")
        out.extend(cuerpo)
        for h in t.hijos:
            emitir(h, nivel + 1)
        w(sp + "</task>")

    for r in raices:
        emitir(r, 0)
    w(I + "</tasks>")

    # recursos
    if recursos:
        w(I + "<resources>")
        for r in recursos:
            base = (I * 2 + '<resource id="%d" name=%s function="%s" contacts=%s phone=%s'
                    % (r["id"], qa(r["nombre"]), r["rol_id"], qa(r.get("contacto", "")), qa(r.get("telefono", ""))))
            if r.get("costo"):
                w(base + ">")
                w(I * 3 + '<rate name="standard" value="%s"/>' % fmt_num(r["costo"]))
                w(I * 2 + "</resource>")
            else:
                w(base + "/>")
        w(I + "</resources>")
        w(I + "<allocations>")
        rol_de = {r["id"]: r["rol_id"] for r in recursos}
        for t, rid, carga, resp in asign:
            w(I * 2 + '<allocation task-id="%d" resource-id="%d" function="%s" responsible="%s" load="%.1f"/>'
              % (t.gid, rid, rol_de[rid], "true" if resp else "false", carga))
        w(I + "</allocations>")
    else:
        w(I + "<resources/>")
        w(I + "<allocations/>")
    w(I + "<vacations/>")

    # columnas visibles
    w(I + "<taskdisplaycolumns>")
    vis = {"tpd3": (0, 290), "tpd4": (1, 80), "tpd5": (2, 80), "tpd6": (3, 55)}
    if op.get("columna_predecesoras", True):
        vis["tpd9"] = (4, 80)
    orden = len(vis)
    cols_vis = op.get("columnas_visibles", ["computo", "cuadrilla", "costo_total", "holgura"])
    if usar_cols:
        for pid, clave, _, ancho in COLUMNAS:
            if clave in cols_vis:
                vis[pid] = (orden, ancho)
                orden += 1
    ids = ["tpd%d" % i for i in range(14)] + ([c[0] for c in COLUMNAS] if usar_cols else [])
    for pid in ids:
        if pid in vis:
            o, wd = vis[pid]
            w(I * 2 + '<displaycolumn property-id="%s" order="%d" width="%d" visible="true"/>' % (pid, o, wd))
        else:
            w(I * 2 + '<displaycolumn property-id="%s" order="-1" width="75" visible="false"/>' % pid)
    w(I + "</taskdisplaycolumns>")

    # linea base
    lb = op.get("linea_base", True)
    if lb:
        nombre_lb = lb if isinstance(lb, str) else "Línea base %s" % dt.date.today().strftime("%d/%m/%Y")
        w(I + "<previous>")
        w(I * 2 + "<previous-tasks name=%s>" % qa(nombre_lb))
        for t in todas:
            w(I * 3 + '<previous-task id="%d" start="%s" duration="%d" meeting="%s" super="%s"/>'
              % (t.gid, cal.dia(t.es).isoformat(), t.dur, "true" if t.hito else "false",
                 "true" if t.resumen else "false"))
        w(I * 2 + "</previous-tasks>")
        w(I + "</previous>")
    else:
        w(I + "<previous/>")
    w(I + '<roles roleset-name="Default"/>')
    if roles:
        w(I + "<roles>")
        for nombre, rid in roles.items():
            w(I * 2 + '<role id="%s" name=%s/>' % (rid, qa(nombre)))
        w(I + "</roles>")
    w("</project>")
    return "\n".join(out) + "\n"


def iter_hojas(t):
    if not t.hijos:
        yield t
    for h in t.hijos:
        for x in iter_hojas(h):
            yield x


# --------------------------------------------------------------------------- reportes

def series_semanales(ctx):
    cal, hojas, tipos = ctx["cal"], ctx["hojas"], ctx["tipos"]
    ini = min(t.es for t in hojas)
    fin = max(t.ef for t in hojas)
    uso = defaultdict(lambda: defaultdict(float))
    costo_dia = defaultdict(float)
    for t in hojas:
        mo, mat = ctx["costos"][t.codigo]
        dias = max(1, t.dur)
        for d in range(t.es, t.es + dias):
            costo_dia[d] += (mo + mat) / dias
        for k, q in t.recursos.items():
            for d in range(t.es, t.ef):
                uso[k][d] += q
    semanas = []
    for d in range(ini, max(fin, ini + 1)):
        f = cal.dia(d)
        lunes = f - dt.timedelta(days=f.weekday())
        if not semanas or semanas[-1]["semana"] != lunes:
            semanas.append({"semana": lunes, "dias": [], "costo": 0.0})
        semanas[-1]["dias"].append(d)
        semanas[-1]["costo"] += costo_dia[d]
    for s in semanas:
        s["pico"] = {k: max(uso[k][d] for d in s["dias"]) for k in tipos}
        s["dias_persona"] = {k: sum(uso[k][d] for d in s["dias"]) for k in tipos}
    return semanas, uso


def resumen_md(ctx):
    spec, cal, hojas, todas, tipos = ctx["spec"], ctx["cal"], ctx["hojas"], ctx["todas"], ctx["tipos"]
    P = spec["proyecto"]
    ini = min(t.es for t in hojas)
    fin = max(t.ef for t in hojas)
    L = ["# %s: resumen del cronograma\n" % P.get("nombre", "Proyecto")]
    L.append("Generado con generar_gan.py v%s\n" % __version__)
    L.append("| Dato | Valor |\n|---|---|")
    L.append("| Inicio | %s |" % cal.dia(ini).strftime("%d/%m/%Y"))
    L.append("| Fin (último día de trabajo) | %s |" % cal.dia(fin - 1).strftime("%d/%m/%Y"))
    L.append("| Duración | %d días hábiles (%d días corridos) |" % (fin - ini, (cal.dia(fin - 1) - cal.dia(ini)).days + 1))
    L.append("| Tareas | %d (%d de trabajo, %d hitos, %d grupos) |"
             % (len(todas), sum(1 for t in hojas if not t.hito), sum(1 for t in hojas if t.hito),
                len(todas) - len(hojas)))
    L.append("| Recursos creados | %d |" % len(ctx["recursos"]))
    tot_mo = sum(c[0] for c in ctx["costos"].values())
    tot_mat = sum(c[1] for c in ctx["costos"].values())
    if tot_mo or tot_mat:
        L.append("| Costo mano de obra y equipos | %s |" % fmt_dinero(tot_mo))
        L.append("| Costo materiales | %s |" % fmt_dinero(tot_mat))
        L.append("| **Costo total** | **%s** |" % fmt_dinero(tot_mo + tot_mat))
    crit = [t for t in sorted(hojas, key=lambda x: (x.es, x.codigo)) if t.critica and not t.abarca]
    L.append("\n## Camino crítico\n")
    L.append(" → ".join("%s %s" % (t.codigo, t.nombre) for t in crit) or "(ninguno)")
    hitos = [t for t in sorted(hojas, key=lambda x: x.es) if t.hito]
    if hitos:
        L.append("\n## Hitos\n\n| Hito | Fecha |\n|---|---|")
        for t in hitos:
            L.append("| %s %s | %s |" % (t.codigo, t.nombre, cal.dia(t.es).strftime("%d/%m/%Y")))
    L.append("\n## Plan de trabajos\n")
    L.append("| Código | Tarea | Dur. | Inicio | Fin | HT | HL | Cuadrilla | Costo |")
    L.append("|---|---|---:|---|---|---:|---:|---|---:|")
    for t in todas:
        if t.contenedor:
            continue
        fin_t = cal.dia(t.ef - 1) if t.dur > 0 else cal.dia(t.es)
        if t.resumen:
            c = sum(sum(ctx["costos"][h.codigo]) for h in iter_hojas(t))
            L.append("| **%s** | **%s** | %d | %s | %s | | | | %s |"
                     % (t.codigo, t.nombre, t.dur, cal.dia(t.es).strftime("%d/%m/%y"), fin_t.strftime("%d/%m/%y"),
                        fmt_dinero(c) if c else ""))
        else:
            c = sum(ctx["costos"][t.codigo])
            cu = " + ".join("%s %s" % (fmt_num(q), tipos[k].get("abrev", k)) for k, q in t.recursos.items())
            L.append("| %s | %s%s | %d | %s | %s | %s | %s | %s | %s |"
                     % (t.codigo, t.nombre, " ◆" if t.hito else "", t.dur, cal.dia(t.es).strftime("%d/%m/%y"),
                        fin_t.strftime("%d/%m/%y"), "" if t.abarca else ("%d%s" % (t.holgura, "●" if t.critica else "")),
                        "" if t.abarca else t.holgura_libre, cu, fmt_dinero(c) if c else ""))
    if tipos:
        semanas, uso = series_semanales(ctx)
        L.append("\n## Recursos\n")
        L.append("| Código | Recurso | Rol | Pico diario | Días-persona | Costo |")
        L.append("|---|---|---|---:|---:|---:|")
        for k, tp in tipos.items():
            pico = max(uso[k].values()) if uso[k] else 0
            dp = sum(uso[k].values())
            L.append("| %s | %s | %s | %s | %s | %s |" % (tp.get("abrev", k), tp["nombre"], tp["rol"], fmt_num(pico),
                                                         fmt_num(dp), fmt_dinero(dp * float(tp.get("costo_dia", 0)))))
        usados = [k for k in tipos if uso[k]]
        L.append("\n## Histograma semanal (pico diario de personas por semana)\n")
        L.append("| Semana | " + " | ".join(tipos[k].get("abrev", k) for k in usados) + " | Total |")
        L.append("|---|" + "---:|" * (len(usados) + 1))
        for s in semanas:
            vals = [s["pico"][k] for k in usados]
            L.append("| %s | %s | %s |" % (s["semana"].strftime("%d/%m/%y"), " | ".join(fmt_num(v) if v else "·" for v in vals),
                                           fmt_num(sum(vals))))
        if tot_mo or tot_mat:
            L.append("\n## Curva S (inversión acumulada)\n")
            L.append("| Semana | Costo semana | Acumulado | % |")
            L.append("|---|---:|---:|---:|")
            acum = 0
            total = tot_mo + tot_mat
            for s in semanas:
                acum += s["costo"]
                L.append("| %s | %s | %s | %.1f %% |" % (s["semana"].strftime("%d/%m/%y"), fmt_dinero(s["costo"]),
                                                         fmt_dinero(acum), 100.0 * acum / total))
    if ctx["avisos"]:
        L.append("\n## Avisos\n")
        L.extend("- " + a for a in ctx["avisos"])
    return "\n".join(L) + "\n"


def escribir_csv(ctx, carpeta):
    os.makedirs(carpeta, exist_ok=True)
    cal, tipos = ctx["cal"], ctx["tipos"]
    with open(os.path.join(carpeta, "tareas.csv"), "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow(["codigo", "tarea", "nivel", "tipo", "duracion", "inicio", "fin", "holgura_total",
                     "holgura_libre", "critica", "predecesoras", "recursos", "costo_mo", "costo_materiales"])
        for t in ctx["todas"]:
            if t.contenedor:
                continue
            fin_t = cal.dia(t.ef - 1) if t.dur > 0 else cal.dia(t.es)
            mo, mat = (0, 0) if t.resumen else ctx["costos"][t.codigo]
            wr.writerow([t.codigo, t.nombre, t.nivel, "grupo" if t.resumen else ("hito" if t.hito else "tarea"),
                         t.dur, cal.dia(t.es).isoformat(), fin_t.isoformat(),
                         "" if t.resumen else t.holgura, "" if t.resumen else t.holgura_libre,
                         "si" if (t.critica and not t.resumen) else "no",
                         " ".join("%s%s%s" % (c, tp, ("%+d" % lg) if lg else "") for c, tp, lg in t.pred),
                         " ".join("%s:%s" % (k, fmt_num(q)) for k, q in t.recursos.items()),
                         round(mo), round(mat)])
    if not tipos:
        return
    semanas, _ = series_semanales(ctx)
    with open(os.path.join(carpeta, "histograma_semanal.csv"), "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        wr.writerow(["semana"] + ["pico_" + k for k in tipos] + ["dias_persona_" + k for k in tipos])
        for s in semanas:
            wr.writerow([s["semana"].isoformat()] + [fmt_num(s["pico"][k]) for k in tipos]
                        + [fmt_num(s["dias_persona"][k]) for k in tipos])
    total = sum(sum(c) for c in ctx["costos"].values())
    if total:
        with open(os.path.join(carpeta, "curva_s.csv"), "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["semana", "costo_semana", "acumulado", "porcentaje"])
            acum = 0
            for s in semanas:
                acum += s["costo"]
                wr.writerow([s["semana"].isoformat(), round(s["costo"]), round(acum), round(100 * acum / total, 2)])


# --------------------------------------------------------------------------- API principal

def generar(spec):
    """Procesa la especificacion y devuelve el contexto (incluye ctx['xml'])."""
    if not isinstance(spec, dict) or "proyecto" not in spec:
        raise SpecError("falta el objeto 'proyecto'")
    if "inicio" not in spec["proyecto"]:
        raise SpecError("falta proyecto.inicio (AAAA-MM-DD)")
    if not spec.get("tareas"):
        raise SpecError("falta la lista 'tareas'")
    spec = json.loads(json.dumps(spec))  # copia
    op = spec.get("opciones", {})
    avisos = []
    inicio = fecha(spec["proyecto"]["inicio"])
    cal = Calendario(spec.get("calendario", {}), inicio)
    if not cal.es_habil(inicio):
        avisos.append("La fecha de inicio %s no es hábil; se usa %s." % (inicio, cal.dia(cal.indice(inicio))))
    inicio_idx = cal.indice(inicio)

    tipos, roles = {}, {}
    for r in spec.get("recursos", []):
        if "nombre" not in r:
            raise SpecError("recurso sin 'nombre': %r" % r)
        k = str(r.get("id") or r["nombre"])
        if k in tipos:
            raise SpecError("recurso duplicado: %s" % k)
        rol = r.get("rol", r["nombre"])
        roles.setdefault(rol, str(len(roles) + 1))
        tipos[k] = dict(r, rol=rol, rol_id=roles[rol])
        tipos[k].setdefault("abrev", k)

    if op.get("tarea_raiz", True):
        spec["tareas"] = [{"id": "0", "nombre": spec["proyecto"].get("nombre", "Proyecto"),
                           "_contenedor": True, "hijos": spec["tareas"]}]
    todas, hojas, codigos = construir(spec["tareas"])
    for t in hojas:
        t.dur = duracion_de(t)
        t.pred = [parse_pred(p) for p in (t.raw.get("predecesoras") or t.raw.get("pred") or [])]
        if t.abarca and t.pred:
            raise SpecError("la tarea permanente %s no puede tener predecesoras" % t.codigo)
        for c, _, _ in t.pred:
            if c not in codigos:
                raise SpecError("la tarea %s depende de '%s', que no existe" % (t.codigo, c))
            if codigos[c].resumen:
                raise SpecError("la tarea %s depende del grupo '%s': vincule a una tarea hoja" % (t.codigo, c))
            if codigos[c].abarca:
                raise SpecError("la tarea %s depende de la tarea permanente '%s'" % (t.codigo, c))
            if c == t.codigo:
                raise SpecError("la tarea %s depende de sí misma" % c)
        for k, q in (t.raw.get("recursos") or {}).items():
            if k not in tipos:
                raise SpecError("la tarea %s usa el recurso '%s' que no está definido en 'recursos'" % (t.codigo, k))
            if float(q) > 0:
                t.recursos[k] = float(q)
        if t.hito and t.recursos:
            avisos.append("El hito %s tiene recursos: se ignoran (duración 0)." % t.codigo)
            t.recursos = {}
    for t in todas:
        if t.resumen and (t.raw.get("predecesoras") or t.raw.get("pred")):
            raise SpecError("el grupo %s tiene predecesoras; póngalas en sus tareas hijas" % t.codigo)

    limites = {k: float(tp["disponibles"]) for k, tp in tipos.items() if tp.get("disponibles")}
    fin = programar(hojas, codigos, cal, inicio_idx, limites, op.get("nivelar", bool(limites)))
    raices = [t for t in todas if t.padre is None]
    for r in raices:
        resumir(r)
    for i, t in enumerate(todas):
        t.gid = i + 1

    modo = op.get("modo_recursos", "individual")
    if modo not in ("individual", "agrupado"):
        raise SpecError("opciones.modo_recursos debe ser 'individual' o 'agrupado'")
    recursos, asign = asignar_recursos(hojas, tipos, modo)

    # avisos de completitud
    con_succ = {c for t in hojas for c, _, _ in t.pred}
    finales = [t for t in hojas if t.codigo not in con_succ and not t.abarca]
    if len(finales) > 1:
        sueltas = [t.codigo for t in finales if t.ef < fin]
        if sueltas:
            avisos.append("Tareas sin sucesora que terminan antes del fin (cabos sueltos): " + ", ".join(sueltas))
    sin_rec = [t.codigo for t in hojas if not t.hito and not t.recursos]
    if sin_rec and tipos:
        avisos.append("Tareas sin recursos asignados: " + ", ".join(sin_rec))
    if not any(t.hito for t in hojas):
        avisos.append("El cronograma no tiene hitos (inicio, fin de estructura, entrega...).")

    ctx = dict(spec=spec, cal=cal, opciones=op, todas=todas, hojas=hojas, codigos=codigos, raices=raices,
               tipos=tipos, roles=roles, recursos=recursos, asign=asign, fin=fin, avisos=avisos,
               costos={t.codigo: costos(t, tipos) for t in hojas})
    ctx["xml"] = escribir_gan(ctx)
    ET.fromstring(ctx["xml"].encode("utf-8"))  # valida que el XML este bien formado
    return ctx


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Genera un cronograma .gan de GanttProject desde JSON")
    ap.add_argument("spec", help="archivo JSON de especificación")
    ap.add_argument("salida", help="archivo .gan a escribir")
    ap.add_argument("--resumen", help="escribe un resumen Markdown (camino crítico, recursos, curva S)")
    ap.add_argument("--csv", help="carpeta donde escribir tareas.csv, histograma_semanal.csv y curva_s.csv")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = ap.parse_args(argv)
    try:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        ctx = generar(spec)
    except json.JSONDecodeError as e:
        sys.stderr.write("ERROR: JSON inválido en %s: %s\n" % (args.spec, e))
        return 2
    except SpecError as e:
        sys.stderr.write("ERROR: %s\n" % e)
        return 1
    with open(args.salida, "w", encoding="utf-8", newline="\n") as f:
        f.write(ctx["xml"])
    if args.resumen:
        with open(args.resumen, "w", encoding="utf-8") as f:
            f.write(resumen_md(ctx))
    if args.csv:
        escribir_csv(ctx, args.csv)
    cal, hojas = ctx["cal"], ctx["hojas"]
    ini = min(t.es for t in hojas)
    print("OK -> %s" % args.salida)
    print("Tareas: %d (%d hojas, %d grupos) | Recursos: %d | Asignaciones: %d"
          % (len(ctx["todas"]), len(hojas), len(ctx["todas"]) - len(hojas), len(ctx["recursos"]), len(ctx["asign"])))
    print("Inicio %s | Fin %s | %d días hábiles" % (cal.dia(ini), cal.dia(ctx["fin"] - 1), ctx["fin"] - ini))
    total = sum(sum(c) for c in ctx["costos"].values())
    if total:
        print("Costo total: %s" % fmt_dinero(total))
    print("Camino crítico: " + " -> ".join(t.codigo for t in sorted(hojas, key=lambda x: (x.es, x.codigo))
                                          if t.critica and not t.abarca))
    retr = [t.codigo for t in hojas if t.retrasada]
    if retr:
        print("Retrasadas por nivelación: " + ", ".join(retr))
    for a in ctx["avisos"]:
        print("AVISO: " + a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
