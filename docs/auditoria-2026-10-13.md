# Auditoría de Circadian Pro — 13 de octubre de 2026

Base revisada: versión estable **0.21.3**, commit `2382f7012ea29943b5ff1a756b39c5f471d4c8c0`.
Correcciones: rama `codex/full-code-audit`, versión de desarrollo **0.21.4**.

## Alcance y método

Se revisaron los once módulos Python, el panel JavaScript, los servicios, las traducciones, el manifiesto y las pruebas. La primera revisión se dividió entre motor de iluminación, integración con Home Assistant y calibración/interfaz. Una segunda revisión independiente recorrió de nuevo todo el código actualizado y comprobó las interacciones entre esos módulos.

Los fallos corregidos cuentan con reproducciones o regresiones sobre lógica de producción. Las pruebas usan dobles para las entradas/salidas de Home Assistant; las pruebas solares usan también Astral 2.2 real. Las carreras de tareas se verifican con `asyncio` real. Se contrastaron los contratos utilizados con el código oficial de Home Assistant 2026.10.1.

Esta auditoría no modifica la instalación de Home Assistant ni sus perfiles. La versión de desarrollo necesita la validación final con luces reales antes de convertirse en una nueva versión estable.

## Hallazgos corregidos

| Prioridad | Problema reproducido | Corrección y evidencia |
| --- | --- | --- |
| Alta | Una previsualización de una luz apagada podía cancelar su propia secuencia al recibir `off → on`: aplicaba brillo, omitía color y devolvía éxito. La restauración podía borrar las marcas de control manual. | Los eventos de una previsualización activa quedan bajo su propio gestor; los apagados y cambios externos conservan prioridad. Pruebas integradas de preview, gestor y ejecutor en `test_audit_preview_manager.py`. |
| Alta | Un manejador de luces bloqueado podía impedir terminar o cerrar la preview indefinidamente: su TTL se registraba después del envío y Stop esperaba el mismo lock. | Límite de diez segundos por servicio y presupuesto global de sesenta segundos para aplicar/restaurar. La cancelación del solicitante se propaga; se liberan reservas incluso si restaurar falla. Regresiones con tareas reales bloqueadas. |
| Alta | Con brillo/color separados y demora cero, el color podía ejecutarse antes de finalizar la primera orden de brillo. | El interceptor continúa después de terminar correctamente el manejador original; la ejecución normal espera cada servicio antes del siguiente. Las luces distintas siguen ejecutándose en paralelo. |
| Alta | Home Assistant emite `call_service` antes de validar permisos: una orden denegada podía borrar control manual o impedir restaurar una previsualización. Los servicios genéricos podían modificar estado interno sin verificar acceso. | Comprobación central de permisos antes de modificar configuración, cachés, tareas o control manual. Se conserva el actor en las órdenes derivadas y el filtrado de objetivos `all`. |
| Media | Una llamada con varias luces reutilizaba atributos de una luz para otras con capacidades o rangos diferentes; podía repetir un `toggle` ya ejecutado al fallar una orden posterior. | Plan independiente por luz, conservación de parámetros no adaptados, objetivos explícitos y recuperación individual. `test_audit_interception.py`. |
| Media | La detección de control manual perdía el brillo tras enviar el segundo comando de una secuencia. La normalización RGBWW → CCT de WLED podía confundirse con un cambio humano. | Se acumula el objetivo completo sin alterar las órdenes enviadas; se conservan confirmaciones de color compatibles y se compara el brillo solicitado. `test_color_temperature_bounds.py` y `test_audit_switch.py`. |
| Media | Comandos pendientes podían ejecutarse después de apagar el perfil, comenzar una calibración o tomar control manual. | Comprobaciones antes del envío, limpieza por identidad de tarea y excepciones explícitas para aplicar/calibrar. |
| Media | Las luces de blanco sin RGB no recibían una temperatura válida cuando el modo sueño estaba configurado con RGB. En luces RGB+CCT no siempre se respetaba la preferencia de CCT nativo. | Selección según capacidades reales y límites de la entidad; RGB conserva los tonos cálidos fuera del rango CCT nativo. |
| Media | Cierre durante solicitudes, fallo al volver atrás o pérdida de una respuesta podían dejar el panel vacío, un flujo abierto o un envío repetido al paso equivocado. | Guardias de cierre, borradores visibles durante reconstrucción, limpieza idempotente y reconciliación mediante GET antes de repetir escrituras. Un guardado incierto se comunica sin repetirlo automáticamente. |
| Media | Deshabilitar campos antes de comprobarlos hacía que la validación nativa omitiera números obligatorios. | Los valores se validan y capturan antes de bloquear el formulario. `test_audit_panel.mjs`. |
| Media | Dos editores del mismo perfil podían detener mutuamente sus previews mediante una orden de cierre tardía. | Identificador por sesión; reserva por propietario, rechazo del segundo editor y cierre que solo afecta a su sesión. Expiración y descarga de la integración conservan limpieza obligatoria. |
| Media | La reserva de nuevas luces durante una preview cedía el control mientras restauraba las anteriores. Un fallo de restauración podía retener reservas. | Reserva antes de esperar I/O y limpieza aun cuando un controlador falle. Los cambios físicos y las órdenes externas se conservan. `test_audit_calibration.py`. |
| Media | Fallos o cancelación al cargar una plataforma podían dejar gestores/listeners huérfanos o afectar a perfiles hermanos. HA puede registrar algunos fallos sin propagarlos. | Limpieza por identidad, aislamiento de fallos de teardown y marcador de plataforma terminada. `test_audit_config.py`. |
| Media | Los perfiles heredados podían perder su modo sueño por aplicar valores nuevos antes de inferir la configuración anterior. Duplicar un perfil YAML podía perder datos efectivos. | Normalización previa, migración persistida una vez y copia de datos/opciones efectivos con serialización de horarios. |
| Media | El formulario de opciones YAML repetía indefinidamente la primera pantalla. | Salida informativa traducida que indica editar YAML, sin crear overrides. |
| Media | La interpolación nocturna era incorrecta cuando el Kelvin de sueño superaba el mínimo del perfil. Horarios solares coincidentes podían producir divisiones por cero. | Interpolación con diferencia firmada y validación de coincidencias después de offsets. |
| Media | Astral no produce amanecer/atardecer durante día o noche polar y el cálculo podía fallar por completo. Python 3.14 también cambia el mensaje matemático que Astral 2.2 utiliza para reconocer esa situación. | Fallback limitado a esos errores físicos, basado en elevación solar y extremos configurados; reconocimiento del origen y argumento matemático sin ocultar errores inesperados. Astral real en Tromsø, verano/invierno, con las tres curvas. `test_audit_solar.py`. |
| Baja | Valores cero se eliminaban al dividir órdenes; grupos anidados no se resolvían bien; formatos nuevos de eventos y objetivos no se reconocían completamente. | Preservación de cero, expansión con detección de ciclos, lectura de `params` y resolución HA de entidad/área/dispositivo/planta/etiqueta. |
| Baja | El hash corto de nombres solo codificaba su prefijo; el historial de estados externos podía crecer indefinidamente. | Hash SHA-256 abreviado y retención de los informes propios necesarios con límite de ocho por orden. |
| Baja | Un proxy de servicios podía perder respuesta, metadatos, tipo de trabajo o cambios anidados al fallar una interceptación. | Copia profunda, preservación del contrato de servicios y retirada idempotente sin sustituir wrappers posteriores. `test_audit_lighting.py`. |
| Baja | Los selectores del panel mostraban valores internos como `tanh` o `schedule`. | El API entrega el catálogo de selectores y el panel utiliza sus traducciones ES/EN sin cambiar los valores guardados. |

La prioridad expresa el impacto funcional, no un cálculo de probabilidad de explotación.

## Eficiencia y claridad

- Brillo y color comparten una única muestra solar. En el escenario normal, el recuento determinista pasa de **24 a 12 llamadas Astral por muestra**.
- Un lote comparte esa muestra entre sus luces. Antes se calculaba para publicar atributos y de nuevo para cada luz; ahora se calcula una vez. El valor predeterminado de transición se resuelve antes de muestrear para conservar el objetivo al final de la transición.
- El gestor solo reconstruye sus luces/timers cuando cambian las luces o los ajustes que lo requieren.
- La expansión de grupos visita una vez cada entidad, elimina duplicados y termina ante ciclos.
- Los informes propios retenidos tienen límite y las tareas se retiran por identidad, evitando que la finalización de una tarea antigua borre la nueva.
- Permisos, parámetros normalizados, objetivos y ciclo de vida de flujos tienen puntos de decisión reutilizables y documentados.

Son reducciones verificadas de trabajo y de estado retenido. No se ha medido una aceleración en milisegundos ni el consumo de la Raspberry Pi.

## Funcionalidad preservada

Continúan disponibles perfiles independientes, restauración de interruptores, RGB/RGBW/RGBWW/CCT, mezcla de blancos físicos, calibraciones de 1000/4000 K, curvas Natural/Lineal/Sigmoide, horarios y offsets, sueño manual/automático, servicios, interceptación de encendido/toggle, control manual parcial, resets y previews temporales reversibles.

La calibración mantiene sus referencias visuales y no redefine las temperaturas físicas de los LEDs. Una luz solo de blanco permanece dentro de su rango; una luz con RGB puede producir la apariencia cálida calibrada por debajo de ese rango.

## Verificación y límites

Validación local final: **222 pruebas Python y 73 pruebas JavaScript pasan: 295 en total, sin skips**. Pasan también la comprobación de los once módulos Python y cuatro documentos JSON, todas las claves de traducción requeridas, Ruff `E9,F,B` y `git diff --check`. Entorno local: Python 3.12.14, Node.js 24 y Astral 2.2.

GitHub Actions incorpora esas comprobaciones en Python 3.12 y 3.14. El resultado remoto debe comprobarse sobre el commit publicado; las pruebas locales no equivalen a un resultado CI confirmado.

No se puede afirmar ausencia absoluta de fallos. El interceptor sigue usando el registro interno de servicios de Home Assistant; está protegido con recuperación y mantiene el modo reactivo si ese contrato cambia. Las conversiones de canales y los tiempos reales dependen de cada integración/controlador. Un timeout libera recursos y comunica el fallo, pero no garantiza que un controlador desconectado pueda restaurarse ni revoca una orden que el dispositivo ya recibió. Antes de instalar esta versión se debe probar en Home Assistant el encendido con brillo/color separados, el control manual, las previews/restauraciones, permisos personalizados, respuestas tardías y el reinicio con los perfiles existentes.

Fuentes primarias contrastadas: [Core 2026.10.1](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/core.py), [servicios y permisos](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/service.py), [resolución de objetivos](https://github.com/home-assistant/core/blob/2026.10.1/homeassistant/helpers/target.py), [dependencias de HA](https://github.com/home-assistant/core/blob/2026.10.1/pyproject.toml) y [Astral 2.2](https://github.com/sffjunkie/astral/tree/2.2).
