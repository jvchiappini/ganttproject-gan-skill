---
name: ganttproject-gan
description: Crea cronogramas de obra completos y profesionales en formato .gan para GanttProject (Gantt con EDT, cómputos, rendimientos, cuadrillas, dependencias FS/SS/FF/SF, hitos, camino crítico, feriados PY/AR, recesos, línea base, costos, columnas personalizadas) con su Diagrama de Recursos (personas asignadas, roles, tarifas, nivelación). También genera un informe técnico (HTML/PDF) con Gantt, camino crítico, histograma de personal, plan de inversiones y curva S; audita archivos .gan existentes y los convierte para completarlos. Usar siempre que se pida un cronograma, plan o programación de obra, diagrama de Gantt, diagrama/histograma de recursos, curva S, informe de programación o cualquier archivo .gan/GanttProject.
---

# GanttProject (.gan): cronograma completo, recursos e informe

Cuatro herramientas en `scripts/` (solo Python ≥ 3.8 estándar, sin instalar nada):

| Script | Para qué |
|---|---|
| `generar_gan.py` | JSON → `.gan` (Gantt + Diagrama de Recursos), resumen `.md` y CSV |
| `informe.py` | JSON → informe técnico profesional `.html` (y `.pdf` con Edge/Chrome) |
| `validar_gan.py` | Audita cualquier `.gan`: errores y todo lo que le falta |
| `gan_a_json.py` | `.gan` existente → JSON editable (para completarlo y regenerarlo) |

**Nunca escribas el XML del .gan a mano.** Armá el JSON y usá los scripts: calculan fechas con el
calendario real, CPM, nivelación, asignaciones y costos, y producen un XML verificado contra el
código fuente de GanttProject.

## Flujo de trabajo

### 1. Reunir datos
Preguntá solo lo que no se pueda suponer razonablemente:
- Obra, **fecha de inicio**, comitente, ubicación.
- Calendario: días laborables (lun–sáb por defecto), país (`PY` por defecto, `AR`), recesos.
- EDT con tareas. **Si el usuario no la da completa, partí de `assets/plantillas/vivienda_unifamiliar_completa.json`**
  (17 rubros, 113 tareas, 9 hitos, 21 tipos de recurso) y adaptala; ver `references/guia_obra.md`.
- Por tarea: cómputo (cantidad + unidad) + rendimiento por cuadrilla + nº de cuadrillas, **o** duración.
- Recursos: tipos, rol, costo diario, y `disponibles` si hay dotación limitada.

Si el usuario tiene un `.gan`: **primero** `validar_gan.py` (para ver qué le falta) y
`gan_a_json.py` (para partir de su estructura). No pises su archivo: generá uno nuevo.

### 2. Escribir el JSON
Guardalo junto al `.gan` final (p. ej. `cronograma.json`) para que se pueda reeditar.
Esquema completo con autocompletado: `schema/cronograma.schema.json` (poné `"$schema"` en el JSON).

### 3. Lista de control de completitud (obligatoria antes de entregar)
Un cronograma "super completo" cumple **todo** esto:
- [ ] Todos los rubros de la obra: preliminares → movimiento de suelos → fundaciones → estructura →
      mampostería → techo → instalaciones (sanitaria, eléctrica, pluvial, débiles) → revoques →
      contrapisos → pisos/revestimientos → cielorrasos → aberturas/herrería/vidrios → pintura →
      obras exteriores → pruebas, limpieza y entrega → dirección y servicios permanentes.
- [ ] Hitos: acta de inicio, controles/inspecciones (excavación, armaduras, losa), fin de fundaciones,
      fin de estructura, obra bajo techo, entrega. El de entrega es la última tarea.
- [ ] Desfasajes técnicos: curado/desencofrado (FS+n), solapes reales (SS+n), espera de revoques antes de pintar.
- [ ] **Red cerrada**: toda tarea tiene predecesora (salvo la primera) y sucesora (salvo la entrega).
      El generador avisa de "cabos sueltos"; corregilos.
- [ ] Toda tarea de trabajo tiene recursos; las permanentes usan `abarca_obra: true`.
- [ ] Costos diarios en los recursos (y materiales si se conocen) → costos, plan de inversiones y curva S.
- [ ] Feriados de **todos** los años del plazo (el preset lo resuelve) y recesos.
- [ ] Línea base, notas automáticas y columnas personalizadas activadas (vienen por defecto).
- [ ] `validar_gan.py` sobre el `.gan` generado: 0 errores y sin advertencias relevantes.

### 4. Generar
```bash
S="<ruta-de-la-skill>/scripts"
python "$S/generar_gan.py" cronograma.json "Obra.gan" --resumen resumen.md --csv csv/
python "$S/validar_gan.py" "Obra.gan"
python "$S/informe.py"     cronograma.json "Informe - Obra.html" --pdf "Informe - Obra.pdf"
```
`generar_gan.py` imprime plazo, costo, camino crítico, tareas niveladas y avisos. Ante un
`ERROR:` (ciclos, ids inexistentes, recursos no definidos, demanda mayor que la dotación) corregí el JSON.

### 5. Entregar
Reportá: plazo (hábiles y corridos), fechas, costo, camino crítico, pico de personal, supuestos
(rendimientos de referencia, feriados trasladables por decreto que hay que agregar a mano).
Indicá cómo verlo en GanttProject:
- Pestaña **Diagrama de Gantt**: columnas Cómputo, Cuadrilla, Costo total y Holgura; botón *Camino crítico*;
  menú *Línea base* para comparar avance contra el plan.
- Pestaña **Diagrama de Recursos**: carga diaria de cada persona (rojo = sobrecarga).

## Esquema del JSON (resumen)

```json
{
  "proyecto": {"nombre": "Vivienda", "inicio": "2026-09-18", "empresa": "", "descripcion": ""},
  "informe": {"documento": "PCO-01", "revision": "A", "cliente": "", "ubicacion": "",
              "elaboro": "", "reviso": "", "aprobo": "", "logo": "logo.png", "moneda": "Gs.",
              "supuestos": [], "observaciones": []},
  "calendario": {"dias_laborables": ["lun","mar","mie","jue","vie","sab"], "feriados_preset": "PY",
                 "feriados": [{"fecha": "2026-10-12", "nombre": "Puente"}],
                 "periodos_no_laborables": [{"desde": "2026-12-24", "hasta": "2027-01-02", "nombre": "Receso"}]},
  "recursos": [{"id": "OF", "nombre": "Oficial albañil", "rol": "Albañilería", "costo_dia": 200000, "disponibles": 4}],
  "opciones": {"modo_recursos": "individual", "linea_base": "Plan original", "columnas_visibles": ["computo","cuadrilla","costo_total","holgura"]},
  "tareas": [
    {"id": "1", "nombre": "Preliminares", "hijos": [
      {"id": "1.1", "nombre": "Acta de inicio", "hito": true},
      {"id": "1.2", "nombre": "Limpieza del terreno", "cantidad": 300, "unidad": "m2", "rendimiento": 100,
       "cuadrillas": 1, "predecesoras": ["1.1"], "recursos": {"AY": 3}},
      {"id": "1.3", "nombre": "Obrador", "duracion": 4, "predecesoras": ["1.2SS+1"], "recursos": {"OF": 1, "AY": 2}}
    ]},
    {"id": "9", "nombre": "Permanentes", "hijos": [
      {"id": "9.1", "nombre": "Conducción de obra", "abarca_obra": true, "recursos": {"ENC": 1}}
    ]}
  ]
}
```

Reglas clave:
- Hojas: `duracion` (días hábiles) **o** `cantidad`+`rendimiento` (+`cuadrillas`) → `ceil(cantidad/(rend×cuadrillas))`
  **o** `hito: true` **o** `abarca_obra: true`.
- `predecesoras`: `"3.1"` (FS), `"3.1SS"`, `"3.1FF+2"`, `"4.1SS-1"`; también `CF`/`CC`/`FC`; o `{"id","tipo","desfase"}`.
  Solo entre **tareas hoja** (nunca a un rubro).
- `recursos`: cantidad **total** de la tarea por tipo (todas las cuadrillas); admite fracciones (0.5).
- Opcionales: `responsable`, `inicio_no_antes`, `avance`, `notas`, `color`, `prioridad`,
  `costo_materiales` o `precio_unitario_material` (× cantidad).
- `modo_recursos`: `individual` (personas concretas sin solapes; recomendado) o `agrupado` (un recurso por tipo con carga %).
- Nivelación: se activa si algún recurso tiene `disponibles`. Usa el método serie con prioridad de menor inicio
  tardío. Agrega vínculos de recurso al cálculo de holguras y fija `thirdDate` (inicio no antes de) para que
  GanttProject respete el desplazamiento.

## Referencias
- `references/formato_gan.md`: el XML de GanttProject (tareas, dependencias, roles, recursos, columnas personalizadas, línea base, costos).
- `references/guia_obra.md`: EDT típica, precedencias, desfasajes y rendimientos de referencia.
- `assets/plantillas/vivienda_unifamiliar_completa.json`: plantilla completa lista para adaptar.
- `assets/ejemplos/`: ejemplo básico.
