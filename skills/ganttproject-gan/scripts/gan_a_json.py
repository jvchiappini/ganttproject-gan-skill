#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
gan_a_json.py - Convierte un .gan existente (hecho a mano en GanttProject) en la
especificacion JSON de generar_gan.py, para completarlo y regenerarlo.

Conserva: nombre, descripcion, fecha de inicio, semana laboral, feriados, EDT
(tareas resumen e hijas), duraciones, hitos, avance, notas, colores,
dependencias (tipo y desfasaje), recursos (tipo = nombre del recurso, rol, tarifa)
y asignaciones (cantidad = carga / 100).

Las dependencias hacia o desde tareas resumen se trasladan a sus tareas hoja
(ultima hoja del grupo predecesor -> primera hoja del grupo sucesor).

Uso:
    python gan_a_json.py entrada.gan salida.json
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

GAN_DIAS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ES_DIAS = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
TIPOS = {"1": "SS", "2": "FS", "3": "FF", "4": "SF"}
COD_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(.*)$")


def convertir(path):
    root = ET.parse(path).getroot()
    week = root.find("./calendars/day-types/default-week")
    laborables = [ES_DIAS[i] for i, d in enumerate(GAN_DIAS) if week is None or week.get(d) != "1"]
    feriados = []
    for d in root.findall("./calendars/date"):
        if d.get("year"):
            feriados.append({"fecha": "%04d-%02d-%02d" % (int(d.get("year")), int(d.get("month")), int(d.get("date"))),
                             "nombre": (d.text or "Feriado").strip()})

    tasks_el = root.find("tasks")
    todas, hijos, padre = {}, defaultdict(list), {}

    def rec(el, p):
        for t in el.findall("task"):
            todas[t.get("id")] = t
            padre[t.get("id")] = p
            if p:
                hijos[p].append(t.get("id"))
            rec(t, t.get("id"))
    rec(tasks_el, None)

    # codigos EDT: usar el numero del nombre si existe y es unico; si no, el id
    codigos, usados = {}, set()
    for tid, t in todas.items():
        m = COD_RE.match(t.get("name", ""))
        cod = m.group(1) if m else None
        if not cod or cod in usados:
            cod = "T" + tid
        usados.add(cod)
        codigos[tid] = cod

    def primera_hoja(tid):
        while hijos[tid]:
            tid = hijos[tid][0]
        return tid

    def hojas_de(tid):
        if not hijos[tid]:
            return [tid]
        out = []
        for h in hijos[tid]:
            out += hojas_de(h)
        return out

    def ultima_hoja(tid):
        hs = hojas_de(tid)
        return max(hs, key=lambda h: (todas[h].get("start", ""), int(todas[h].get("duration", "0"))))

    preds = defaultdict(list)
    for tid, t in todas.items():
        for d in t.findall("depend"):
            sid = d.get("id")
            if sid not in todas:
                continue
            tipo = TIPOS.get(d.get("type"), "FS")
            lag = int(float(d.get("difference", "0") or 0))
            origen = ultima_hoja(tid) if hijos[tid] else tid
            destinos = [primera_hoja(sid)] if hijos[sid] else [sid]
            if hijos[sid] and tipo in ("FS", "SS"):
                destinos = [h for h in hojas_de(sid) if not any(
                    dd.get("id") for x in hojas_de(sid) for dd in todas[x].findall("depend") if dd.get("id") == h)]
                destinos = destinos or [primera_hoja(sid)]
            for dst in destinos:
                txt = codigos[origen] + ("" if tipo == "FS" else tipo) + (("%+d" % lag) if lag else "")
                if txt not in preds[dst]:
                    preds[dst].append(txt)

    recursos = []
    roles = {r.get("id"): r.get("name") for r in root.findall("./roles/role")}
    res_id = {}
    for r in root.findall("./resources/resource"):
        rid = r.get("id")
        clave = "R%s" % rid
        res_id[rid] = clave
        item = {"id": clave, "abrev": clave, "nombre": r.get("name", clave),
                "rol": roles.get(r.get("function", ""), r.get("function", "General"))}
        rate = r.find("rate")
        if rate is not None:
            item["costo_dia"] = float(rate.get("value", "0"))
        recursos.append(item)
    asign = defaultdict(dict)
    for a in root.findall("./allocations/allocation"):
        if a.get("resource-id") in res_id:
            asign[a.get("task-id")][res_id[a.get("resource-id")]] = round(float(a.get("load", "100")) / 100.0, 2)

    inicio = min((t.get("start") for t in todas.values() if t.get("start")), default=None)

    def nodo(tid):
        t = todas[tid]
        m = COD_RE.match(t.get("name", ""))
        n = {"id": codigos[tid], "nombre": m.group(2).strip() if m else t.get("name", "")}
        if hijos[tid]:
            n["hijos"] = [nodo(h) for h in hijos[tid]]
            return n
        dur = int(t.get("duration", "0"))
        if t.get("meeting") == "true" or dur == 0:
            n["hito"] = True
        else:
            n["duracion"] = dur
        if preds[tid]:
            n["predecesoras"] = preds[tid]
        if asign.get(tid):
            n["recursos"] = asign[tid]
        if int(t.get("complete", "0") or 0):
            n["avance"] = int(t.get("complete"))
        if t.get("color"):
            n["color"] = t.get("color")
        notas = t.find("notes")
        if notas is not None and (notas.text or "").strip():
            n["notas"] = notas.text.strip()
        if not preds[tid] and t.get("start") and t.get("start") != inicio:
            n["inicio_no_antes"] = t.get("start")
        return n

    raices = [tid for tid in todas if padre[tid] is None]
    tareas = [nodo(t) for t in raices]
    nombre = root.get("name", "Proyecto")
    # si hay una unica raiz con el nombre del proyecto, usarla como contenedor
    if len(tareas) == 1 and tareas[0].get("hijos"):
        nombre = tareas[0]["nombre"] or nombre
        tareas = tareas[0]["hijos"]
    desc = root.find("description")
    spec = {
        "proyecto": {"nombre": nombre, "empresa": root.get("company", ""), "inicio": inicio,
                     "locale": root.get("locale", "es_AR"),
                     "descripcion": (desc.text or "").strip() if desc is not None and desc.text else ""},
        "calendario": {"dias_laborables": laborables, "feriados_preset": None, "feriados": feriados},
        "recursos": recursos,
        "opciones": {"modo_recursos": "agrupado" if recursos else "individual", "version": root.get("version", "2.7.1")},
        "tareas": tareas,
    }
    return spec


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Convierte un .gan en especificación JSON editable")
    ap.add_argument("gan")
    ap.add_argument("salida")
    args = ap.parse_args(argv)
    spec = convertir(args.gan)
    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    n = sum(1 for _ in _iter(spec["tareas"]))
    print("OK -> %s (%d tareas, %d recursos)" % (args.salida, n, len(spec["recursos"])))
    print("Revisá: feriados_preset (se dejó en null y se copiaron los feriados del archivo), recursos y cómputos.")
    return 0


def _iter(lista):
    for t in lista:
        yield t
        for x in _iter(t.get("hijos", [])):
            yield x


if __name__ == "__main__":
    sys.exit(main())
