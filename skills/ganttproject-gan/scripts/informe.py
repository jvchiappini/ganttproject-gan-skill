#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
informe.py - Informe tecnico profesional del cronograma (HTML listo para imprimir / PDF).

Toma la MISMA especificacion JSON que generar_gan.py, recalcula el cronograma con el
mismo motor y produce un documento A4 autocontenido (sin dependencias externas) con:

  1. Portada con datos de la obra, codigo de documento y revision.
  2. Resumen ejecutivo: indicadores clave y sintesis narrativa automatica.
  3. Bases de la programacion: calendario, feriados, recesos, criterios y supuestos.
  4. Estructura de desglose (EDT) con duracion, fechas, peso y costo por rubro.
  5. Diagrama de Gantt (pagina apaisada) con camino critico, hitos y dias no laborables.
  6. Camino critico, hitos y analisis de holguras.
  7. Plan de trabajos detallado (computo, rendimiento, cuadrilla, fechas, holguras, costo).
  8. Recursos: dotacion, histograma semanal por especialidad, recursos limitados.
  9. Costos: por rubro, plan de inversiones mensual y curva S.
 10. Observaciones y riesgos de programacion.
 11. Anexos: feriados, glosario y bloque de firmas.

Uso:
    python informe.py cronograma.json informe.html [--pdf informe.pdf]

Datos opcionales del informe en el JSON (objeto "informe"):
    {"documento": "PCO-01", "revision": "A", "cliente": "...", "ubicacion": "...",
     "elaboro": "Nombre - cargo", "reviso": "...", "aprobo": "...", "logo": "ruta.png",
     "moneda": "Gs.", "observaciones": ["texto", ...]}
"""
import argparse
import base64
import datetime as dt
import html
import json
import mimetypes
import os
import shutil
import subprocess
import sys
from collections import defaultdict, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generar_gan as G  # noqa: E402

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MESES_C = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS_N = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# paleta categorica validada (orden fijo, nunca ciclado)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTROS = "#9a9893"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
CRIT = "#c62828"
BARRA = "#2a78d6"

E = html.escape


def f_larga(d):
    return "%d de %s de %d" % (d.day, MESES[d.month - 1], d.year)


def f_corta(d):
    return d.strftime("%d/%m/%Y")


def money(x, moneda):
    return "%s %s" % (moneda, G.fmt_dinero(x)) if moneda else G.fmt_dinero(x)


def pct(x):
    return ("%.1f" % x).replace(".", ",") + " %"


def num(x):
    return G.fmt_num(x).replace(".", ",")


# --------------------------------------------------------------------------- datos derivados

def analizar(ctx):
    cal, hojas, todas, tipos = ctx["cal"], ctx["hojas"], ctx["todas"], ctx["tipos"]
    A = {}
    ini = min(t.es for t in hojas)
    fin = max(t.ef for t in hojas)
    A["ini"], A["fin"] = ini, fin
    A["f_ini"], A["f_fin"] = cal.dia(ini), cal.dia(fin - 1)
    A["habiles"] = fin - ini
    A["corridos"] = (A["f_fin"] - A["f_ini"]).days + 1
    A["costo_mo"] = sum(c[0] for c in ctx["costos"].values())
    A["costo_mat"] = sum(c[1] for c in ctx["costos"].values())
    A["costo"] = A["costo_mo"] + A["costo_mat"]
    trabajo = [t for t in hojas if not t.hito and not t.abarca]
    A["trabajo"] = trabajo
    A["hitos"] = sorted([t for t in hojas if t.hito], key=lambda t: (t.es, t.codigo))
    A["criticas"] = sorted([t for t in trabajo + A["hitos"] if t.critica], key=lambda t: (t.es, t.codigo))
    A["casi"] = sorted([t for t in trabajo if 0 < t.holgura <= 5], key=lambda t: t.holgura)
    A["retrasadas"] = [t for t in hojas if t.retrasada]
    A["permanentes"] = [t for t in hojas if t.abarca]
    # uso diario por tipo
    uso = defaultdict(lambda: defaultdict(float))
    for t in hojas:
        for k, q in t.recursos.items():
            for d in range(t.es, t.ef):
                uso[k][d] += q
    A["uso"] = uso
    personal = [k for k, tp in tipos.items() if tp["rol"].lower() not in ("equipos", "equipo", "maquinaria")]
    A["personal"] = personal
    tot_dia = {d: sum(uso[k][d] for k in personal) for d in range(ini, fin)}
    A["pico_personal"] = max(tot_dia.values()) if tot_dia else 0
    A["dia_pico"] = cal.dia(max(tot_dia, key=tot_dia.get)) if tot_dia else A["f_ini"]
    A["prom_personal"] = (sum(tot_dia.values()) / len(tot_dia)) if tot_dia else 0
    A["dias_persona"] = sum(tot_dia.values())
    # rubros (nivel 1 bajo la raiz)
    raiz = [t for t in todas if t.contenedor]
    A["rubros"] = raiz[0].hijos if raiz else [t for t in todas if t.padre is None]
    A["costo_rubro"] = OrderedDict((r.codigo, sum(sum(ctx["costos"][h.codigo]) for h in G.iter_hojas(r)))
                                   for r in A["rubros"])
    # semanas y meses
    semanas, _ = G.series_semanales(ctx)
    A["semanas"] = semanas
    meses = OrderedDict()
    for s in semanas:
        for d in s["dias"]:
            f = cal.dia(d)
            meses.setdefault((f.year, f.month), 0.0)
    costo_dia = defaultdict(float)
    for t in hojas:
        mo, mat = ctx["costos"][t.codigo]
        n = max(1, t.dur)
        for d in range(t.es, t.es + n):
            costo_dia[d] += (mo + mat) / n
    for d, c in costo_dia.items():
        f = cal.dia(d)
        meses[(f.year, f.month)] = meses.get((f.year, f.month), 0.0) + c
    A["meses"] = meses
    # grupos de especialidad (por rol) para el histograma
    roles = OrderedDict()
    for k in personal:
        roles.setdefault(tipos[k]["rol"], []).append(k)
    A["roles"] = roles
    return A


# --------------------------------------------------------------------------- SVG helpers

def svg_open(w, h, label):
    return ('<svg class="chart" viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s" '
            'xmlns="http://www.w3.org/2000/svg" font-family="inherit">' % (w, h, E(label)))


def t_svg(x, y, s, size=10, fill=INK2, anchor="start", weight="400", extra=""):
    return ('<text x="%.1f" y="%.1f" font-size="%s" fill="%s" text-anchor="%s" font-weight="%s" %s>%s</text>'
            % (x, y, size, fill, anchor, weight, extra, E(str(s))))


def nice_max(v):
    if v <= 0:
        return 1
    import math
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * e:
            return m * e
    return 10 * e


# --------------------------------------------------------------------------- Gantt

def gantt_svgs(ctx, A, filas_por_pagina=40):
    cal = ctx["cal"]
    filas = [t for t in ctx["todas"] if not t.contenedor]
    d0 = A["f_ini"] - dt.timedelta(days=A["f_ini"].weekday())
    d1 = A["f_fin"] + dt.timedelta(days=6 - A["f_fin"].weekday())
    ndias = (d1 - d0).days + 1
    LBL, W = 300, 1060
    TW = W - LBL - 10
    px = TW / ndias
    RH, HEAD = 13.2, 34

    def x_de(f):
        return LBL + (f - d0).days * px

    import math
    n_pag = max(1, math.ceil(len(filas) / float(filas_por_pagina)))
    por = math.ceil(len(filas) / float(n_pag))  # reparto parejo entre hojas
    paginas = [filas[i:i + por] for i in range(0, len(filas), por)]
    out = []
    for n, pag in enumerate(paginas):
        H = HEAD + RH * len(pag) + 8
        s = [svg_open(W, H, "Diagrama de Gantt, hoja %d de %d" % (n + 1, len(paginas)))]
        # dias no laborables
        f = d0
        while f <= d1:
            if not cal.es_habil(f):
                s.append('<rect x="%.2f" y="%d" width="%.2f" height="%.1f" fill="#f1f0ec"/>'
                         % (x_de(f), HEAD, max(px, 0.6), H - HEAD - 4))
            f += dt.timedelta(days=1)
        # meses
        f = dt.date(d0.year, d0.month, 1)
        while f <= d1:
            sig = dt.date(f.year + (f.month == 12), f.month % 12 + 1, 1)
            xa, xb = max(x_de(f), LBL), min(x_de(sig), LBL + TW)
            if xb > xa:
                s.append('<rect x="%.1f" y="0" width="%.1f" height="15" fill="#f6f5f2" stroke="#ffffff" stroke-width="2"/>'
                         % (xa, xb - xa))
                if xb - xa > 34:
                    s.append(t_svg((xa + xb) / 2, 11, "%s %d" % (MESES_C[f.month - 1].capitalize(), f.year),
                                   9, INK, "middle", "600"))
                s.append('<line x1="%.1f" y1="0" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.8"/>'
                         % (xa, xa, H - 4, "#d3d2cc"))
            f = sig
        # semanas
        f, k = d0, 1
        while f <= d1:
            x = x_de(f)
            s.append('<line x1="%.1f" y1="17" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="0.4"/>' % (x, x, H - 4, GRID))
            if px * 7 > 16:
                s.append(t_svg(x + 3.5 * px, 28, "S%d" % k, 7, MUTED, "middle"))
            f += dt.timedelta(days=7)
            k += 1
        s.append(t_svg(4, 28, "Código · Tarea", 8.5, INK2, "start", "600"))
        s.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="1"/>' % (HEAD - 2, W, HEAD - 2, "#bdbcb6"))
        for i, t in enumerate(pag):
            y = HEAD + i * RH
            if i % 2 == 1:
                s.append('<rect x="0" y="%.1f" width="%d" height="%.1f" fill="#000" fill-opacity="0.025"/>' % (y, W, RH))
            ind = 4 + 9 * max(0, t.nivel - 1)
            nombre = "%s  %s" % (t.codigo, t.nombre)
            maxc = int((LBL - ind - 6) / 4.6)
            if len(nombre) > maxc:
                nombre = nombre[:maxc - 1] + "…"
            s.append(t_svg(ind, y + RH - 3.6, nombre, 8.2 if not t.resumen else 8.4,
                           INK if t.resumen else INK2, "start", "700" if t.resumen else "400"))
            fa = cal.dia(t.es)
            fb = cal.dia(t.ef - 1) if t.dur > 0 else fa
            xa, xb = x_de(fa), x_de(fb) + px
            tip = "%s %s | %s → %s | %d días hábiles%s" % (
                t.codigo, t.nombre, f_corta(fa), f_corta(fb), t.dur,
                "" if t.resumen or t.abarca else " | holgura %d%s" % (t.holgura, " (crítica)" if t.critica else ""))
            if t.hito:
                cx, cy, r = x_de(fa), y + RH / 2, 4.6
                s.append('<path d="M%.1f %.1f L%.1f %.1f L%.1f %.1f L%.1f %.1f Z" fill="%s" stroke="#fff" stroke-width="1"><title>%s</title></path>'
                         % (cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy, CRIT if t.critica else INK, E(tip)))
                s.append(t_svg(cx + 7, cy + 3, f_corta(fa)[:5], 7, MUTED))
            elif t.resumen:
                yb = y + 3.4
                s.append('<g><title>%s</title><rect x="%.1f" y="%.1f" width="%.1f" height="3.4" fill="%s"/>'
                         '<path d="M%.1f %.1f l0 6 l4 -2.6 Z M%.1f %.1f l0 6 l-4 -2.6 Z" fill="%s"/></g>'
                         % (E(tip), xa, yb, max(1, xb - xa), INK, xa, yb, xb, yb, INK))
            else:
                col = "#b9c6d3" if t.abarca else (CRIT if t.critica else BARRA)
                s.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"><title>%s</title></rect>'
                         % (xa, y + 2.6, max(1.5, xb - xa), RH - 5.2, col, E(tip)))
                if t.avance_pct:
                    s.append('<rect x="%.1f" y="%.1f" width="%.1f" height="2" fill="%s"/>'
                             % (xa, y + RH / 2 - 1, max(1, (xb - xa) * t.avance_pct / 100.0), INK))
        s.append("</svg>")
        out.append("".join(s))
    return out


def leyenda_gantt():
    def item(sw, txt):
        return '<span class="lg-item">%s<span>%s</span></span>' % (sw, E(txt))
    sw_bar = '<svg width="22" height="10"><rect y="1" width="22" height="8" rx="2" fill="%s"/></svg>'
    sw_res = ('<svg width="22" height="10"><rect y="2" width="22" height="3.4" fill="%s"/>'
              '<path d="M0 2 l0 6 l4 -2.6Z M22 2 l0 6 l-4 -2.6Z" fill="%s"/></svg>' % (INK, INK))
    sw_hito = '<svg width="12" height="12"><path d="M6 1 L11 6 L6 11 L1 6Z" fill="%s"/></svg>'
    sw_nl = '<svg width="14" height="10"><rect width="14" height="10" fill="#f1f0ec" stroke="#d3d2cc"/></svg>'
    return ('<div class="legend">' + item(sw_bar % BARRA, "Tarea") + item(sw_bar % CRIT, "Tarea crítica (holgura 0)")
            + item(sw_bar % "#b9c6d3", "Tarea permanente") + item(sw_res, "Rubro / resumen")
            + item(sw_hito % INK, "Hito") + item(sw_hito % CRIT, "Hito crítico")
            + item(sw_nl, "Día no laborable / feriado") + "</div>")


# --------------------------------------------------------------------------- graficos

def histograma_svg(ctx, A):
    semanas, tipos, roles = A["semanas"], ctx["tipos"], A["roles"]
    grupos = list(roles.items())
    if len(grupos) > 7:
        resto = [k for _, ks in grupos[7:] for k in ks]
        nombre = grupos[7][0] if len(grupos) == 8 else "Otros"
        grupos = grupos[:7] + [(nombre, resto)]
    otros_idx = 7
    W, H, L, B, T = 700, 280, 44, 38, 12
    PW, PH = W - L - 12, H - B - T
    serie = []
    for s in semanas:
        n = len(s["dias"])
        serie.append([sum(s["dias_persona"][k] for k in ks) / n for _, ks in grupos])
    vmax = nice_max(max([sum(v) for v in serie] + [1]))
    bw = PW / max(1, len(semanas))
    o = [svg_open(W, H, "Histograma semanal de personal por especialidad")]
    for i in range(6):
        v = vmax * i / 5
        y = T + PH - PH * v / vmax
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="%s"/>'
                 % (L, y, W - 12, y, GRID if i else "#bdbcb6", 0.8 if i else 1))
        o.append(t_svg(L - 6, y + 3, num(round(v, 1)), 9, MUTED, "end"))
    o.append(t_svg(12, T + PH / 2, "personas / día", 9, INK2, "middle", "400",
                   'transform="rotate(-90 12 %.1f)"' % (T + PH / 2)))
    for j, (s, vals) in enumerate(zip(semanas, serie)):
        x = L + j * bw + 1
        y = T + PH
        tip = ["Semana del %s" % f_corta(s["semana"])]
        for gi, ((nom, _), v) in enumerate(zip(grupos, vals)):
            if v <= 0:
                continue
            h = PH * v / vmax
            color = SERIES[gi] if gi < otros_idx else OTROS
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="#fff" stroke-width="1"/>'
                     % (x, y - h, max(1, bw - 2), h, color))
            y -= h
            tip.append("%s: %s" % (nom, num(round(v, 1))))
        tip.append("Total: %s" % num(round(sum(vals), 1)))
        o.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="transparent"><title>%s</title></rect>'
                 % (x - 1, T, bw, PH, E("\n".join(tip))))
        if j % max(1, len(semanas) // 10) == 0:
            o.append(t_svg(x + bw / 2, H - B + 14, s["semana"].strftime("%d/%m"), 8.5, MUTED, "middle"))
    o.append(t_svg(L + PW / 2, H - 6, "semana (lunes)", 9, INK2, "middle"))
    o.append("</svg>")
    leg = '<div class="legend">' + "".join(
        '<span class="lg-item"><i style="background:%s"></i><span>%s</span></span>'
        % (SERIES[i] if i < otros_idx else OTROS, E(n)) for i, (n, _) in enumerate(grupos)) + "</div>"
    return '<div class="fig">' + leg + "".join(o) + "</div>"


def recurso_limitado_svg(ctx, A, k):
    tp = ctx["tipos"][k]
    uso = A["uso"][k]
    ini, fin = A["ini"], A["fin"]
    lim = float(tp.get("disponibles", 0))
    W, H, L, B, T = 480, 170, 34, 26, 18
    PW, PH = W - L - 10, H - B - T
    import math
    pico = max([lim] + list(uso.values()) + [1])
    vmax = max(2, int(math.ceil(pico)) + 1)
    paso = 1 if vmax <= 6 else (2 if vmax <= 12 else 5)
    vmax = int(math.ceil(vmax / float(paso)) * paso)
    n = max(1, fin - ini)
    o = [svg_open(W, H, "Uso diario de %s" % tp["nombre"])]
    for v in range(0, vmax + 1, paso):
        y = T + PH - PH * v / vmax
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="0.8"/>' % (L, y, W - 10, y, GRID))
        o.append(t_svg(L - 5, y + 3, str(v), 8.5, MUTED, "end"))
    pts = []
    for d in range(ini, fin):
        x0 = L + (d - ini) * PW / n
        x1 = L + (d - ini + 1) * PW / n
        y = T + PH - PH * uso.get(d, 0) / vmax
        pts += ["%.1f,%.1f" % (x0, y), "%.1f,%.1f" % (x1, y)]
    o.append('<polygon points="%.1f,%.1f %s %.1f,%.1f" fill="%s" fill-opacity="0.18"/>'
             % (L, T + PH, " ".join(pts), L + PW, T + PH, SERIES[0]))
    o.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (" ".join(pts), SERIES[0]))
    if lim:
        y = T + PH - PH * lim / vmax
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1.4" stroke-dasharray="5 3"/>'
                 % (L, y, W - 10, y, INK))
        o.append(t_svg(W - 12, y - 4, "Disponibles: %s" % num(lim), 8.5, INK, "end", "600"))
    o.append(t_svg(L, 11, tp["nombre"], 10, INK, "start", "700"))
    o.append(t_svg(L, H - 8, f_corta(A["f_ini"]), 8.5, MUTED))
    o.append(t_svg(W - 10, H - 8, f_corta(A["f_fin"]), 8.5, MUTED, "end"))
    o.append("</svg>")
    return "".join(o)


def curva_s_svg(A, moneda):
    meses = list(A["meses"].items())
    total = A["costo"] or 1
    W, H, L, B, T = 700, 260, 46, 34, 14
    PW, PH = W - L - 56, H - B - T
    n = len(meses)
    o = [svg_open(W, H, "Curva S: avance económico acumulado previsto")]
    for i in range(6):
        v = 20 * i
        y = T + PH - PH * v / 100
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="%s"/>'
                 % (L, y, L + PW, y, GRID if i else "#bdbcb6", 0.8 if i else 1))
        o.append(t_svg(L - 6, y + 3, "%d %%" % v, 9, MUTED, "end"))
    acum, pts = 0.0, ["%.1f,%.1f" % (L, T + PH)]
    for i, ((y_, m), c) in enumerate(meses):
        acum += c
        x = L + PW * (i + 1) / n
        y = T + PH - PH * (acum / total)
        pts.append("%.1f,%.1f" % (x, y))
        o.append(t_svg(L + PW * (i + 0.5) / n, H - B + 15, "%s %s" % (MESES_C[m - 1], str(y_)[2:]), 9, MUTED, "middle"))
    o.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-linejoin="round"/>'
             % (" ".join(pts), SERIES[0]))
    acum = 0.0
    for i, ((y_, m), c) in enumerate(meses):
        acum += c
        x = L + PW * (i + 1) / n
        y = T + PH - PH * (acum / total)
        o.append('<circle cx="%.1f" cy="%.1f" r="4" fill="%s" stroke="#fff" stroke-width="2"><title>%s</title></circle>'
                 % (x, y, SERIES[0], E("Fin de %s %d: %s acumulado (%s)" % (MESES[m - 1], y_, money(acum, moneda),
                                                                          pct(100 * acum / total)))))
        if i in (n // 4, n // 2, (3 * n) // 4) or i == n - 1:
            o.append(t_svg(x + 7, y + 12, pct(100 * acum / total), 9, INK, "start", "600"))
    o.append("</svg>")
    return "".join(o)


def barras_h_svg(items, moneda, label):
    """items: [(etiqueta, valor)] ya ordenados."""
    if not items:
        return ""
    W, RH, L = 700, 21, 250
    H = RH * len(items) + 10
    vmax = max(v for _, v in items) or 1
    tot = sum(v for _, v in items) or 1
    o = [svg_open(W, H, label)]
    for i, (lab, v) in enumerate(items):
        y = 4 + i * RH
        w = (W - L - 170) * v / vmax
        o.append(t_svg(L - 10, y + 14, lab if len(lab) < 42 else lab[:40] + "…", 10, INK2, "end"))
        o.append('<rect x="%d" y="%.1f" width="%.1f" height="12" rx="2" fill="%s"><title>%s</title></rect>'
                 % (L, y + 4, max(1, w), SERIES[0], E("%s: %s" % (lab, money(v, moneda)))))
        o.append(t_svg(L + w + 8, y + 14, "%s  ·  %s" % (money(v, moneda), pct(100 * v / tot)), 9.5, INK, "start"))
    o.append("</svg>")
    return "".join(o)


def barras_mensuales_svg(A, moneda):
    meses = list(A["meses"].items())
    W, H, L, B, T = 700, 220, 46, 32, 22
    PW, PH = W - L - 12, H - B - T
    vmax = nice_max(max([c for _, c in meses] + [1]))
    bw = PW / max(1, len(meses))
    o = [svg_open(W, H, "Inversión mensual prevista")]
    for i in range(5):
        v = vmax * i / 4
        y = T + PH - PH * v / vmax
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="0.8"/>' % (L, y, W - 12, y, GRID))
        o.append(t_svg(L - 6, y + 3, abrev(v), 9, MUTED, "end"))
    for i, ((y_, m), c) in enumerate(meses):
        h = PH * c / vmax
        x = L + i * bw + bw * 0.2
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s"><title>%s</title></rect>'
                 % (x, T + PH - h, bw * 0.6, h, SERIES[0], E("%s %d: %s" % (MESES[m - 1], y_, money(c, moneda)))))
        o.append(t_svg(x + bw * 0.3, T + PH - h - 5, abrev(c), 9, INK, "middle", "600"))
        o.append(t_svg(x + bw * 0.3, H - B + 15, "%s %s" % (MESES_C[m - 1], str(y_)[2:]), 9, MUTED, "middle"))
    o.append("</svg>")
    return "".join(o)


def abrev(v):
    if v >= 1e9:
        return num(round(v / 1e9, 2)) + " MM"
    if v >= 1e6:
        return num(round(v / 1e6, 1)) + " M"
    if v >= 1e3:
        return num(round(v / 1e3)) + " k"
    return num(round(v))


# --------------------------------------------------------------------------- HTML

CSS = """
:root{--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;--line2:#cfcdc6;--paper:#ffffff;
--bg:#ecebe7;--accent:#1d3557;--accent2:#2a78d6;--crit:#c62828;--soft:#f6f5f2;}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;color:var(--ink);background:var(--bg);font:10.5pt/1.45 "Segoe UI","Helvetica Neue",Arial,sans-serif;
-webkit-print-color-adjust:exact;print-color-adjust:exact;font-variant-numeric:tabular-nums}
.page{background:var(--paper);width:210mm;min-height:297mm;margin:12mm auto;padding:18mm 17mm 20mm;
box-shadow:0 2px 14px rgba(0,0,0,.12);position:relative}
.page.land{width:297mm;min-height:210mm;padding:12mm 12mm 14mm}
h1,h2,h3{color:var(--accent);margin:0;font-weight:700;letter-spacing:-.01em}
h2{font-size:17pt;padding-bottom:6px;border-bottom:2px solid var(--accent);margin:0 0 14px}
h2 .n{color:var(--accent2);margin-right:8px}
h3{font-size:11.5pt;margin:18px 0 8px}
p{margin:0 0 8px}
.muted{color:var(--muted)} .ink2{color:var(--ink2)} .small{font-size:8.5pt}
table{width:100%;border-collapse:collapse;font-size:8.6pt;margin:6px 0 12px}
th{background:var(--accent);color:#fff;font-weight:600;text-align:left;padding:5px 6px;white-space:nowrap}
td{padding:4px 6px;border-bottom:1px solid var(--line);vertical-align:top}
tr.grp td{background:var(--soft);font-weight:700;color:var(--accent);border-bottom:1px solid var(--line2)}
tr.crit td:first-child{box-shadow:inset 3px 0 0 var(--crit)}
td.r,th.r{text-align:right;white-space:nowrap} td.c,th.c{text-align:center;white-space:nowrap}
thead{display:table-header-group} tr{break-inside:avoid}
.tag{display:inline-block;font-size:7.5pt;font-weight:700;padding:1px 6px;border-radius:9px;letter-spacing:.02em}
.tag.crit{background:#fdecea;color:var(--crit)} .tag.ok{background:#e8f3ec;color:#1e6b3a}
.tag.warn{background:#fff4df;color:#8a5a00} .tag.info{background:#e9f1fb;color:#1c5cab}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:6px 0 16px}
.kpi{border:1px solid var(--line);border-top:3px solid var(--accent);padding:9px 11px;border-radius:4px;background:#fff}
.kpi .l{font-size:7.8pt;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
.kpi .v{font-size:15pt;font-weight:700;color:var(--ink);margin-top:2px;line-height:1.15}
.kpi .s{font-size:8pt;color:var(--ink2)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:8.5pt;color:var(--ink2);margin:4px 0 8px}
.lg-item{display:inline-flex;align-items:center;gap:6px}
.lg-item i{display:inline-block;width:12px;height:12px;border-radius:2px}
.chart{display:block;margin:4px 0 10px;overflow:visible}
.fig{break-inside:avoid}
table.plan{font-size:8.1pt} table.plan td:nth-child(2){min-width:58mm} table.plan td:nth-child(5){min-width:24mm}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px}
.box{border:1px solid var(--line);border-left:3px solid var(--accent2);background:var(--soft);padding:10px 12px;margin:8px 0 12px;border-radius:3px}
.box.crit{border-left-color:var(--crit)}
.cover{display:flex;flex-direction:column;justify-content:space-between;padding:0!important;overflow:hidden}
.cover .band{background:var(--accent);color:#fff;padding:22mm 18mm 16mm}
.cover .band .kicker{font-size:9pt;letter-spacing:.22em;text-transform:uppercase;opacity:.8;font-weight:600}
.cover .band h1{color:#fff;font-size:30pt;line-height:1.1;margin:10px 0 8px}
.cover .band .sub{font-size:12pt;opacity:.92;max-width:150mm}
.cover .stripe{height:6px;background:linear-gradient(90deg,#2a78d6 0 40%,#eb6834 40% 55%,#1baf7a 55% 70%,#eda100 70% 85%,#e87ba4 85%)}
.cover .body{padding:14mm 18mm;flex:1}
.cover .meta{display:grid;grid-template-columns:44mm 1fr;gap:6px 10px;font-size:10pt;margin-top:6mm}
.cover .meta div:nth-child(odd){color:var(--muted);text-transform:uppercase;font-size:8pt;letter-spacing:.08em;padding-top:2px;font-weight:600}
.cover .foot{padding:8mm 18mm 12mm;border-top:1px solid var(--line);display:flex;justify-content:space-between;font-size:8.5pt;color:var(--ink2)}
.cover img.logo{max-height:22mm;max-width:60mm;margin-bottom:6mm;background:#fff;padding:4px;border-radius:3px}
.toc{list-style:none;padding:0;margin:0;counter-reset:t}
.toc li{display:flex;gap:10px;padding:7px 0;border-bottom:1px dotted var(--line2);font-size:11pt}
.toc li b{color:var(--accent2);width:26px}
.pghead{display:flex;justify-content:space-between;font-size:7.8pt;color:var(--muted);border-bottom:1px solid var(--line);
padding-bottom:5px;margin-bottom:12px;text-transform:uppercase;letter-spacing:.06em}
.pgfoot{margin-top:18px;display:flex;justify-content:space-between;font-size:7.8pt;color:var(--muted);border-top:1px solid var(--line);padding-top:4px}
.sign{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px}
.sign>div{font-size:9pt;display:flex;flex-direction:column;break-inside:avoid}
.sign .sp{height:24mm;border-bottom:1px solid var(--ink);margin-bottom:6px}
.sign b{display:block;color:var(--accent);font-size:8pt;text-transform:uppercase;letter-spacing:.08em}
.sign .fd{color:var(--muted);font-size:8pt;margin-top:10px}
.printbar{position:sticky;top:0;z-index:5;background:var(--accent);color:#fff;padding:8px 16px;display:flex;justify-content:space-between;align-items:center;font-size:10pt}
.printbar button{background:#fff;color:var(--accent);border:0;padding:6px 14px;border-radius:4px;font-weight:700;cursor:pointer;font:inherit;font-weight:700}
ul.clean{margin:4px 0 10px 18px;padding:0} ul.clean li{margin:2px 0}
@media print{
 html,body{background:#fff}
 .printbar,.pghead,.pgfoot{display:none}
 .page,.page.land{margin:0;box-shadow:none;width:auto;min-height:0;padding:0;break-before:page}
 .page:first-of-type{break-before:auto}
 .page.cover{page:cover;height:297mm}
 .page.land{page:land}
 h2,h3{break-after:avoid} .chart,.kpis,.box,.sign{break-inside:avoid}
}
@media screen and (max-width:900px){.page,.page.land{width:auto;margin:0;padding:16px}.kpis{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
"""


class Doc:
    def __init__(self, meta):
        self.meta = meta
        self.pages = []

    def page(self, body, cls="", head=True):
        h = ""
        if head:
            h = ('<div class="pghead"><span>%s</span><span>%s · Rev. %s</span></div>'
                 % (E(self.meta["obra"]), E(self.meta["documento"]), E(self.meta["revision"])))
        self.pages.append((cls, h + body))

    def render(self, titulo):
        n = len(self.pages)
        partes = []
        for i, (cls, body) in enumerate(self.pages, 1):
            foot = "" if "cover" in cls else (
                '<div class="pgfoot"><span>%s · Programación y control de obra</span><span>Hoja %d</span></div>'
                % (E(self.meta["documento"]), i))
            partes.append('<section class="page %s">%s%s</section>' % (cls, body, foot))
        return ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>%s</title><style>%s%s</style></head><body>'
                '<div class="printbar"><span>%s</span><button onclick="window.print()">Imprimir / Guardar PDF</button></div>'
                '%s</body></html>' % (E(titulo), CSS, self.page_css(), E(titulo), "".join(partes)))

    def page_css(self):
        def q(x):
            return '"%s"' % x.replace("\\", "").replace('"', "'")
        izq = q("%s · %s · Rev. %s" % (self.meta["obra"], self.meta["documento"], self.meta["revision"]))
        box = ('font:8pt "Segoe UI",Arial,sans-serif;color:#8a8984')
        return ("@page{size:A4 portrait;margin:17mm 16mm 18mm;"
                "@top-left{content:%s;%s;vertical-align:bottom;padding-bottom:3mm}"
                "@top-right{content:'Programación y control de obra';%s;vertical-align:bottom;padding-bottom:3mm}"
                "@bottom-left{content:'Generado con ganttproject-gan';%s;vertical-align:top;padding-top:3mm}"
                "@bottom-right{content:'Página ' counter(page) ' de ' counter(pages);%s;vertical-align:top;padding-top:3mm}}"
                "@page land{size:A4 landscape;margin:11mm 11mm 12mm}"
                "@page cover{margin:0;@top-left{content:none}@top-right{content:none}"
                "@bottom-left{content:none}@bottom-right{content:none}}" % (izq, box, box, box, box))


def tabla(cab, filas, clases=None, alin=None, cls_tabla=""):
    alin = alin or {}
    th = "".join('<th class="%s">%s</th>' % (alin.get(i, ""), E(c)) for i, c in enumerate(cab))
    trs = []
    for j, f in enumerate(filas):
        cls = (clases[j] if clases else "") or ""
        trs.append('<tr class="%s">%s</tr>' % (cls, "".join('<td class="%s">%s</td>' % (alin.get(i, ""), c)
                                                         for i, c in enumerate(f))))
    return '<table class="%s"><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (cls_tabla, th, "".join(trs))


def construir(ctx, spec, base_dir):
    for t in ctx["todas"]:
        t.avance_pct = max(0, min(100, int(t.raw.get("avance", 0)))) if not t.resumen else 0
    A = analizar(ctx)
    cal, tipos, op = ctx["cal"], ctx["tipos"], ctx["opciones"]
    P = spec["proyecto"]
    inf = spec.get("informe", {})
    moneda = inf.get("moneda", "Gs.")
    hoy = dt.date.today()
    meta = {"obra": P.get("nombre", "Proyecto"), "documento": inf.get("documento", "PCO-01"),
            "revision": inf.get("revision", "A")}
    doc = Doc(meta)
    n_sec = [0]

    def sec(titulo):
        n_sec[0] += 1
        return '<h2><span class="n">%d.</span>%s</h2>' % (n_sec[0], E(titulo))

    # ---------------- portada
    logo = ""
    if inf.get("logo"):
        ruta = inf["logo"] if os.path.isabs(inf["logo"]) else os.path.join(base_dir, inf["logo"])
        if os.path.exists(ruta):
            mime = mimetypes.guess_type(ruta)[0] or "image/png"
            with open(ruta, "rb") as fh:
                logo = '<img class="logo" alt="logo" src="data:%s;base64,%s">' % (mime, base64.b64encode(fh.read()).decode())
    filas_meta = [("Obra", meta["obra"]), ("Comitente", inf.get("cliente", "")), ("Ubicación", inf.get("ubicacion", "")),
                  ("Empresa", P.get("empresa", "")), ("Inicio previsto", f_larga(A["f_ini"])),
                  ("Fin previsto", f_larga(A["f_fin"])),
                  ("Plazo", "%d días hábiles · %d días corridos" % (A["habiles"], A["corridos"])),
                  ("Presupuesto MO + equipos" if not A["costo_mat"] else "Presupuesto", money(A["costo"], moneda) if A["costo"] else ""),
                  ("Elaboró", inf.get("elaboro", "")), ("Fecha de emisión", f_larga(hoy))]
    meta_html = "".join("<div>%s</div><div>%s</div>" % (E(a), E(b)) for a, b in filas_meta if b)
    doc.page('<div class="band">%s<div class="kicker">Informe técnico · Programación de obra</div>'
             '<h1>%s</h1><div class="sub">Cronograma de ejecución, camino crítico, diagrama de recursos y plan de inversiones</div></div>'
             '<div class="stripe"></div><div class="body">%s<div class="meta">%s</div></div>'
             '<div class="foot"><span>Documento %s · Revisión %s</span><span>Generado con ganttproject-gan v%s</span></div>'
             % (logo, E(meta["obra"]), ('<p class="ink2">%s</p>' % E(P["descripcion"])) if P.get("descripcion") else "",
                meta_html, E(meta["documento"]), E(meta["revision"]), G.__version__),
             cls="cover", head=False)

    # ---------------- indice
    secciones = ["Resumen ejecutivo", "Bases de la programación", "Estructura de desglose del trabajo (EDT)",
                 "Diagrama de Gantt", "Camino crítico, hitos y holguras", "Plan de trabajos detallado",
                 "Recursos", "Costos e inversiones", "Observaciones y riesgos", "Anexos"]
    doc.page('<h2>Contenido</h2><ol class="toc">%s</ol>'
             '<div class="box" style="margin-top:18px"><b>Cómo leer este informe.</b> Las fechas se expresan en días '
             'hábiles según el calendario de la sección 2. Las tareas críticas (holgura total 0) están marcadas en rojo: '
             'cualquier atraso en ellas posterga la entrega. El archivo <i>.gan</i> que acompaña a este informe se abre con '
             'GanttProject y contiene la misma programación, con recursos asignados y la línea base guardada.</div>'
             % "".join("<li><b>%d</b><span>%s</span></li>" % (i + 1, E(s)) for i, s in enumerate(secciones)))

    # ---------------- 1 resumen ejecutivo
    rub_max = max(A["costo_rubro"].items(), key=lambda kv: kv[1]) if A["costo"] else None
    cod_rub = {r.codigo: r for r in A["rubros"]}
    kpis = [
        ("Plazo de obra", "%d días" % A["habiles"], "hábiles · %d corridos" % A["corridos"]),
        ("Inicio", f_corta(A["f_ini"]), DIAS_N[A["f_ini"].weekday()]),
        ("Fin previsto", f_corta(A["f_fin"]), DIAS_N[A["f_fin"].weekday()]),
        ("Presupuesto", abrev(A["costo"]) if A["costo"] else "—", moneda + (" (MO + equipos)" if not A["costo_mat"] else "")),
        ("Tareas", str(len(A["trabajo"])), "%d rubros · %d hitos" % (len(A["rubros"]), len(A["hitos"]))),
        ("Tareas críticas", str(sum(1 for t in A["criticas"] if not t.hito)),
         pct(100.0 * sum(1 for t in A["criticas"] if not t.hito) / max(1, len(A["trabajo"]))) + " del total"),
        ("Pico de personal", num(round(A["pico_personal"], 1)), "personas el %s" % f_corta(A["dia_pico"])),
        ("Dotación media", num(round(A["prom_personal"], 1)), "personas/día · %s días-persona" % num(round(A["dias_persona"]))),
    ]
    k_html = '<div class="kpis">' + "".join('<div class="kpi"><div class="l">%s</div><div class="v">%s</div><div class="s">%s</div></div>'
                                             % (E(a), E(b), E(c)) for a, b, c in kpis) + "</div>"
    narr = ["La obra <b>%s</b> se programa para iniciar el <b>%s</b> y finalizar el <b>%s</b>, con un plazo de "
            "<b>%d días hábiles</b> (%d días corridos), sobre un calendario de %s."
            % (E(meta["obra"]), f_larga(A["f_ini"]), f_larga(A["f_fin"]), A["habiles"], A["corridos"],
               E(texto_semana(cal)))]
    narr.append("El cronograma se compone de <b>%d tareas</b> agrupadas en <b>%d rubros</b>, con <b>%d hitos</b> de control. "
                "El camino crítico involucra %d tareas; además, %d tareas tienen una holgura de 5 días hábiles o menos y "
                "deben seguirse como <i>casi críticas</i>."
                % (len(A["trabajo"]), len(A["rubros"]), len(A["hitos"]), sum(1 for t in A["criticas"] if not t.hito), len(A["casi"])))
    if A["costo"]:
        narr.append("El costo programado asciende a <b>%s</b>%s. El rubro de mayor incidencia es <b>%s</b> (%s del total)."
                    % (money(A["costo"], moneda), " (mano de obra y equipos)" if not A["costo_mat"] else "",
                       E(cod_rub[rub_max[0]].nombre), pct(100 * rub_max[1] / A["costo"])))
    if A["retrasadas"]:
        narr.append("La programación está <b>nivelada por recursos</b>: %d tareas se desplazaron respecto del CPM puro para "
                    "respetar la dotación disponible, por lo que el camino crítico incluye dependencias de recurso."
                    % len(A["retrasadas"]))
    narr.append("El pico de personal es de <b>%s personas</b> (%s), con una dotación media de %s personas por día hábil."
                % (num(round(A["pico_personal"], 1)), f_corta(A["dia_pico"]), num(round(A["prom_personal"], 1))))
    hitos_rows = [[E(t.codigo), E(t.nombre), f_corta(cal.dia(t.es)),
                   '<span class="tag crit">crítico</span>' if t.critica else '<span class="tag ok">con holgura</span>']
                  for t in A["hitos"]]
    doc.page(sec("Resumen ejecutivo") + k_html + "".join("<p>%s</p>" % p for p in narr)
             + "<h3>Hitos principales</h3>" + tabla(["Código", "Hito", "Fecha", "Estado"], hitos_rows, alin={2: "c", 3: "c"}),
             cls="flow")

    # ---------------- 2 bases
    fer = sorted((f, n) for f, n in cal.feriados.items() if A["f_ini"] <= f <= A["f_fin"])
    recesos = spec.get("calendario", {}).get("periodos_no_laborables", [])
    lim = [(tp["nombre"], tp["disponibles"]) for tp in tipos.values() if tp.get("disponibles")]
    crit_list = [
        "Duración de cada tarea = cómputo ÷ (rendimiento por cuadrilla × cantidad de cuadrillas), redondeado hacia arriba a días hábiles enteros.",
        "Método del camino crítico (CPM) con vínculos fin-comienzo (FS), comienzo-comienzo (SS), fin-fin (FF) y comienzo-fin (SF), con desfasajes en días hábiles.",
        "Holgura total (HT): margen sin atrasar la entrega. Holgura libre (HL): margen sin atrasar a ninguna sucesora.",
        "Los costos de mano de obra y equipos resultan de: cantidad de recursos × costo diario × duración." +
        ("" if A["costo_mat"] else " No se incluyen materiales."),
        "Las tareas permanentes (conducción, vigilancia, limpieza) abarcan todo el plazo y no condicionan el camino crítico.",
    ]
    if lim:
        crit_list.append("Nivelación de recursos por método serie con prioridad de menor inicio tardío, respetando la dotación máxima: "
                         + ", ".join("%s: %s" % (E(n), num(v)) for n, v in lim) + ".")
    crit_list += [E(x) for x in inf.get("supuestos", [])]
    cuerpo = sec("Bases de la programación")
    cuerpo += "<h3>Calendario laboral</h3>" + tabla(["Parámetro", "Valor"], [
        ["Días laborables", E(texto_semana(cal))],
        ["Feriados", E(("Preset %s + " % spec["calendario"]["feriados_preset"]) if spec.get("calendario", {}).get("feriados_preset") else "") + "%d días feriados dentro del plazo" % len(fer)],
        ["Recesos", "<br>".join("%s: %s al %s" % (E(r.get("nombre", "Receso")), f_corta(G.fecha(r["desde"])), f_corta(G.fecha(r["hasta"])))
                                for r in recesos) or "—"],
        ["Unidad de duración", "Día hábil (jornada completa)"],
        ["Modo de recursos", "Personas individuales (sin sobreasignación)" if op.get("modo_recursos", "individual") == "individual"
         else "Agrupados por especialidad (carga %)"],
        ["Línea base", "Guardada en el archivo .gan" if op.get("linea_base", True) else "No"],
    ])
    cuerpo += "<h3>Criterios de cálculo</h3><ul class='clean'>%s</ul>" % "".join("<li>%s</li>" % c for c in crit_list)
    cuerpo += "<h3>Recursos considerados</h3>" + tabla(
        ["Código", "Recurso", "Especialidad / rol", "Costo diario", "Disponibles"],
        [[E(tp.get("abrev", k)), E(tp["nombre"]), E(tp["rol"]), money(float(tp.get("costo_dia", 0)), moneda) if tp.get("costo_dia") else "—",
          num(tp["disponibles"]) if tp.get("disponibles") else "sin tope"] for k, tp in tipos.items()],
        alin={3: "r", 4: "c"})
    doc.page(cuerpo, cls="flow")

    # ---------------- 3 EDT
    filas, cls = [], []
    total = A["costo"] or 1
    for r in A["rubros"]:
        c = A["costo_rubro"][r.codigo]
        fb = cal.dia(r.ef - 1) if r.dur else cal.dia(r.es)
        n_t = sum(1 for h in G.iter_hojas(r) if not h.hito)
        filas.append(["<b>%s</b>" % E(r.codigo), "<b>%s</b>" % E(r.nombre), str(n_t), str(r.dur), f_corta(cal.dia(r.es)), f_corta(fb),
                      money(c, moneda) if c else "—", pct(100 * c / total) if A["costo"] else "—",
                      '<span class="tag crit">sí</span>' if any(h.critica and not h.abarca for h in G.iter_hojas(r)) else ""])
        cls.append("")
    filas.append(["", "<b>Total obra</b>", str(len(A["trabajo"])), str(A["habiles"]), f_corta(A["f_ini"]), f_corta(A["f_fin"]),
                  "<b>%s</b>" % money(A["costo"], moneda) if A["costo"] else "—", "100 %" if A["costo"] else "", ""])
    cls.append("grp")
    cuerpo = sec("Estructura de desglose del trabajo (EDT)")
    cuerpo += "<p class='ink2'>Rubros del proyecto con su duración (días hábiles), fechas extremas, incidencia económica y si contienen tareas críticas.</p>"
    cuerpo += tabla(["Cód.", "Rubro", "Tareas", "Dur.", "Inicio", "Fin", "Costo", "Incid.", "Crítico"], filas, cls,
                    alin={2: "c", 3: "c", 6: "r", 7: "r", 8: "c"})
    if A["costo"]:
        items = sorted(((cod_rub[c].codigo + " " + cod_rub[c].nombre, v) for c, v in A["costo_rubro"].items() if v),
                       key=lambda kv: -kv[1])
        cuerpo += "<h3>Incidencia económica por rubro</h3>" + barras_h_svg(items, moneda, "Costo por rubro")
    doc.page(cuerpo, cls="flow")

    # ---------------- 4 Gantt (apaisado)
    svgs = gantt_svgs(ctx, A)
    for i, s in enumerate(svgs):
        cab = sec("Diagrama de Gantt") if i == 0 else '<h3 style="margin-top:0">Diagrama de Gantt (continuación %d/%d)</h3>' % (i + 1, len(svgs))
        doc.page(cab + leyenda_gantt() + s, cls="land")

    # ---------------- 5 camino critico y holguras
    filas = []
    for t in A["criticas"]:
        fb = cal.dia(t.ef - 1) if t.dur else cal.dia(t.es)
        pr = ", ".join("%s%s%s" % (c, "" if tp == "FS" else " " + tp, ("%+d" % lg) if lg else "") for c, tp, lg in t.pred)
        if getattr(t, "pred_recurso", None):
            pr += (" · " if pr else "") + "recurso: " + ", ".join(c for c, _, _ in t.pred_recurso)
        filas.append([E(t.codigo), E(t.nombre) + (" ◆" if t.hito else ""), str(t.dur), f_corta(cal.dia(t.es)), f_corta(fb), E(pr)])
    cuerpo = sec("Camino crítico, hitos y holguras")
    cuerpo += ('<div class="box crit"><b>Camino crítico:</b> secuencia de %d actividades sin holgura que determina el plazo de %d días hábiles. '
               'Un día de atraso en cualquiera de ellas atrasa la entrega en un día, salvo que se tomen medidas de recuperación '
               '(más cuadrillas, horas extra o solapamientos).</div>' % (len(A["criticas"]), A["habiles"]))
    cuerpo += tabla(["Cód.", "Actividad", "Dur.", "Inicio", "Fin", "Predecesoras"], filas, alin={2: "c"})
    if A["casi"]:
        cuerpo += "<h3>Actividades casi críticas (holgura total ≤ 5 días)</h3>" + tabla(
            ["Cód.", "Actividad", "HT", "HL", "Inicio", "Fin"],
            [[E(t.codigo), E(t.nombre), str(t.holgura), str(t.holgura_libre), f_corta(cal.dia(t.es)), f_corta(cal.dia(t.ef - 1))]
             for t in A["casi"]], alin={2: "c", 3: "c"})
    rangos = [("Crítica (0)", lambda h: h <= 0), ("1 a 5 días", lambda h: 1 <= h <= 5), ("6 a 15 días", lambda h: 6 <= h <= 15),
              ("16 a 30 días", lambda h: 16 <= h <= 30), ("Más de 30 días", lambda h: h > 30)]
    cuerpo += "<h3>Distribución de holguras</h3>" + tabla(["Holgura total", "Tareas", "% del total"],
        [[a, str(sum(1 for t in A["trabajo"] if f(t.holgura))), pct(100.0 * sum(1 for t in A["trabajo"] if f(t.holgura)) / max(1, len(A["trabajo"])))]
         for a, f in rangos], alin={1: "c", 2: "r"})
    doc.page(cuerpo, cls="flow")

    # ---------------- 6 plan de trabajos
    filas, cls = [], []
    for t in ctx["todas"]:
        if t.contenedor:
            continue
        fb = cal.dia(t.ef - 1) if t.dur else cal.dia(t.es)
        if t.resumen:
            c = sum(sum(ctx["costos"][h.codigo]) for h in G.iter_hojas(t))
            filas.append([E(t.codigo), E(t.nombre), "", "", "", str(t.dur), f_corta(cal.dia(t.es)), f_corta(fb), "", "",
                          money(c, moneda) if c else ""])
            cls.append("grp")
            continue
        r = t.raw
        comp = "%s %s" % (num(r["cantidad"]), E(r.get("unidad", ""))) if "cantidad" in r else ""
        rend = ("%s ×%s" % (num(r["rendimiento"]), num(r.get("cuadrillas", 1)))) if "rendimiento" in r else ""
        cu = " + ".join("%s %s" % (num(q), E(tipos[k].get("abrev", k))) for k, q in t.recursos.items())
        c = sum(ctx["costos"][t.codigo])
        ht = "" if t.abarca else ('<span class="tag crit">0</span>' if t.critica else str(t.holgura))
        filas.append([E(t.codigo), E(t.nombre) + (" ◆" if t.hito else "") + (" <span class='tag info'>nivelada</span>" if t.retrasada else ""),
                      comp, rend, cu, str(t.dur), f_corta(cal.dia(t.es)), f_corta(fb), ht,
                      "" if t.abarca else str(t.holgura_libre), money(c, moneda) if c else ""])
        cls.append("crit" if t.critica and not t.abarca else "")
    doc.page(sec("Plan de trabajos detallado") +
             "<p class='ink2 small'>Cómputo, rendimiento diario por cuadrilla × número de cuadrillas, cuadrilla asignada, duración en días hábiles, "
             "fechas, holgura total (HT) y libre (HL) y costo de mano de obra y equipos.</p>" +
             tabla(["Cód.", "Tarea", "Cómputo", "Rend.", "Cuadrilla", "Dur.", "Inicio", "Fin", "HT", "HL", "Costo"], filas, cls,
                   alin={5: "c", 8: "c", 9: "c", 10: "r"}, cls_tabla="plan"), cls="flow")

    # ---------------- 7 recursos
    filas = []
    for k, tp in tipos.items():
        u = A["uso"][k]
        dp = sum(u.values())
        dias_act = sum(1 for v in u.values() if v > 0)
        filas.append([E(tp.get("abrev", k)), E(tp["nombre"]), E(tp["rol"]), num(round(max(u.values()) if u else 0, 2)),
                      num(round(dp, 1)), str(dias_act), money(dp * float(tp.get("costo_dia", 0)), moneda) if tp.get("costo_dia") else "—"])
    cuerpo = sec("Recursos")
    cuerpo += ("<p class='ink2'>El archivo .gan asigna personas concretas a cada tarea (%d recursos creados), de modo que el "
               "<b>Diagrama de Recursos</b> de GanttProject muestra la carga diaria de cada persona sin sobreasignaciones.</p>"
               % len(ctx["recursos"]))
    cuerpo += tabla(["Cód.", "Recurso", "Especialidad", "Pico diario", "Días-recurso", "Días activos", "Costo"], filas,
                    alin={3: "c", 4: "r", 5: "c", 6: "r"})
    cuerpo += "<h3>Histograma de personal por especialidad</h3><p class='ink2 small'>Promedio diario de personas en obra por semana (excluye equipos).</p>"
    cuerpo += histograma_svg(ctx, A)
    limitados = [k for k, tp in tipos.items() if tp.get("disponibles")]
    if limitados:
        cuerpo += "<h3>Recursos con dotación limitada</h3><p class='ink2 small'>Uso diario frente a la dotación disponible (línea punteada).</p>"
        cuerpo += '<div class="grid2">%s</div>' % "".join(recurso_limitado_svg(ctx, A, k) for k in limitados)
    doc.page(cuerpo, cls="flow")

    # ---------------- 8 costos
    cuerpo = sec("Costos e inversiones")
    if A["costo"]:
        acum, filas = 0.0, []
        for (y_, m), c in A["meses"].items():
            acum += c
            filas.append(["%s %d" % (MESES[m - 1].capitalize(), y_), money(c, moneda), pct(100 * c / A["costo"]),
                          money(acum, moneda), pct(100 * acum / A["costo"])])
        filas.append(["<b>Total</b>", "<b>%s</b>" % money(A["costo"], moneda), "100 %", "", ""])
        cuerpo += "<h3>Plan de inversiones mensual</h3>" + tabla(["Mes", "Inversión del mes", "% mes", "Acumulado", "% acumulado"], filas,
                                                                 [""] * (len(filas) - 1) + ["grp"], alin={1: "r", 2: "r", 3: "r", 4: "r"})
        cuerpo += "<h3>Inversión mensual prevista</h3>" + barras_mensuales_svg(A, moneda)
        cuerpo += "<h3>Curva S: avance económico acumulado</h3>" + curva_s_svg(A, moneda)
        cuerpo += ("<p class='ink2 small'>La curva S es la referencia para el control de avance: comparando el avance real "
                   "certificado con esta curva se detectan atrasos o adelantos económicos.</p>")
    else:
        cuerpo += "<p>No se cargaron costos diarios de recursos ni costos de materiales, por lo que no se calculan inversiones.</p>"
    doc.page(cuerpo, cls="flow")

    # ---------------- 9 observaciones
    obs = []
    for t in A["retrasadas"]:
        obs.append(("info", "Tarea %s «%s» desplazada por nivelación de recursos (%d días respecto del CPM sin límites)."
                    % (t.codigo, t.nombre, t.es - t.es_cpm)))
    if A["casi"]:
        obs.append(("warn", "%d actividades con holgura ≤ 5 días: un desvío menor las vuelve críticas (%s)."
                    % (len(A["casi"]), ", ".join(t.codigo for t in A["casi"][:12]) + ("…" if len(A["casi"]) > 12 else ""))))
    p_crit = 100.0 * sum(1 for t in A["criticas"] if not t.hito) / max(1, len(A["trabajo"]))
    if p_crit > 40:
        obs.append(("warn", "El %s de las tareas es crítico: el cronograma es poco flexible." % pct(p_crit)))
    for a in ctx["avisos"]:
        obs.append(("warn", a))
    lluvia = [t for t in A["trabajo"] if any(w in t.nombre.lower() for w in ("excav", "hormig", "techo", "cubierta", "exterior", "losa"))]
    if lluvia:
        obs.append(("info", "Actividades sensibles a la lluvia (%d): excavaciones, hormigonados, cubierta y trabajos exteriores. "
                    "Se recomienda prever días de contingencia climática." % len(lluvia)))
    for x in inf.get("observaciones", []):
        obs.append(("info", x))
    tag = {"warn": '<span class="tag warn">atención</span>', "info": '<span class="tag info">nota</span>'}
    cuerpo = sec("Observaciones y riesgos")
    cuerpo += tabla(["#", "Tipo", "Observación"], [[str(i + 1), tag[k], E(v)] for i, (k, v) in enumerate(obs)] or [["", "", "Sin observaciones."]],
                    alin={0: "c", 1: "c"})
    cuerpo += ("<h3>Recomendaciones de control</h3><ul class='clean'>"
               "<li>Actualizar el avance (%) en GanttProject al menos una vez por semana y compararlo con la línea base guardada.</li>"
               "<li>Hacer un seguimiento diario de las actividades críticas y casi críticas.</li>"
               "<li>Comparar la certificación mensual con el plan de inversiones y la curva S.</li>"
               "<li>Confirmar con anticipación la disponibilidad de las cuadrillas en los picos del histograma.</li>"
               "<li>Registrar en el libro de obra los días perdidos por lluvia y reprogramar las tareas afectadas.</li></ul>")
    doc.page(cuerpo, cls="flow")

    # ---------------- 10 anexos
    cuerpo = sec("Anexos")
    cuerpo += "<h3>A. Feriados y días no laborables dentro del plazo</h3>"
    cuerpo += tabla(["Fecha", "Día", "Motivo"], [[f_corta(f), DIAS_N[f.weekday()], E(n)] for f, n in fer] or [["—", "", ""]])
    cuerpo += "<h3>B. Glosario</h3>" + tabla(["Término", "Significado"], [
        ["FS (fin-comienzo)", "La sucesora comienza cuando termina la predecesora (± desfasaje)."],
        ["SS (comienzo-comienzo)", "La sucesora comienza cuando comienza la predecesora (± desfasaje)."],
        ["FF (fin-fin)", "La sucesora termina cuando termina la predecesora (± desfasaje)."],
        ["SF (comienzo-fin)", "La sucesora termina cuando comienza la predecesora (± desfasaje)."],
        ["HT: holgura total", "Días que una tarea puede atrasarse sin atrasar la entrega."],
        ["HL: holgura libre", "Días que una tarea puede atrasarse sin atrasar a ninguna sucesora."],
        ["Camino crítico", "Cadena de tareas con holgura total nula que fija el plazo de obra."],
        ["Nivelación", "Desplazamiento de tareas para no superar la dotación disponible de recursos."],
        ["Línea base", "Foto del plan aprobado, para comparar con el avance real."],
        ["Curva S", "Inversión acumulada prevista en el tiempo."],
    ])
    cuerpo += "<h3>C. Aprobación</h3><div class='sign'>%s</div>" % "".join(
        "<div><div class='sp'></div><b>%s</b><span>%s</span><span class='fd'>Firma y aclaración · Fecha: ____/____/______</span></div>"
        % (lab, E(inf.get(k, "")) or "&nbsp;")
        for k, lab in (("elaboro", "Elaboró"), ("reviso", "Revisó"), ("aprobo", "Aprobó")))
    doc.page(cuerpo, cls="flow")
    return doc.render("Informe de programación · %s" % meta["obra"])


def texto_semana(cal):
    d = sorted(cal.laborables)
    nombres = [DIAS_N[i] for i in d]
    if d == list(range(d[0], d[-1] + 1)) and len(d) > 2:
        return "%s a %s (%d días por semana)" % (nombres[0], nombres[-1], len(d))
    return ", ".join(nombres)


def buscar_navegador():
    candidatos = [shutil.which(n) for n in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")]
    candidatos += [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return next((c for c in candidatos if c and os.path.exists(c)), None)


def a_pdf(html_path, pdf_path):
    nav = buscar_navegador()
    if not nav:
        return False, "no se encontró Edge/Chrome; abra el HTML y use Imprimir → Guardar como PDF"
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    cmd = [nav, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
           "--print-to-pdf=%s" % os.path.abspath(pdf_path), url]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except Exception as e:  # pragma: no cover - depende del sistema
        return False, str(e)
    return os.path.exists(pdf_path), nav


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Genera el informe profesional (HTML/PDF) de un cronograma")
    ap.add_argument("spec", help="especificación JSON (la misma que usa generar_gan.py)")
    ap.add_argument("salida", help="archivo .html del informe")
    ap.add_argument("--pdf", help="además, exporta a PDF con Edge/Chrome headless")
    args = ap.parse_args(argv)
    try:
        with open(args.spec, encoding="utf-8") as f:
            spec = json.load(f)
        ctx = G.generar(spec)
    except G.SpecError as e:
        sys.stderr.write("ERROR: %s\n" % e)
        return 1
    html_doc = construir(ctx, spec, os.path.dirname(os.path.abspath(args.spec)))
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print("OK -> %s" % args.salida)
    if args.pdf:
        ok, info = a_pdf(args.salida, args.pdf)
        print(("OK -> %s" % args.pdf) if ok else ("AVISO: no se pudo generar el PDF (%s)" % info))
    return 0


if __name__ == "__main__":
    sys.exit(main())
