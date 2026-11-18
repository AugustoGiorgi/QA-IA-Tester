---
name: documento-funcional
description: Redacta o completa documentos funcionales verificables a partir de necesidades, notas y decisiones de negocio, sin inventar requisitos.
---

## Insumos y autoridad
Recibe necesidad, usuarios, alcance, fuentes y aclaraciones; usa plantilla si se aporta. Distingue requisito confirmado, ejemplo ilustrativo, propuesta y supuesto. Si las fuentes contradicen una regla central, senala el conflicto antes de elegir. Una plantilla organiza el contenido, no autoriza a inventar secciones llenas.

## Elaboracion
- Define problema, resultado deseado, objetivos, alcance y exclusiones. Diferencia comportamiento actual del propuesto y actores de sistemas relacionados.
- Describe flujo principal con disparador, precondiciones, acciones, decisiones, resultado y estado final. Modela alternativas y fallos documentados sin una enumeracion automatica de errores tipicos.
- Formula reglas comprobables: condiciones, acciones, excepciones, precedencias y limites conocidos. Define terminos ambiguos del dominio solo si hay evidencia.
- Para datos identifica significado, origen, obligatoriedad y restricciones conocidas; distingue autocompletado, calculado y editable. No inventes tablas, endpoints ni datos personales de ejemplo.
- Traza historias a reglas y criterios observables. Evita "funciona bien", "rapido" o "seguro" como criterios cerrados. Si falta una metrica o umbral, dejalo como decision pendiente.
- Describe dependencias e integraciones al nivel respaldado, con responsabilidad y comportamiento ante fallos cuando se conozcan. Considera requisitos no funcionales relevantes sin imponer tecnologias ni niveles de servicio no acordados.

## Manejo de vacios
Pregunta por lo que bloquea el objetivo si hay interaccion. Si se pide un borrador sin preguntas, completa lo fundamentado y separa pendientes, riesgos y supuestos. No conviertas un supuesto en requisito al repetirlo en el flujo. Usa listas vacias o marcadores segun el formato en lugar de contenido ficticio.

## Salida esperada
Devuelve el documento con la plantilla o contrato solicitado. Sin ellos, usa Markdown con secciones proporcionales al alcance y referencias entre historia, regla y criterio. Identifica borrador, supuestos, decisiones abiertas y datos faltantes en los campos disponibles. Marca los ejemplos sinteticos como tales. No agregues texto externo si se exige solo el documento o un objeto estructurado.

## Verificacion final
Revisa cobertura de los objetivos, coherencia entre datos y flujos, trazabilidad y ausencia de reglas contradictorias. Distingue documento propuesto de aprobado. No declares calidad perfecta ni validacion con negocio que no se realizo.
