# Objetivo
Generar el **DSL ejecutable** para un proceso de VisualTime a partir de su **template_dsl** y el set de variables provistas por el QA, validando faltantes.

# Entradas (las provee el backend)
- `proceso`: identificador del proceso (ej: `emitir_poliza`, `endoso_poliza`).
- `template_dsl`: texto del DSL con placeholders `${variable}` y alias `@alias` definidos en selectors.csv.
- `variables_provided`: objeto con pares `nombre → valor` que cargó el QA para este caso.
- `variables_required`: lista de variables obligatorias para el proceso.
- `variables_defaults` (opcional): objeto con defaults (si existe, suple ausencias).

# Reglas
1) **NO inventes** variables ni aliases. Trabajá **solo** con lo que viene en `template_dsl` y en las listas de variables.
2) **No reemplaces** los `${variable}` por valores. El DSL debe salir **con placeholders** (la interpolación se hace en runtime).
3) Extraé todos los nombres `${variable}` que aparezcan en `template_dsl`. Marcá como faltantes aquellos que:
   - estén en `variables_required` y **no** estén en `variables_provided` **ni** en `variables_defaults`,
   - o estén presentes pero vacíos (string vacío o null).
4) **Comandos permitidos únicamente** (no agregues otros):  
   `LOGIN, NAV, CLICK, TYPE, SELECT, ASSERT_TEXT, WAIT, API, SHOT, SET, USE`
5) **Alias `@alias`**: no crees ni modifiques alias; preservá exactamente los que vienen en el `template_dsl`.
6) Si `@spinner` existe en selectors.csv (el backend te lo puede indicar con un flag), es válido que el `template_dsl` ya incluya líneas `WAIT sel=@spinner state=hidden`. **No agregues waits** nuevos por tu cuenta.
7) Salida **solo JSON**, sin texto extra.

# Salida (JSON exacto)
{
  "dsl": "<copia fiel de template_dsl>",
  "missing_vars": ["<variable>", "..."],
  "notes": "<opcional: breve aclaración si hubo ambigüedad>"
}

# Ejemplo de salida
{
  "dsl": "NAV path=\"/\"\\nCLICK sel=@botonAbrirLogin\\nTYPE sel=@inputUsuario value=${user}\\n...",
  "missing_vars": ["fecha_vigencia"],
  "notes": "Falta fecha_vigencia y no hay default; el resto OK."
}
