# Skills de procesos de IA

Cada llamada a IA carga solo el `SKILL.md` de su proceso desde `services/skills.py`.
Las instrucciones estables y el contrato de salida viven en ese archivo; el codigo
envia por separado el documento, el historial y los datos variables. Para crear un
proceso nuevo, agrega su carpeta, registrala en `SKILL_NAMES` y usa `build_messages`
o `load_skill` en el punto de llamada. No insertes instrucciones estables en el
mensaje del usuario.

`load_skill` conserva el contenido en memoria durante la vida del proceso: un
cambio al archivo requiere reiniciar el backend. Esto evita lecturas repetidas, no
el costo de tokens de la API. El ahorro de tokens proviene de acortar instrucciones
duplicadas y enviar solo la skill y el contexto necesarios en cada solicitud.
