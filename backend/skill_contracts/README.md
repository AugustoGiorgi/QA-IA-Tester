# Contratos de esta integracion

Estos archivos fijan los formatos y restricciones que la aplicacion espera.
No forman parte de las skills exportables. Para cada llamada, el cargador
lee el cuerpo de un solo `SKILL.md` y le agrega su contrato si esta registrado
en `APP_CONTRACT_SKILLS`. No manda el frontmatter ni carga otras skills.

Al sumar un flujo, escribi la skill sin nombres, rutas o dependencias de un
proyecto concreto. Registra su nombre en `SKILL_NAMES` y agrega un contrato solo si
la integracion necesita una estructura o restriccion adicional. Conserva los
documentos y datos variables en el mensaje de contexto. El cargador cachea el
prompt compuesto; un cambio requiere reiniciar el backend.
