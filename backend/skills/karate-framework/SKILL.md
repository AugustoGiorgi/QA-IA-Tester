---
name: karate-framework
description: Escribe o revisa features Karate para pruebas funcionales de APIs a partir de contratos, ejemplos y escenarios del proyecto.
---

## Entrada
Identifica metodo, ruta, parametros, autentificacion, payload, reglas, respuestas y version de Karate a partir del contrato, casos y repo que suministre el usuario. Un ejemplo observado ayuda a verificar un campo, no sustituye el contrato; registra conflictos en vez de acomodar asserts a un fallo.

## Diseno
- Usa un Feature por capacidad y Background para URL/configuracion o esquemas realmente compartidos. No fijes hosts ni rutas de carpetas. Adapta el runner y las convenciones al proyecto cuando existan; parametriza base URL, credenciales y datos sinteticos.
- En el camino feliz, valida en el mismo escenario el status documentado, el tipo de contenido cuando importa, estructura y efectos de negocio. Usa `match each` para elementos, `contains deep` para subconjuntos anidados y matchers como `##string` solo cuando se admiten ausencia o null. Si deben venir filas, afirma cardinalidad antes de validar sus campos.
- Usa Scenario Outline para variantes del mismo flujo. Distingue errores de entrada y negocio solo si el contrato indica su resultado; no asumas codigos, mensajes o un formato de errores universal. En filtros o paginacion valida contenido y limites observables, no solo status.
- Si una respuesta de infraestructura requiere un doble, usa un mock local acordado o marca el caso pendiente; no provoques fallos en servicios reales. Mantiene escenarios aislados y sin dependencia de orden ni secretos en el repositorio. Fija datos de fecha si la prueba necesita reproducibilidad.

## Entrega
Entrega `.feature` y, si hace falta, la configuracion minima, variables, datos requeridos y pendientes. No inventes rutas ni fixtures externos. Si el proyecto brinda runner y ambiente autorizado, revisa sintaxis o corre solo lo solicitado; de lo contrario, identifica la validacion que queda pendiente.
