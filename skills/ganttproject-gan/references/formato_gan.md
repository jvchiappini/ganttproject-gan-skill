# Formato .gan (GanttProject): referencia

XML UTF-8. Lo verifiqué contra el código fuente de GanttProject (`ResourceSaver`, `AssignmentSaver`,
`GanttXMLSaver`, `RoleImpl`, `SchedulerImpl`) y contra un archivo real guardado por GanttProject.

## Estructura
```xml
<project name="" company="" webLink="http://" view-date="AAAA-MM-DD" view-index="0"
         gantt-divider-location="523" resource-divider-location="300" version="2.7.1" locale="es_AR">
  <description/>
  <view zooming-state="default:9" id="gantt-chart"> <field id="tpd3" name="Nombre" .../> ... </view>
  <view id="resource-table"> <field id="0" name="Nombre"/> <field id="1" name="Función"/> </view>
  <calendars>
    <day-types>
      <day-type id="0"/><day-type id="1"/>
      <default-week id="1" name="default" sun="1" mon="0" tue="0" wed="0" thu="0" fri="0" sat="0"/>  <!-- 1 = no laborable -->
      <only-show-weekends value="false"/><overriden-day-types/><days/>
    </day-types>
    <date year="2026" month="9" date="29" type="HOLIDAY" color="#ff3300"><![CDATA[Boquerón]]></date>
  </calendars>
  <tasks empty-milestones="true">
    <taskproperties>
      tpd0..tpd9 (propiedades por defecto)
      <taskproperty id="tpc0" name="Cómputo" type="custom" valuetype="text"/>   <!-- columna personalizada -->
    </taskproperties>
    <task id="1" name="Rubro" meeting="false" start="2026-09-18" duration="24" complete="0" expand="true">
      <task id="2" name="Tarea" color="#8cb6ce" meeting="false" start="2026-09-18" duration="6" complete="0" expand="true">
        <notes><![CDATA[texto]]></notes>
        <depend id="3" type="2" difference="0" hardness="Strong"/>
        <customproperty taskproperty-id="tpc0" value="120 m2"/>
      </task>
    </task>
  </tasks>
  <resources>
    <resource id="0" name="Oficial 1" function="1" contacts="" phone="">
      <rate name="standard" value="220000"/>
    </resource>
  </resources>
  <allocations>
    <allocation task-id="2" resource-id="0" function="1" responsible="true" load="100.0"/>
  </allocations>
  <vacations/>
  <taskdisplaycolumns> <displaycolumn property-id="tpd3" order="0" width="300" visible="true"/> ... </taskdisplaycolumns>
  <previous>
    <previous-tasks name="Línea base">
      <previous-task id="2" start="2026-09-18" duration="6" meeting="false" super="false"/>
    </previous-tasks>
  </previous>
  <roles roleset-name="Default"/>
  <roles> <role id="1" name="Albañilería"/> </roles>
</project>
```

## Semántica clave
- `start` es el primer día; `duration` se cuenta en **días hábiles** según el calendario. Si una tarea
  empieza el viernes 18 con duración 6 y el domingo no es laborable, ocupa 18,19,21,22,23,24 y su sucesora FS empieza el 25.
- Los feriados (`<date>`) llevan año: valen solo ese año.
- `<depend>` va **dentro de la predecesora**; `id` = id de la **sucesora**.
  `type`: 1 = SS (comienzo-comienzo), 2 = FS (fin-comienzo), 3 = FF, 4 = SF. `difference` = desfase en días.
- `hardness="Strong"`: al abrir, el planificador lleva la sucesora a la fecha más temprana permitida.
  `Rubber`: solo la empuja si se viola la restricción, así que respeta retrasos (nivelación, holgura usada).
- Hito: `meeting="true" duration="0"`.
- Tareas resumen: start/duration = envolvente de las hijas (GanttProject igual las recalcula).
- Roles: los del conjunto por defecto se refieren como `Default:N`; los roles propios del proyecto
  (bloque `<roles>` sin `roleset-name`) se refieren **solo por su número**: `function="1"`.
- `load` = % de dedicación del recurso en la tarea (100.0 = jornada completa). Más de 100 % por día
  en un recurso aparece como sobrecarga (rojo) en el Diagrama de Recursos.
- `<rate name="standard" value="…"/>`: costo por día del recurso (las versiones viejas lo ignoran).
- Prioridad: atributo `priority` (0 baja, 1 normal, 2 alta, 3 la más baja, 4 la más alta).
- Restricción de inicio "no antes de": `thirdDate="AAAA-MM-DD" thirdDate-constraint="1"`.
- Orden de los hijos de `<task>` (como lo escribe GanttProject): `notes`, `depend`*, `customproperty`*, subtareas.
- Columnas personalizadas: `<taskproperty id="tpcN" type="custom" valuetype="text|int|double|date|boolean">` y,
  en cada tarea, `<customproperty taskproperty-id="tpcN" value="..."/>`. Se hacen visibles con
  `<displaycolumn property-id="tpcN" order="k" visible="true"/>`.
- Costo de tarea: `cost-manual-value="1234" cost-calculated="false"` (si `cost-calculated="true"`, GanttProject
  lo calcula como tarifa × duración de los recursos asignados).
- Línea base: `<previous><previous-tasks name="..."><previous-task id start duration meeting super/>`.
- Vacaciones de recursos: `<vacations><vacation start="AAAA-MM-DD" end="AAAA-MM-DD" resourceid="0"/></vacations>`.
- Planificador (SchedulerImpl): con dependencias `Strong` la sucesora se lleva a la fecha más temprana
  permitida; para conservar un retraso voluntario (nivelación) se usa `thirdDate` + `thirdDate-constraint="1"`
  (inicio no antes de), que el planificador intersecta con las restricciones.
