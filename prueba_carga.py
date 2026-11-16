"""Prueba de carga de la API de Agix.

    set AGIX_URL=https://agixapp-agix-app.hf.space      (o http://127.0.0.1:7861)
    set AGIX_API_KEY=...                                (la del Space)
    python prueba_carga.py

Cuatro escenarios, en orden:
  1 un /backlog solo                      cuánto tarda sin competencia
  2 /estimar mientras se genera un backlog  /estimar no debe esperar a la generación
  3 ráfaga de 20 /estimar simultáneos      que no se caiga y cuánto esperan los últimos
  4 dos /backlog a la vez                  el segundo espera turno (~el doble)
Imprime latencias y códigos; no cambia nada en el servicio.
"""
import json
import os
import statistics
import sys
import threading
import time

import httpx

URL = os.environ.get("AGIX_URL", "http://127.0.0.1:7861").rstrip("/")
H = {"X-API-Key": os.environ.get("AGIX_API_KEY", "")}
DESC = ("Una aplicación web para reservar servicios de limpieza a domicilio, con citas "
        "recurrentes y pagos en la app.")
DESC2 = ("Portal de empleo para egresados de una universidad, donde las empresas publican "
         "ofertas y los egresados postulan con su CV.")


def backlog(desc, out, i):
    t = time.time()
    r = httpx.post(f"{URL}/backlog", json={"descripcion": desc}, headers=H, timeout=900)
    out[i] = (r.status_code, time.time() - t, r.json() if r.status_code == 200 else r.text)


def estimar(cuerpo, out, i):
    t = time.time()
    try:
        r = httpx.post(f"{URL}/estimar", json=cuerpo, headers=H, timeout=900)
        out[i] = (r.status_code, time.time() - t)
    except Exception as e:                       # noqa: BLE001
        out[i] = (type(e).__name__, time.time() - t)


def resumen(nombre, res):
    cod = [c for c, *_ in res]
    lat = [x[1] for x in res]
    print(f"  {nombre}: códigos {sorted(set(map(str, cod)))} · "
          f"mediana {statistics.median(lat):.1f} s · máximo {max(lat):.1f} s · n={len(lat)}")


def main():
    s = httpx.get(f"{URL}/salud", timeout=60).json()
    print(f"{URL} · {json.dumps(s, ensure_ascii=False)}")
    if s.get("estado") != "listo":
        sys.exit("el servicio no está listo")

    print("\n1 · un /backlog solo")
    o = [None]
    backlog(DESC, o, 0)
    cod, seg, r = o[0]
    if cod != 200:
        sys.exit(f"  /backlog devolvió {cod}: {r}")
    print(f"  200 en {seg:.1f} s · {r['n_tareas']} tareas · total {r['estimacion']['total_horas']} h")
    n = r["n_tareas"]
    cuerpo = {"historias": [{"tareas": h["tareas"]} for h in r["historias"]],
              "cerradas": [2.0] * min(5, n) + [None] * (n - min(5, n))}

    print("\n2 · /estimar mientras se genera otro backlog")
    ob = [None]
    hb = threading.Thread(target=backlog, args=(DESC2, ob, 0))
    hb.start()
    time.sleep(5)                                 # que la generación ya esté en marcha
    oe = []
    while hb.is_alive() and len(oe) < 5:
        x = [None]
        estimar(cuerpo, x, 0)
        oe.append(x[0])
    hb.join()
    resumen("/estimar durante la generación", oe)
    print(f"  el /backlog concurrente: {ob[0][0]} en {ob[0][1]:.1f} s")

    print("\n3 · ráfaga de 20 /estimar simultáneos")
    o = [None] * 20
    hs = [threading.Thread(target=estimar, args=(cuerpo, o, i)) for i in range(20)]
    t = time.time()
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    resumen("ráfaga", o)
    print(f"  la ráfaga entera en {time.time() - t:.1f} s")

    print("\n4 · dos /backlog a la vez")
    o = [None, None]
    hs = [threading.Thread(target=backlog, args=(d, o, i)) for i, d in enumerate((DESC, DESC2))]
    for h in hs:
        h.start()
    for h in hs:
        h.join()
    for i, (c, sg, _) in enumerate(o):
        print(f"  backlog {i + 1}: {c} en {sg:.1f} s")


if __name__ == "__main__":
    main()
