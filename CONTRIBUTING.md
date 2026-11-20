# Cómo contribuir

¡Gracias por tu interés! Se agradecen las mejoras de rendimientos, plantillas de otros tipos de obra
(edificios, galpones, obras viales), feriados de otros países y correcciones del formato `.gan`.

## Flujo

1. Hacé un fork y creá una rama: `git checkout -b feature/mi-mejora`.
2. Mantené la regla de **cero dependencias**: solo la biblioteca estándar de Python 3.8 o superior.
3. Agregá o actualizá los tests en `tests/` y ejecutá:
   ```bash
   python -m unittest discover -s tests -v
   ```
4. Si cambia la salida, regenerá el ejemplo:
   ```bash
   cd skills/ganttproject-gan/scripts
   python generar_gan.py ../assets/plantillas/vivienda_unifamiliar_completa.json ../../../examples/vivienda-unifamiliar/vivienda_unifamiliar.gan --resumen ../../../examples/vivienda-unifamiliar/resumen.md --csv ../../../examples/vivienda-unifamiliar/csv
   python informe.py ../assets/plantillas/vivienda_unifamiliar_completa.json ../../../examples/vivienda-unifamiliar/informe.html --pdf ../../../examples/vivienda-unifamiliar/informe.pdf
   ```
5. Anotá el cambio en `CHANGELOG.md` y abrí un pull request.

## Convenciones

- Código y mensajes al usuario en castellano; identificadores sin tildes.
- Todo cambio en el XML generado se verifica contra el código fuente de GanttProject
  (`ganttproject/src/main/java/net/sourceforge/ganttproject/io/` y `biz.ganttproject.core/.../io/XmlSerializer.kt`),
  documentándolo en `references/formato_gan.md`.
- Una nueva plantilla tiene que pasar `validar_gan.py` sin errores ni advertencias.
