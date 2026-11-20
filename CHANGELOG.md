# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
[Versionado Semántico](https://semver.org/lang/es/).

## [2.0.0] - 2026-11-20

### Añadido
- `informe.py`: informe técnico profesional en HTML autocontenido y PDF (Edge/Chrome sin interfaz). Incluye
  portada, resumen ejecutivo, bases, EDT, Gantt apaisado, camino crítico, plan de trabajos, histograma de
  personal, recursos limitados, plan de inversiones, curva S, observaciones, anexos y firmas.
- `validar_gan.py`: auditoría de completitud y consistencia de cualquier `.gan`.
- `gan_a_json.py`: conversión de `.gan` existentes a la especificación JSON (ida y vuelta sin pérdida de fechas).
- Plantilla completa de vivienda unifamiliar: 17 rubros, 113 tareas, 9 hitos y 21 tipos de recurso.
- Tareas permanentes (`abarca_obra`), recesos (`periodos_no_laborables`), costos por tarea (mano de obra y
  materiales), columnas personalizadas, línea base, holgura libre, responsable por tarea.
- Salidas CSV: tareas, histograma semanal y curva S.
- JSON Schema para autocompletado y validación.
- Suite de tests e integración continua (Linux y Windows, Python 3.8 a 3.13).

### Cambiado
- Nivelación por método serie con prioridad de menor inicio tardío. Considera las tareas permanentes y agrega
  vínculos de recurso al cálculo de holguras, por lo que el camino crítico resulta continuo.
- Las tareas niveladas se fijan con *inicio no antes de* (`thirdDate`), en lugar de dependencias Rubber.
- La asignación individual tiene en cuenta la carga: una persona al 20 % puede tomar otras tareas.

## [1.0.0] - 2026-11-20

### Añadido
- Primera versión: `generar_gan.py` con calendario PY/AR, CPM, EDT, hitos, recursos individuales o agrupados
  y nivelación básica.
