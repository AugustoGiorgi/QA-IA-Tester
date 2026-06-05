# Objetivo
Extraer las variables necesarias para un proceso de VisualTime a partir de un texto/adjunto de negocio (evidencias, correos, especificaciones).

# Proceso objetivo
- Este prompt se usa para distintos procesos. Te paso el `proceso` y la lista de variables válidas.
- Procesos soportados ahora:
  - `emitir_poliza`
  - `endoso_poliza`

# Entradas (las recibe el modelo en el mensaje del usuario/sistema)
- `proceso`: uno de los anteriores.
- `texto_fuente`: contenido en lenguaje natural con los pasos/datos.
- `variables_validas`: **lista exacta de nombres de variables** que se permiten para ese proceso.

## Variables por proceso (referencia)
- **comunes** (en todos los procesos):
  - base_url (string)
  - user (string)
  - pass (string)
- `emitir_poliza`:
  - fecha_vigencia (string, formato dd/mm/aaaa)
  - sucursal (string) — default sugerido “Casa Central”
  - oficina (string)
  - agencia (string)
  - ramo (string)
  - producto (string)
  - via_pago (string)
  - titular (string)
  - codigo_contratante (string)
  - convenio (string)
  - cuotificar (boolean: true/false)
  - codigo_modulo (string)
- `endoso_poliza`:
  - numero_poliza (string)
  - nuevo_valor (string)

# Reglas
1) **No inventes** valores. Si no está claro o no aparece, dejar la variable sin incluir y listarla en `missing_vars`.
2) Usar solo nombres de `variables_validas`. Si detectás otras, listalas en `extra_vars_detected` (para diagnóstico).
3) Normalizar:
   - fechas → `dd/mm/aaaa`
   - booleanos → `true` / `false` (minúsculas)
   - strings → sin espacios extra (trim)
4) Si el texto trae varios candidatos para la misma variable, elegí el **más reciente** o el que esté **en el contexto principal** y deja un breve motivo en `notes`.
5) No mezclar semánticas: **no calcules** valores derivados; solo extraé lo explícito.

# Salida (JSON EXACTO, sin texto adicional)
{
  "variables": { "<nombre>": <valor>, ... },
  "missing_vars": ["<nombre>", ...],
  "extra_vars_detected": ["<nombre_no_listado>", ...],
  "notes": "<opcional: breve aclaración>"
}

# Ejemplo de salida
{
  "variables": {
    "base_url": "http://172.16.70.172/dropthings",
    "user": "admin",
    "pass": "admin123",
    "fecha_vigencia": "01/10/2025",
    "sucursal": "Casa Central",
    "oficina": "1",
    "agencia": "2",
    "ramo": "Integral Comercio e Industria",
    "producto": "10190",
    "via_pago": "Tarjeta de crédito",
    "titular": "12345",
    "codigo_contratante": "C-001",
    "convenio": "3 - Mastercard",
    "cuotificar": true,
    "codigo_modulo": "1"
  },
  "missing_vars": [],
  "extra_vars_detected": [],
  "notes": "Valores tomados de la sección Facturación y Cabecera."
}

# Instrucción final
Devolvé **solo** el JSON con ese esquema. Si no hay ninguna variable, devolvé `variables: {}` y `missing_vars` con todas las requeridas.
