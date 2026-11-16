"""API de Agix: de una descripción de producto a un backlog con horas.

Envuelve las dos tuberías del paquete de producción (generacion/src/19_pipeline.py y
generacion/src/34_estimacion.py) sin cambiarlas, siguiendo el contrato de
INTEGRACION.md:

  - los modelos se cargan UNA vez, al arrancar, en un hilo aparte para que el puerto
    responda mientras tanto (Hugging Face comprueba que el contenedor escucha);
  - una generación a la vez: los modelos no son seguros para uso concurrente, así que
    las peticiones esperan su turno detrás de un candado;
  - al estimador se le pasa el inglés (h["tareas"]); el adaptador ya lo hace.

Los modelos no van en la imagen. Se buscan en este orden:
  1 AGIX_BASE: una carpeta local que ya contiene generacion/ (pruebas en local)
  2 AGIX_MODEL_REPO: un repositorio de modelos de Hugging Face con el contenido de
    generacion/, que se descarga al arrancar con HF_TOKEN si es privado

Variables de entorno: AGIX_API_KEY (si está, se exige la cabecera X-API-Key),
AGIX_ESPERA_MAX (segundos que una petición espera turno, 600 por omisión), AGIX_SEED.
"""
import importlib.util
import logging
import math
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("agix")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _cpus():
    """Las CPU que el contenedor puede usar DE VERDAD.

    os.cpu_count() devuelve los núcleos de la máquina física, no la cuota del
    contenedor: en un Space de 2 vCPU puede decir 32, y torch lanza 32 hilos que se
    pelean por 2 núcleos. Se lee la cuota de cgroup (v2 y v1) y, si no hay, la
    afinidad del proceso.
    """
    try:
        q, p = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
        if q != "max":
            return max(1, int(int(q) / int(p)))
    except (OSError, ValueError):
        pass
    try:
        q = int(open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read())
        p = int(open("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read())
        if q > 0:
            return max(1, q // p)
    except (OSError, ValueError):
        pass
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


# antes de que nada importe torch, numpy u onnxruntime
HILOS = int(os.environ.get("AGIX_HILOS") or _cpus())
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, str(HILOS))

VERSION = "1.0.0"
ESPERA_MAX = float(os.environ.get("AGIX_ESPERA_MAX", 600))

ESTADO = {"estado": "cargando", "detalle": "", "inicio": time.time(), "carga_s": None,
          "estimador": None}
_M = {}                                   # GENERADOR, ESTIMADOR
# Un candado por tubería: el generador atiende de uno en uno (~1 min por backlog) y el
# estimador también, pero /estimar (~2 s) no espera a que termine una generación.
CANDADO_GEN = threading.Lock()
CANDADO_EST = threading.Lock()


# ------------------------------------------------------------------ carga
def _raiz():
    """La carpeta que contiene src/, models/, data/ y estimador/."""
    local = os.environ.get("AGIX_BASE")
    if local:
        if not (Path(local) / "src" / "19_pipeline.py").exists():
            raise RuntimeError(f"AGIX_BASE={local} no contiene src/19_pipeline.py")
        return Path(local)
    repo = os.environ.get("AGIX_MODEL_REPO")
    if not repo:
        raise RuntimeError("falta AGIX_BASE o AGIX_MODEL_REPO")
    from huggingface_hub import snapshot_download
    destino = os.environ.get("AGIX_CACHE", "/tmp/agix")
    ESTADO["detalle"] = f"descargando {repo}"
    t = time.time()
    d = snapshot_download(repo_id=repo, repo_type="model", local_dir=destino,
                          token=os.environ.get("HF_TOKEN"))
    log.info("modelos descargados en %.0f s", time.time() - t)
    return Path(d)


CODIGO = Path(__file__).resolve().parent.parent / "codigo"


def _codigo_del_repositorio(raiz):
    """El codigo de las tuberias viene del repositorio de GitHub, no del de modelos.

    El repositorio de modelos solo tiene que aportar los pesos y los datos. Si la imagen
    trae `codigo/` (src/ y estimador/src/), esos archivos sustituyen a los descargados,
    asi que un cambio de codigo se despliega con un push, sin volver a subir 3 GB."""
    if not CODIGO.exists():
        return
    import shutil
    for f in CODIGO.rglob("*.py"):
        destino = Path(raiz) / f.relative_to(CODIGO)
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, destino)
        log.info("codigo del repositorio: %s", destino.relative_to(raiz))


def _modulo(ruta, alias):
    spec = importlib.util.spec_from_file_location(alias, ruta)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    spec.loader.exec_module(m)
    return m


def _cargar():
    t0 = time.time()
    try:
        raiz = _raiz()
        _codigo_del_repositorio(raiz)
        os.environ["AGIX_BASE"] = str(raiz)
        os.environ.setdefault("AGIX_DEVICE", "cpu")
        os.environ["HF_HUB_OFFLINE"] = "1"          # a partir de aquí todo es local
        ESTADO["detalle"] = "cargando los modelos"
        import torch
        torch.set_num_threads(HILOS)
        torch.set_num_interop_threads(1)
        log.info("hilos: %d (os.cpu_count dice %s)", HILOS, os.cpu_count())
        # el codificador ONNX del estimador crea su sesión con SessionOptions() y,
        # sin más, ONNX Runtime usa los núcleos de la máquina física: se le fija la
        # cuota real sin tocar el paquete de modelos
        import onnxruntime as ort
        _base = ort.SessionOptions

        def _opciones():
            o = _base()
            o.intra_op_num_threads = HILOS
            o.inter_op_num_threads = 1
            return o
        ort.SessionOptions = _opciones
        pl = _modulo(raiz / "src" / "19_pipeline.py", "agix_pl")
        es = _modulo(raiz / "src" / "34_estimacion.py", "agix_es")
        _M["GENERADOR"] = pl.Pipeline(traducir=True)
        _M["ESTIMADOR"] = es.Estimacion(por_tarea=True)
        est = _M["ESTIMADOR"]
        ESTADO["estimador"] = {"disponible": est.disponible, "motivo": est.motivo}
        if est.disponible:
            # calentar: la primera estimación abre el codificador ONNX
            ESTADO["detalle"] = "calentando el estimador"
            est.estimar({"historias": [{"tareas": ["Add OAuth2 login with Google"]}]})
        ESTADO.update(estado="listo", detalle="", carga_s=round(time.time() - t0, 1))
        log.info("listo en %.0f s", time.time() - t0)
    except Exception as e:                       # noqa: BLE001
        log.exception("fallo al cargar")
        ESTADO.update(estado="error", detalle=f"{type(e).__name__}: {e}")


@asynccontextmanager
async def ciclo(_app):
    threading.Thread(target=_cargar, daemon=True).start()
    yield


app = FastAPI(title="Agix API", version=VERSION, lifespan=ciclo,
              description="Genera un backlog de historias de usuario con sus tareas a partir "
                          "de la descripción de un producto, y estima sus horas.")


# ------------------------------------------------------------------ utilidades
def _clave(x_api_key: Optional[str] = Header(default=None)):
    esperada = os.environ.get("AGIX_API_KEY")
    if esperada and x_api_key != esperada:
        raise HTTPException(401, "falta la cabecera X-API-Key o no es válida")


def _listo():
    if ESTADO["estado"] == "cargando":
        raise HTTPException(503, f"el servicio está arrancando ({ESTADO['detalle']}); "
                                 "vuelve a intentarlo en unos segundos",
                            headers={"Retry-After": "30"})
    if ESTADO["estado"] == "error":
        raise HTTPException(500, f"el servicio no pudo cargar los modelos: {ESTADO['detalle']}")


def _json(x):
    """numpy y demás a tipos de JSON; NaN e infinito a None."""
    if isinstance(x, dict):
        return {str(k): _json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json(v) for v in x]
    if hasattr(x, "tolist"):
        return _json(x.tolist())
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    try:
        return float(x)
    except (TypeError, ValueError):
        return str(x)


class _Turno:
    def __init__(self, candado):
        self.c = candado

    def __enter__(self):
        if not self.c.acquire(timeout=ESPERA_MAX):
            raise HTTPException(503, "el servicio está ocupado con otras peticiones; "
                                     "vuelve a intentarlo", headers={"Retry-After": "60"})

    def __exit__(self, *a):
        self.c.release()


# ------------------------------------------------------------------ esquemas
class PeticionBacklog(BaseModel):
    descripcion: str = Field(..., min_length=10, max_length=2000,
                             description="Descripción del producto, en español o inglés. "
                                         "Mejor si dice quién lo usa y qué tiene que poder "
                                         "hacer cada uno.")
    k_historias: int = Field(12, ge=4, le=20, description="Historias del backlog.")
    previo_equipo: Optional[float] = Field(
        None, description="Media de los `desplazamiento` guardados de los proyectos ya "
                          "terminados del equipo. Punto de partida del total.")


class Historia(BaseModel):
    tareas: List[str] = Field(..., description="Tareas EN INGLÉS, tal como las devuelve "
                                               "/backlog en el campo `tareas`.")

    model_config = {"extra": "allow"}


class PeticionEstimar(BaseModel):
    historias: List[Historia]
    cerradas: Optional[List[Optional[float]]] = Field(
        None, description="Horas reales de las tareas ya cerradas, en el orden en que "
                          "aparecen recorriendo las historias; null donde no estén cerradas.")
    respuestas: Optional[List[Optional[float]]] = Field(
        None, description="Horas que el usuario dice que tardó «la última vez» en algo así, "
                          "mismo orden, null donde no conteste. Anclan como una cerrada; si "
                          "una tarea tiene las dos, gana la cerrada.")
    previo_equipo: Optional[float] = Field(
        None, description="Media de los `desplazamiento` de los proyectos anteriores del equipo.")


class PeticionDesplazamiento(BaseModel):
    historias: List[Historia]
    horas: List[float] = Field(..., description="Horas reales de TODAS las tareas del proyecto "
                                                "terminado, en el orden en que aparecen.")


# ------------------------------------------------------------------ endpoints
@app.get("/")
def raiz():
    return {"servicio": "Agix API", "version": VERSION, "estado": ESTADO["estado"],
            "documentacion": "/docs"}


@app.get("/salud")
def salud():
    return {"estado": ESTADO["estado"], "detalle": ESTADO["detalle"],
            "carga_s": ESTADO["carga_s"], "estimador": ESTADO["estimador"],
            "generando": CANDADO_GEN.locked(), "estimando": CANDADO_EST.locked(), "version": VERSION,
            "hilos": HILOS, "cpu_count": os.cpu_count(),
            "segundos_encendido": round(time.time() - ESTADO["inicio"])}


@app.post("/backlog", dependencies=[Depends(_clave)])
def backlog(p: PeticionBacklog):
    """Genera el backlog y le pone horas. En CPU tarda del orden de un minuto."""
    _listo()
    t = time.time()
    with _Turno(CANDADO_GEN):
        r = _M["GENERADOR"](p.descripcion, k_historias=p.k_historias)
    with _Turno(CANDADO_EST):
        r = _M["ESTIMADOR"].estimar(r, previo_equipo=p.previo_equipo)
    r["segundos"] = round(time.time() - t, 2)
    return _json(r)


@app.post("/estimar", dependencies=[Depends(_clave)])
def estimar(p: PeticionEstimar):
    """Recalcula las horas de un backlog, anclando el total con las tareas cerradas."""
    _listo()
    r = {"historias": [h.model_dump() for h in p.historias]}
    n = sum(len(h["tareas"]) for h in r["historias"])
    for nombre, v in (("cerradas", p.cerradas), ("respuestas", p.respuestas)):
        if v is not None and len(v) != n:
            raise HTTPException(422, f"`{nombre}` tiene {len(v)} valores y el backlog "
                                     f"{n} tareas: tienen que coincidir")
        if v is not None and any(x is not None and x <= 0 for x in v):
            raise HTTPException(422, f"las horas de `{nombre}` tienen que ser positivas")
    with _Turno(CANDADO_EST):
        r = _M["ESTIMADOR"].estimar(r, cerradas=p.cerradas, respuestas=p.respuestas,
                                    previo_equipo=p.previo_equipo)
    r["n_tareas"] = n
    return _json(r)


@app.post("/desplazamiento", dependencies=[Depends(_clave)])
def desplazamiento(p: PeticionDesplazamiento):
    """El valor a guardar de un proyecto terminado; la media de los del equipo es su
    `previo_equipo` en el siguiente proyecto."""
    _listo()
    r = {"historias": [h.model_dump() for h in p.historias]}
    n = sum(len(h["tareas"]) for h in r["historias"])
    if len(p.horas) != n or any(x <= 0 for x in p.horas):
        raise HTTPException(422, f"`horas` necesita {n} valores positivos, uno por tarea")
    with _Turno(CANDADO_EST):
        d = _M["ESTIMADOR"].desplazamiento(r, p.horas)
    return {"desplazamiento": d, "n_tareas": n}
