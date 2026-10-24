# Circadian Pro 0.21.31

Complete English and Spanish interface coverage, with consistent terminology across the panel and Home Assistant's native controls.

- Translate configuration and calibration fields, help texts, choices, validation messages and preview failures in both languages.
- Translate the three Home Assistant actions and all 47 action fields, the four switch controls, their displayed attribute labels and actionable service errors.
- Follow the user's interface language in the panel, including regional Spanish and English locales. Other languages fall back to English. Refresh translations after a language change while preserving unsaved settings and active preview sessions.
- Preserve profile names, custom entity names, entity IDs, unique IDs, calibration values, schedules and switch states. Native entity names follow Home Assistant's server language. Technical YAML/action identifiers and diagnostic logs keep their existing technical format.

No configuration migration is required. Regression tests check catalog completeness and placeholders, translated selectors and action fields, preserved entity identifiers, regional language selection, preview failure codes and language changes during editing.

Local validation: 347 Python tests and 189 frontend tests passed, plus source checks and Ruff. Browser rendering and installation on a live Home Assistant instance have not been verified for this release.

## En español

Interfaz completa en español e inglés, con términos coherentes en el panel y en los controles nativos de Home Assistant.

- Campos, ayudas, desplegables, mensajes de validación y errores de previsualización traducidos en ambos idiomas.
- Traducción de las tres acciones y sus 47 campos, los cuatro interruptores, las etiquetas de sus atributos y los errores de acciones.
- El panel sigue el idioma de la interfaz y admite variantes regionales. Si cambias el idioma, actualiza sus textos conservando los ajustes pendientes y la previsualización activa.
- Se mantienen los nombres elegidos, los identificadores, las calibraciones, los horarios y los estados de los interruptores. Los nombres nativos de las entidades siguen el idioma del servidor de Home Assistant.

No hace falta migrar la configuración. La validación local, los resultados de CI sobre la revisión exacta y la comprobación de instalación en Home Assistant se registran por separado en la publicación y en el registro privado de despliegue.
