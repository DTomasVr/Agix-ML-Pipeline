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

API que convierte la descripción de un producto en un backlog de historias de usuario con sus
tareas, y estima las horas de cada tarea y del proyecto. La estimación del total se afina a
medida que el equipo aporta lo que sabe y registra horas reales.

## Qué hace

| endpoint | para qué |
|---|---|
| `POST /backlog` | Genera el backlog a partir de una descripción (en español o en inglés) y le pone horas: por tarea, por historia y el total del proyecto con un intervalo del 80 %. |
| `POST /estimar` | Recalcula las horas de un backlog. Acepta las horas reales de las tareas ya cerradas y las que el usuario recuerda de trabajos parecidos; con ellas el total se reancla y el intervalo se estrecha. |
| `POST /desplazamiento` | Al terminar un proyecto, devuelve un valor que resume cuánto se desvió del modelo. La media de los del equipo sirve de punto de partida para su siguiente proyecto. |
| `GET /salud` | Estado del servicio. |

La documentación interactiva, con los esquemas de cada petición, está en `/docs`.

## Cómo está hecho

- **Generación:** dos modelos mT5-small afinados encadenan descripción → historias → tareas, y
  una selección por cobertura garantiza una historia por cada función que nombra la descripción.
- **Estimación:** un codificador de frases multilingüe y regresores apilados, con dos cabezas: la
  mediana para mostrar cada tarea y la media para sumar el proyecto.
- Todo corre en CPU y sin servicios externos.

El código de las tuberías está en `codigo/` y el servicio en `app/`.

## Ramas

Los cambios se preparan en `prod` y se fusionan en `main`, que es la versión desplegada.
