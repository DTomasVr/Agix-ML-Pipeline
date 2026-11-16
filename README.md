---
title: Agix API
emoji: 📋
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Agix API

De una descripción de producto a un backlog de historias de usuario con sus tareas y las
horas estimadas. Documentación interactiva en **`/docs`**.

## Endpoints

Si el Space tiene definida la variable `AGIX_API_KEY`, las peticiones `POST` tienen que llevar la
cabecera `X-API-Key: <clave>`.

### `GET /salud`

Estado del servicio. Úsalo para saber si ya arrancó.

```json
{"estado": "listo", "detalle": "", "carga_s": 75.2, "ocupado": false, "version": "1.0.0"}
```

`estado` es `cargando` (descargando o cargando los modelos), `listo` o `error`.

### `POST /backlog`

```json
{"descripcion": "Una aplicación web para reservar servicios de limpieza a domicilio, con citas recurrentes y pagos en la app.",
 "k_historias": 12}
```

Devuelve el backlog con horas:

```jsonc
{
  "historias": [{
    "historia_es": "Como cliente, quiero reservar servicios de limpieza…",
    "historia":    "As a customer, I want to book cleaning services…",
    "tareas_es":   ["Crear un formulario de reserva en el portal del cliente", "…"],
    "tareas":      ["Create a booking form in the customer portal", "…"],   // INGLÉS: lo que /estimar necesita
    "horas_probables": [4.0, 1.25],        // horas de cada tarea, para ENSEÑAR
    "horas_tareas":    [5.97, 3.81],       // las mismas sin redondear
    "horas_banda":     [[1.3, 26.6], [0.9, 17.0]],
    "horas_suma":      9.8                 // horas de la historia entera
  }],
  "estimacion": {"disponible": true, "total_horas": 289.9, "banda_baja": 105.3,
                 "banda_alta": 573.6, "nivel": 0.8, "tareas": 42, "cerradas": 0},
  "n_tareas": 42,
  "segundos": 33.4
}
```

Las listas de tareas, horas y bandas están **alineadas**: el índice *i* es la misma tarea.

**El total del proyecto es `estimacion.total_horas`. No lo sumes tú**: no es la suma de
`horas_probables` (son dos modelos distintos a propósito; ver `INTEGRACION.md` §3 del paquete).

### `POST /estimar`

Recalcula las horas de un backlog ya generado, **anclando el total** con las horas reales de las
tareas que ya se cerraron. Es rápido (~2 s) y es lo que se llama cada vez que se registra trabajo.

```json
{"historias": [{"tareas": ["Create a booking form in the customer portal", "…"]}, "…"],
 "cerradas": [3.5, null, 2.0, null, "…"]}
```

- `historias[].tareas` van **en inglés**, tal como las devolvió `/backlog` en `tareas`.
- `cerradas` tiene **un valor por tarea**, en el orden en que aparecen recorriendo las historias,
  con `null` donde la tarea no está cerrada.

## Errores

| código | cuándo | qué hacer |
|---|---|---|
| 401 | falta `X-API-Key` o no es válida | — |
| 422 | la petición no cumple el esquema (descripción de menos de 10 caracteres, `cerradas` de otra longitud…) | corregir la petición |
| 503 | arrancando, u ocupado más de 10 min | reintentar tras `Retry-After` segundos |
| 500 | no pudo cargar los modelos | revisar los registros del Space |

## Tiempos y límites

- **Atiende una generación a la vez**; las demás esperan turno. Pon un *timeout* de cliente de
  **al menos 3 minutos** en `/backlog`. Dos backlogs pedidos a la vez: el segundo tarda ~el doble.
- **`/estimar` no espera a las generaciones**: tiene su propia cola. Mientras se genera un backlog,
  `/estimar` sigue respondiendo en ~2 s. Las estimaciones también van de una en una: una ráfaga de
  20 simultáneas termina entera en ~30 s, sin errores.
- **No pidas horas tarea por tarea.** `/backlog` ya devuelve las de cada tarea. Llama a `/estimar`
  solo cuando cambie algo (se cierra una tarea, se añade o edita una), y siempre con el backlog
  ENTERO: el total del proyecto se calcula sobre todas las tareas.
- Medido en este Space (CPU basic, 2 vCPU): `/backlog` **~60 s** para 12 historias y 31 tareas
  (57,8 s de generación, 1,8 s de estimación), `/estimar` ~2 s, arranque ~1 min. En local con 12
  hilos, `/backlog` tarda 33 s.
- Si el Space está dormido, la primera petición lo despierta y tarda el arranque más la descarga
  de los modelos.

## Despliegue

1. Subir los modelos a un repositorio **privado** (una vez):
   ```bash
   set HF_TOKEN=hf_...      # token de escritura
   python subir_modelos.py <usuario>/agix-modelos
   ```
2. Crear el Space con **SDK Docker** y subir esta carpeta (`app/`, `Dockerfile`,
   `requirements.txt`, `README.md`).
3. En *Settings → Variables and secrets* del Space:
   - variable `AGIX_MODEL_REPO` = `<usuario>/agix-modelos`
   - secreto `HF_TOKEN` = un token de **lectura** con acceso a ese repositorio
   - secreto `AGIX_API_KEY` = la clave que usará el backend (opcional, recomendado)

## Probar en local, sin Docker

```bash
set AGIX_BASE=F:\Agix-produccion\generacion
python -m uvicorn app.main:app --port 7861
```
