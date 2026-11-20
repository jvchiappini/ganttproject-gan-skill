#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
validar_gan.py - Auditoria de completitud y consistencia de un archivo .gan de GanttProject.

Revisa un cronograma (hecho a mano o generado) y lista lo que le falta o esta mal:

  ERRORES   (el archivo esta roto o es inconsistente)
    - XML invalido, ids duplicados, dependencias a tareas inexistentes
    - asignaciones a tareas/recursos inexistentes, roles sin definir
    - tareas que empiezan en dia no laborable
  ADVERTENCIAS (el cronograma esta incompleto)
    - tareas sin predecesoras / sin sucesoras (cabos sueltos)
    - dependencias que apuntan a tareas resumen
    - tareas sin recursos, proyecto sin recursos (sin Diagrama de Recursos)
    - recursos sobreasignados (> 100 % el mismo dia)
    - feriados faltantes en algun anio del plazo (preset PY/AR)
    - falta de hitos (inicio / fin), tareas de duracion 0 que no son hitos
    - sin linea base, sin costos, sin notas, sin descripcion
  INFO (estadisticas)

Uso:
    python validar_gan.py archivo.gan [--feriados PY|AR|ninguno] [--json]
Codigo de salida: 0 sin errores, 1 con errores.
"""
import argparse
import datetime as dt
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generar_gan as G  # noqa: E402

GAN_DIAS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Auditoria:
    def __init__(self):
        self.items = []

    def add(self, nivel, codigo, msg):
        self.items.append({"nivel": nivel, "codigo": codigo, "mensaje": msg})

    def err(self, c, m):
        self.add("ERROR", c, m)

    def warn(self, c, m):
        self.add("ADVERTENCIA", c, m)

    def info(self, c, m):
        self.add("INFO", c, m)

    @property
    def errores(self):
        return [i for i in self.items if i["nivel"] == "ERROR"]


def cargar(path):
    with open(path, "rb") as f:
        return ET.fromstring(f.read())


def calendario_gan(root):
    no_lab = set()
    week = root.find("./calendars/day-types/default-week")
    if week is not None:
        for i, d in enumerate(GAN_DIAS):
            if week.get(d) == "1":
                no_lab.add(i)
    else:
        no_lab = {5, 6}
    feriados = {}
    for d in root.findall("./calendars/date"):
        try:
            if d.get("year"):
                f = dt.date(int(d.get("year")), int(d.get("month")), int(d.get("date")))
                feriados[f] = (d.text or "").strip() or "Feriado"
        except ValueError:
            pass
    return no_lab, feriados


def dias_habiles(inicio, dur, no_lab, feriados):
    """Lista de fechas habiles que ocupa una tarea (como GanttProject)."""
    out, d = [], inicio
    guard = 0
    while len(out) < dur and guard < 5000:
        if d.weekday() not in no_lab and d not in feriados:
            out.append(d)
        d += dt.timedelta(days=1)
        guard += 1
    return out


def auditar(path, preset="PY"):
    A = Auditoria()
    try:
        root = cargar(path)
    except ET.ParseError as e:
        A.err("XML", "El archivo no es XML válido: %s" % e)
        return A, {}
    if root.tag != "project":
        A.err("XML", "La raíz no es <project>: no parece un archivo de GanttProject.")
        return A, {}

    no_lab, feriados = calendario_gan(root)
    tareas, padre, hijos = {}, {}, defaultdict(list)

    def rec(el, p):
        for t in el.findall("task"):
            tid = t.get("id")
            if tid in tareas:
                A.err("ID", "Id de tarea duplicado: %s" % tid)
            tareas[tid] = t
            padre[tid] = p
            if p is not None:
                hijos[p].append(tid)
            rec(t, tid)
    tasks_el = root.find("tasks")
    if tasks_el is None:
        A.err("TAREAS", "El archivo no tiene <tasks>.")
        return A, {}
    rec(tasks_el, None)
    if not tareas:
        A.err("TAREAS", "El cronograma no tiene tareas.")
        return A, {}

    nombre = lambda tid: "%s «%s»" % (tid, tareas[tid].get("name", ""))
    hojas = [t for t in tareas if not hijos[t]]
    preds, succs = defaultdict(list), defaultdict(list)
    for tid, t in tareas.items():
        for d in t.findall("depend"):
            sid = d.get("id")
            if sid not in tareas:
                A.err("DEP", "La tarea %s tiene una dependencia hacia la tarea inexistente %s." % (nombre(tid), sid))
                continue
            succs[tid].append(sid)
            preds[sid].append(tid)
            if hijos[sid] or hijos[tid]:
                A.warn("DEP-RESUMEN", "Dependencia %s → %s involucra una tarea resumen: vincular tareas hoja es más robusto."
                       % (tid, sid))
            if d.get("type") not in ("1", "2", "3", "4"):
                A.err("DEP", "Tipo de dependencia inválido (%s) en %s → %s." % (d.get("type"), tid, sid))

    fechas = {}
    for tid in tareas:
        t = tareas[tid]
        try:
            ini = G.fecha(t.get("start"))
            dur = int(t.get("duration", "0"))
        except Exception:
            A.err("FECHA", "Fecha o duración inválida en %s." % nombre(tid))
            continue
        fechas[tid] = (ini, dur)
        if tid in hojas and dur > 0 and (ini.weekday() in no_lab or ini in feriados):
            A.err("FECHA", "%s empieza el %s, que no es día laborable." % (nombre(tid), ini.strftime("%d/%m/%Y")))
        if tid in hojas and dur == 0 and t.get("meeting") != "true":
            A.warn("DUR0", "%s tiene duración 0 pero no está marcada como hito." % nombre(tid))

    # cabos sueltos
    con_fecha = [t for t in hojas if t in fechas]
    if con_fecha:
        primera = min(fechas[t][0] for t in con_fecha)
        fin_proj = max(dias_habiles(fechas[t][0], max(1, fechas[t][1]), no_lab, feriados)[-1] for t in con_fecha)

        def pred_efectivas(t):
            p, x = list(preds[t]), padre.get(t)
            while x is not None:
                p += preds[x]
                x = padre.get(x)
            return p

        def succ_efectivas(t):
            s, x = list(succs[t]), padre.get(t)
            while x is not None:
                s += succs[x]
                x = padre.get(x)
            return s
        sin_pred = [t for t in con_fecha if not pred_efectivas(t) and fechas[t][0] > primera]
        span = max(1, (fin_proj - primera).days)

        def fin_de(t):
            return dias_habiles(fechas[t][0], max(1, fechas[t][1]), no_lab, feriados)[-1]

        def permanente(t):  # abarca (casi) toda la obra: direccion, sereno, limpieza...
            return fechas[t][0] == primera and (fin_de(t) - primera).days >= 0.9 * span

        sin_succ = [t for t in con_fecha if not succ_efectivas(t) and fin_de(t) < fin_proj and not permanente(t)]
        if sin_pred:
            A.warn("SIN-PRED", "Tareas sin predecesoras que no empiezan con la obra (%d): %s"
                   % (len(sin_pred), ", ".join(nombre(t) for t in sin_pred[:15]) + ("…" if len(sin_pred) > 15 else "")))
        if sin_succ:
            A.warn("SIN-SUCC", "Tareas sin sucesoras que terminan antes del fin (cabos sueltos, %d): %s"
                   % (len(sin_succ), ", ".join(nombre(t) for t in sin_succ[:15]) + ("…" if len(sin_succ) > 15 else "")))
        # feriados faltantes
        if preset and preset.upper() in G.PRESETS:
            faltan = []
            for y in range(primera.year, fin_proj.year + 1):
                for f, n in G.PRESETS[preset.upper()](y):
                    if primera <= f <= fin_proj and f.weekday() not in no_lab and f not in feriados:
                        faltan.append("%s (%s)" % (f.strftime("%d/%m/%Y"), n))
            if faltan:
                A.warn("FERIADOS", "Feriados %s del plazo que no están cargados (%d): %s. En GanttProject cada feriado vale "
                       "solo para su año." % (preset.upper(), len(faltan), "; ".join(faltan)))
        A.info("PLAZO", "Plazo: %s → %s (%d días corridos)." % (primera.strftime("%d/%m/%Y"), fin_proj.strftime("%d/%m/%Y"),
                                                                 (fin_proj - primera).days + 1))

    # hitos
    hitos = [t for t in hojas if tareas[t].get("meeting") == "true" or fechas.get(t, (0, 1))[1] == 0]
    if not hitos:
        A.warn("HITOS", "No hay hitos (inicio de obra, fin de estructura, obra bajo techo, entrega...).")
    elif len(hitos) < 3:
        A.warn("HITOS", "Solo hay %d hito(s); un plan de obra suele tener hitos de control intermedios." % len(hitos))

    # recursos
    roles_def = {r.get("id") for r in root.findall("./roles/role")}
    recursos = {r.get("id"): r for r in root.findall("./resources/resource")}
    alloc = root.findall("./allocations/allocation")
    if not recursos:
        A.warn("RECURSOS", "El proyecto no tiene recursos: el Diagrama de Recursos queda vacío.")
    for rid, r in recursos.items():
        fn = r.get("function", "")
        if fn and ":" not in fn and fn not in roles_def:
            A.err("ROL", "El recurso «%s» usa la función %s que no está definida en <roles>." % (r.get("name"), fn))
        if r.find("rate") is None:
            A.add("INFO", "COSTO", "El recurso «%s» no tiene costo diario (tarifa estándar)." % r.get("name"))
    asignadas = set()
    carga = defaultdict(lambda: defaultdict(float))
    for a in alloc:
        tid, rid = a.get("task-id"), a.get("resource-id")
        if tid not in tareas:
            A.err("ASIG", "Asignación a una tarea inexistente (%s)." % tid)
            continue
        if rid not in recursos:
            A.err("ASIG", "Asignación de un recurso inexistente (%s) en %s." % (rid, nombre(tid)))
            continue
        asignadas.add(tid)
        if tid in fechas and tid in hojas:
            ini, dur = fechas[tid]
            for d in dias_habiles(ini, dur, no_lab, feriados):
                carga[rid][d] += float(a.get("load", "100"))
    if recursos:
        sin_rec = [t for t in hojas if t not in asignadas and fechas.get(t, (0, 0))[1] > 0]
        if sin_rec:
            A.warn("SIN-RECURSOS", "Tareas sin recursos asignados (%d): %s" % (
                len(sin_rec), ", ".join(nombre(t) for t in sin_rec[:15]) + ("…" if len(sin_rec) > 15 else "")))
        for rid, dias in carga.items():
            sobre = sorted(d for d, v in dias.items() if v > 100.0 + 1e-6)
            if sobre:
                A.warn("SOBRECARGA", "«%s» está sobreasignado %d día(s), p. ej. %s (%.0f %%)."
                       % (recursos[rid].get("name"), len(sobre), sobre[0].strftime("%d/%m/%Y"), dias[sobre[0]]))
        sin_uso = [recursos[r].get("name") for r in recursos if r not in carga]
        if sin_uso:
            A.warn("RECURSO-OCIOSO", "Recursos sin ninguna asignación: %s" % ", ".join(sin_uso))

    # otros elementos de un plan completo
    if root.find("./previous/previous-tasks") is None:
        A.warn("LINEA-BASE", "No hay línea base guardada: no se podrá comparar el avance real con el plan.")
    if not any(t.get("cost-manual-value") for t in tareas.values()) and not any(r.find("rate") is not None for r in recursos.values()):
        A.warn("COSTOS", "No hay costos (ni tarifas de recursos ni costos por tarea): no se puede armar la curva S.")
    if not any(t.find("notes") is not None for t in tareas.values()):
        A.warn("NOTAS", "Ninguna tarea tiene notas (cómputo, rendimiento, criterio).")
    desc = root.find("description")
    if desc is None or not (desc.text or "").strip():
        A.warn("DESCRIPCION", "El proyecto no tiene descripción.")
    if len(hojas) < 25:
        A.warn("DETALLE", "Solo %d tareas: para una obra suele hacer falta más detalle (ver la plantilla completa)." % len(hojas))
    nombres = defaultdict(list)
    for tid in hojas:
        nombres[tareas[tid].get("name", "").strip().lower()].append(tid)
    for n, ids in nombres.items():
        if len(ids) > 1 and n:
            A.warn("NOMBRE", "Nombre repetido en %d tareas: «%s»." % (len(ids), n))
    A.info("RESUMEN", "%d tareas (%d hoja, %d resumen, %d hitos), %d recursos, %d asignaciones, %d feriados."
           % (len(tareas), len(hojas), len(tareas) - len(hojas), len(hitos), len(recursos), len(alloc), len(feriados)))
    return A, {"tareas": len(tareas), "hojas": len(hojas), "recursos": len(recursos)}


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Audita un archivo .gan de GanttProject")
    ap.add_argument("gan")
    ap.add_argument("--feriados", default="PY", help="preset de feriados para verificar (PY, AR o ninguno)")
    ap.add_argument("--json", action="store_true", help="salida en JSON")
    args = ap.parse_args(argv)
    preset = None if args.feriados.lower() in ("ninguno", "none", "") else args.feriados
    A, _ = auditar(args.gan, preset)
    if args.json:
        print(json.dumps(A.items, ensure_ascii=False, indent=2))
    else:
        iconos = {"ERROR": "✖", "ADVERTENCIA": "▲", "INFO": "·"}
        print("Auditoría de %s\n" % args.gan)
        for nivel in ("ERROR", "ADVERTENCIA", "INFO"):
            for i in A.items:
                if i["nivel"] == nivel:
                    print("%s %-11s [%s] %s" % (iconos[nivel], nivel, i["codigo"], i["mensaje"]))
        n_e = len(A.errores)
        n_w = sum(1 for i in A.items if i["nivel"] == "ADVERTENCIA")
        print("\nResultado: %d error(es), %d advertencia(s)." % (n_e, n_w))
    return 1 if A.errores else 0


if __name__ == "__main__":
    sys.exit(main())
