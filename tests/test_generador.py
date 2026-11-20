# SPDX-License-Identifier: MIT
"""Pruebas del motor de programacion, del XML .gan y de las utilidades."""
import copy
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(RAIZ, "skills", "ganttproject-gan")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import gan_a_json  # noqa: E402
import generar_gan as G  # noqa: E402
import informe  # noqa: E402
import validar_gan  # noqa: E402

PLANTILLA = os.path.join(SKILL, "assets", "plantillas", "vivienda_unifamiliar_completa.json")
BASICO = os.path.join(SKILL, "assets", "ejemplos", "ejemplo_basico.json")


def cargar(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def spec_min(**kw):
    s = {"proyecto": {"nombre": "Test", "inicio": "2026-03-02"},
         "calendario": {"dias_laborables": ["lun", "mar", "mie", "jue", "vie"], "feriados_preset": None},
         "opciones": {"linea_base": False},
         "tareas": []}
    s.update(kw)
    return s


def por_codigo(ctx):
    return {t.codigo: t for t in ctx["todas"]}


class TestCalendario(unittest.TestCase):
    def test_pascua(self):
        self.assertEqual(G.pascua(2026), dt.date(2026, 4, 5))
        self.assertEqual(G.pascua(2027), dt.date(2027, 3, 28))
        self.assertEqual(G.pascua(2030), dt.date(2030, 4, 21))

    def test_feriados_paraguay(self):
        cal = G.Calendario({"feriados_preset": "PY"}, dt.date(2026, 1, 5))
        self.assertIn(dt.date(2026, 9, 29), cal.feriados)
        self.assertIn(dt.date(2027, 3, 25), cal.feriados)  # Jueves Santo 2027
        self.assertFalse(cal.es_habil(dt.date(2026, 12, 8)))

    def test_receso(self):
        cal = G.Calendario({"periodos_no_laborables": [{"desde": "2026-12-24", "hasta": "2026-12-31"}]},
                           dt.date(2026, 12, 1))
        self.assertFalse(cal.es_habil(dt.date(2026, 12, 28)))
        self.assertTrue(cal.es_habil(dt.date(2027, 1, 4)))


class TestProgramacion(unittest.TestCase):
    def test_reproduce_gan_real(self):
        """El ejemplo basico reproduce las fechas de un .gan hecho en GanttProject."""
        ctx = G.generar(cargar(BASICO))
        t = por_codigo(ctx)
        cal = ctx["cal"]
        esperado = {"1.1": "2026-09-18", "2.1": "2026-09-25", "2.2": "2026-10-03", "3.1": "2026-10-24",
                    "3.4": "2026-11-28", "4.2": "2026-11-28", "5.1": "2026-12-14", "5.2": "2026-12-29"}
        for cod, f in esperado.items():
            self.assertEqual(cal.dia(t[cod].es).isoformat(), f, cod)

    def test_tipos_de_dependencia(self):
        s = spec_min(tareas=[
            {"id": "A", "nombre": "A", "duracion": 5},
            {"id": "B", "nombre": "B", "duracion": 3, "predecesoras": ["A"]},
            {"id": "C", "nombre": "C", "duracion": 2, "predecesoras": ["ASS+1"]},
            {"id": "D", "nombre": "D", "duracion": 2, "predecesoras": ["AFF"]},
            {"id": "E", "nombre": "E", "duracion": 2, "predecesoras": [{"id": "A", "tipo": "CF", "desfase": 2}]},
            {"id": "F", "nombre": "F", "hito": True, "predecesoras": ["B", "C", "D", "E"]},
        ])
        t = por_codigo(G.generar(s))
        self.assertEqual(t["B"].es - t["A"].es, 5)
        self.assertEqual(t["C"].es - t["A"].es, 1)
        self.assertEqual(t["D"].ef, t["A"].ef)
        self.assertEqual(t["E"].es - t["A"].ef, 2)
        self.assertTrue(t["F"].hito)

    def test_computo_y_rendimiento(self):
        s = spec_min(tareas=[{"id": "A", "nombre": "Muro", "cantidad": 100, "rendimiento": 9, "cuadrillas": 2}])
        self.assertEqual(por_codigo(G.generar(s))["A"].dur, 6)  # ceil(100/18)

    def test_camino_critico_y_holgura(self):
        s = spec_min(tareas=[
            {"id": "A", "nombre": "A", "duracion": 5},
            {"id": "B", "nombre": "B", "duracion": 2},
            {"id": "C", "nombre": "C", "duracion": 1, "predecesoras": ["A", "B"]},
        ])
        t = por_codigo(G.generar(s))
        self.assertTrue(t["A"].critica and t["C"].critica)
        self.assertFalse(t["B"].critica)
        self.assertEqual(t["B"].holgura, 3)

    def test_errores(self):
        casos = [
            spec_min(tareas=[{"id": "A", "nombre": "A", "duracion": 1, "predecesoras": ["B"]},
                             {"id": "B", "nombre": "B", "duracion": 1, "predecesoras": ["A"]}]),  # ciclo
            spec_min(tareas=[{"id": "A", "nombre": "A", "duracion": 1, "predecesoras": ["Z"]}]),  # inexistente
            spec_min(tareas=[{"id": "A", "nombre": "A"}]),  # sin duracion
            spec_min(tareas=[{"id": "A", "nombre": "A", "duracion": 1, "recursos": {"X": 1}}]),  # recurso
        ]
        for c in casos:
            with self.assertRaises(G.SpecError):
                G.generar(c)

    def test_nivelacion_respeta_topes(self):
        s = spec_min(recursos=[{"id": "OF", "nombre": "Oficial", "disponibles": 2}], tareas=[
            {"id": "A", "nombre": "A", "duracion": 3, "recursos": {"OF": 2}},
            {"id": "B", "nombre": "B", "duracion": 3, "recursos": {"OF": 2}},
            {"id": "C", "nombre": "C", "duracion": 3, "recursos": {"OF": 1}},
        ])
        ctx = G.generar(s)
        uso = defaultdict(float)
        for t in ctx["hojas"]:
            for d in range(t.es, t.ef):
                uso[d] += t.recursos.get("OF", 0)
        self.assertLessEqual(max(uso.values()), 2)
        self.assertTrue(any(t.retrasada for t in ctx["hojas"]))
        self.assertIn('thirdDate-constraint="1"', ctx["xml"])


class TestXML(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = G.generar(cargar(PLANTILLA))
        cls.root = ET.fromstring(cls.ctx["xml"].encode("utf-8"))

    def test_estructura(self):
        r = self.root
        self.assertEqual(r.tag, "project")
        for tag in ("calendars", "tasks", "resources", "allocations", "taskdisplaycolumns", "previous", "roles"):
            self.assertIsNotNone(r.find(tag), tag)
        self.assertIsNotNone(r.find("./previous/previous-tasks"))

    def test_referencias_validas(self):
        r = self.root
        tareas = {t.get("id") for t in r.iter("task")}
        recursos = {x.get("id") for x in r.findall("./resources/resource")}
        roles = {x.get("id") for x in r.findall("./roles/role")}
        for d in r.iter("depend"):
            self.assertIn(d.get("id"), tareas)
        for a in r.findall("./allocations/allocation"):
            self.assertIn(a.get("task-id"), tareas)
            self.assertIn(a.get("resource-id"), recursos)
            self.assertIn(a.get("function"), roles)
        for x in r.findall("./resources/resource"):
            self.assertIn(x.get("function"), roles)

    def test_sin_sobreasignacion_individual(self):
        carga = defaultdict(lambda: defaultdict(float))
        for t, rid, c, _ in self.ctx["asign"]:
            for d in range(t.es, t.ef):
                carga[rid][d] += c
        self.assertLessEqual(max(v for dias in carga.values() for v in dias.values()), 100.0 + 1e-6)

    def test_columnas_personalizadas(self):
        props = {p.get("id") for p in self.root.iter("taskproperty") if p.get("type") == "custom"}
        self.assertTrue({"tpc0", "tpc2", "tpc5", "tpc6"} <= props)
        self.assertTrue(any(True for _ in self.root.iter("customproperty")))

    def test_auditoria_plantilla_limpia(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.gan")
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.ctx["xml"])
            A, _ = validar_gan.auditar(p, "PY")
            self.assertEqual(A.errores, [])
            self.assertEqual([i for i in A.items if i["nivel"] == "ADVERTENCIA"], [])


class TestUtilidades(unittest.TestCase):
    def test_ida_y_vuelta(self):
        ctx = G.generar(cargar(BASICO))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.gan")
            with open(p, "w", encoding="utf-8") as f:
                f.write(ctx["xml"])
            spec2 = gan_a_json.convertir(p)
            ctx2 = G.generar(spec2)
        f1 = {t.nombre: (ctx["cal"].dia(t.es), t.dur) for t in ctx["hojas"]}
        f2 = {t.nombre: (ctx2["cal"].dia(t.es), t.dur) for t in ctx2["hojas"]}
        self.assertEqual(f1, f2)

    def test_informe(self):
        spec = cargar(PLANTILLA)
        html = informe.construir(G.generar(copy.deepcopy(spec)), spec, SKILL)
        for txt in ("Resumen ejecutivo", "Diagrama de Gantt", "Camino crítico", "Curva S", "Aprobación"):
            self.assertIn(txt, html)
        self.assertTrue(html.startswith("<!doctype html>"))

    def test_auditoria_detecta_faltantes(self):
        s = spec_min(tareas=[{"id": "A", "nombre": "A", "duracion": 2}, {"id": "B", "nombre": "B", "duracion": 2}])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.gan")
            with open(p, "w", encoding="utf-8") as f:
                f.write(G.generar(s)["xml"])
            A, _ = validar_gan.auditar(p, None)
        codigos = {i["codigo"] for i in A.items}
        self.assertTrue({"RECURSOS", "HITOS", "LINEA-BASE"} <= codigos)


class TestEsquema(unittest.TestCase):
    def test_ejemplos_validan_contra_esquema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema no instalado")
        esquema = cargar(os.path.join(SKILL, "schema", "cronograma.schema.json"))
        for p in (PLANTILLA, BASICO):
            jsonschema.validate(cargar(p), esquema)


if __name__ == "__main__":
    unittest.main()
