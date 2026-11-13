# -*- coding: utf-8 -*-
"""
Lo que la capa aprendida de MP6 haría con los datos reales, para CAMBIOS-MP6.md: qué aprende el titular,
su efecto con y sin capa en la muestra de medir.py y, lo que manda, en los botones; las ofertas desde el
reenfoque que cambiarían de lado del umbral; la sugerencia de fondo que tocaría; y cuánto cuesta.

Trabaja sobre una COPIA de la carpeta de la copia (perfiles.db, su -wal y config.json): la primera
pasada completa los rasgos de las etiquetas viejas en `marcas.extra`, que es una escritura, y la copia
que se pasa no se toca. No llama a nada de fuera. No toca medir.py, que no puede ver la capa.

    PYTHONUTF8=1 python pruebas/medir_mp6.py <carpeta-de-la-copia> [--json]
"""

from __future__ import annotations

import json
import math
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

copia = Path(sys.argv[1]).resolve()
DATOS = Path(tempfile.mkdtemp(prefix="medir-mp6-"))
for nombre in ("perfiles.db", "perfiles.db-wal", "config.json"):
    if (copia / nombre).exists():
        shutil.copy2(copia / nombre, DATOS / nombre)
os.environ["JOBSPY_DATA_DIR"] = str(DATOS)
for variable in ("JOBSPY_TELEGRAM_CHAT_ID", "JOBSPY_TELEGRAM_TOKEN", "JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"):
    os.environ.pop(variable, None)

import aprendizaje  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import medir  # noqa: E402
import perfiles  # noqa: E402
from dedupe_difuso import normalizar_empresa, normalizar_texto  # noqa: E402
from reglas import titular_ciber  # noqa: E402

U = 5.0


def wilson(aciertos: int, n: int) -> str:
    """Intervalo de Wilson al 95 %: con 10 casos el número solo no dice nada."""
    if not n:
        return "—"
    p, z = aciertos / n, 1.96
    centro = (p + z * z / (2 * n)) / (1 + z * z / n)
    margen = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return "%d de %d = %.0f %% [%.0f-%.0f]" % (aciertos, n, 100 * p, 100 * (centro - margen), 100 * (centro + margen))


def main() -> int:
    salida_json = "--json" in sys.argv
    cfg = config_mod.cargar_config()
    chat = perfiles.titular()
    perfil = perfiles.obtener(chat)
    informe: dict = {}

    # 1. Lo que aprende, y lo que cuesta (en frío: completa los rasgos; y en caliente).
    tracemalloc.start()
    t0 = time.perf_counter()
    estado = aprendizaje.estado(perfil, cfg)
    frio = time.perf_counter() - t0
    pico = tracemalloc.get_traced_memory()[1] / 2**20
    tracemalloc.stop()
    aprendizaje.olvidar_cache()
    t0 = time.perf_counter()
    aprendizaje.estado(perfiles.obtener(chat), cfg)
    recalculo = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(20):
        aprendizaje.capa_de(perfiles.obtener(chat), cfg)
    en_cache = (time.perf_counter() - t0) / 20
    capa = estado["capa"]
    informe["capa"] = {"activa": estado["activa"], "etiquetas": estado["etiquetas"],
                       "pesos": {r: round(p, 2) for r, p in sorted(capa.pesos.items(), key=lambda kv: -kv[1])},
                       "protegidos": capa.protegidos}
    informe["coste"] = {"frio_s": round(frio, 3), "recalculo_s": round(recalculo, 3), "en_cache_s": round(en_cache, 4),
                        "pico_python_mib": round(pico, 2), "bytes_en_datos": len(perfiles.a_json(
                            perfiles.obtener(chat)["datos"].get("ajustes_aprendidos") or {}))}

    base = None  # el titular, con config global: el de config.json
    con = buscador.PuntuadorConCapa(buscador._puntuador_del_titular(), capa)

    def nota(oferta, puntuador):
        anos = buscador.extraer_anos_experiencia(oferta.get("description"))
        return buscador.explicar_relevancia(oferta.get("title"), oferta.get("location"), anos,
                                            oferta.get("description"), puntuador, oferta)[0]

    # 2. Los botones: lo que manda. Temporal: cada día se aprende con lo de antes y se mide ese día.
    ajustes = aprendizaje.ajustes(perfil)
    todas = aprendizaje.etiquetas_de(chat, ajustes)
    ofertas = perfiles.ofertas_por_url(chat, [e["url"] for e in todas])
    dias = sorted({e["fecha"][:10] for e in todas})
    filas = []
    for dia in dias:
        previas = aprendizaje.etiquetas_de(chat, ajustes, hasta=dia + "T00:00:00")
        pesos, _ = aprendizaje.aprender(previas, ajustes["olvidados"])
        capa_dia = aprendizaje.Capa(pesos, len(previas), [])
        p_dia = buscador.PuntuadorConCapa(buscador._puntuador_del_titular(), capa_dia)
        for e in todas:
            if e["fecha"][:10] != dia or e["url"] not in ofertas:
                continue
            o = ofertas[e["url"]]
            filas.append({"despues": e["fecha"] >= titular_ciber.VIGENTE_DESDE, "y": e["y"], "sin": nota(o, base),
                          "con": nota(o, p_dia) if len(previas) >= aprendizaje.MIN_ETIQUETAS else nota(o, base),
                          "titulo": o.get("title")})

    def precision(subconjunto, clave):
        sel = [f for f in subconjunto if f[clave] >= U]
        return wilson(sum(f["y"] for f in sel), len(sel))

    despues = [f for f in filas if f["despues"]]
    informe["botones"] = {
        "despues_del_reenfoque": {"n": len(despues), "sin": precision(despues, "sin"), "con": precision(despues, "con"),
                                  "cambian": [(f["titulo"], f["sin"], f["con"], f["y"]) for f in despues
                                              if (f["sin"] >= U) != (f["con"] >= U)]},
        "todas_temporal": {"n": len(filas), "sin": precision(filas, "sin"), "con": precision(filas, "con")},
    }

    # 3. Las ofertas que le han entrado desde el reenfoque: cuáles cruzan el umbral.
    recientes = [o for o in perfiles.ofertas_recientes(chat, titular_ciber.VIGENTE_DESDE)]
    completas = perfiles.ofertas_por_url(chat, [o["job_url"] for o in recientes])
    cruzan, altas_sin, altas_con = [], 0, 0
    for o in completas.values():
        s, c = nota(o, base), nota(o, con)
        altas_sin += s >= U
        altas_con += c >= U
        if (s >= U) != (c >= U):
            cruzan.append((o.get("title"), s, c))
    informe["desde_el_reenfoque"] = {"ofertas": len(completas), "altas_sin": altas_sin, "altas_con": altas_con,
                                      "cruzan": cruzan}

    # 4. La muestra de medir.py, con y sin capa, sin las ofertas que además son etiquetas.
    casos = medir.cargar("etiquetadas_ciber.json")
    claves = {(normalizar_texto(e["titulo"]), normalizar_empresa(e["empresa"])) for e in todas}
    limpios = [c for c in casos if (normalizar_texto(c["title"]), normalizar_empresa(c["company"])) not in claves]
    config_m, punt_m = medir.config_de_medida("titular-ciber")
    muestra = {"casos": len(casos), "sin_las_etiquetas": len(limpios)}
    for nombre, lista in (("entera", casos), ("sin_etiquetas", limpios)):
        for cual, p in (("sin", punt_m), ("con", buscador.PuntuadorConCapa(punt_m, capa))):
            df = medir.pipeline(lista, config_m, p)
            muestra[f"{nombre}_{cual}"] = medir.metricas(df, lista, U)
    informe["muestra"] = muestra

    # 5. La sugerencia de fondo que tocaría hoy, y su efecto en medir.py antes de ofrecerla.
    sug = aprendizaje.candidata(perfil, cfg)
    if sug:
        nueva = aprendizaje.config_con(perfil, cfg, sug)
        df = medir.pipeline(casos, {**config_m, sug["clave"]: nueva[sug["clave"]]},
                            buscador.crear_puntuador({**config_m, sug["clave"]: nueva[sug["clave"]]}))
        positivas = perfiles.urls_positivas(chat)
        quita = aprendizaje.efecto(perfil, cfg, sug, positivas)["fuera"]
        informe["sugerencia"] = {"texto": sug["texto"], "segura": not quita, "quitaria": [
            (ofertas.get(u) or perfiles.ofertas_por_url(chat, [u]).get(u, {})).get("title") for u in quita],
            "medir_con": medir.metricas(df, casos, U), "medir_sin": muestra["entera_sin"]}
    else:
        informe["sugerencia"] = None
    informe["cuando"] = datetime.now().isoformat(timespec="seconds")

    if salida_json:
        print(json.dumps(informe, ensure_ascii=False, indent=1, default=str))
        return 0
    c = informe["capa"]
    print(f"Capa del titular: {'activa' if c['activa'] else 'inactiva'}, {c['etiquetas']} etiquetas, "
          f"{len(c['pesos'])} rasgos con peso")
    for r, p in c["pesos"].items():
        print("  %+5.2f  %s" % (p, aprendizaje.nombre_de_rasgo(r)))
    print("  rebajados por la protección:", ", ".join(aprendizaje.nombre_de_rasgo(r) for r in c["protegidos"]) or "ninguno")
    print("\nCoste:", informe["coste"])
    b = informe["botones"]
    print("\nBotones, después del reenfoque (%d): sin capa %s · con capa %s" % (
        b["despues_del_reenfoque"]["n"], b["despues_del_reenfoque"]["sin"], b["despues_del_reenfoque"]["con"]))
    for t, s, cc, y in b["despues_del_reenfoque"]["cambian"]:
        print("    %s %.1f -> %.1f  %s" % ("⭐" if y else "🙈", s, cc, t))
    print("Botones, todas en temporal (%d): sin %s · con %s" % (b["todas_temporal"]["n"], b["todas_temporal"]["sin"],
                                                              b["todas_temporal"]["con"]))
    d = informe["desde_el_reenfoque"]
    print("\nDesde el reenfoque: %d ofertas, %d de Encaje alto sin capa y %d con ella" % (d["ofertas"], d["altas_sin"], d["altas_con"]))
    for t, s, cc in d["cruzan"]:
        print("    %.1f -> %.1f  %s" % (s, cc, t))
    m = informe["muestra"]
    for nombre in ("entera", "sin_etiquetas"):
        s, cc = m[f"{nombre}_sin"], m[f"{nombre}_con"]
        print("\nMuestra %s: sin capa %d avisos, %.0f %% / %.0f %%; con capa %d avisos, %.0f %% / %.0f %%" % (
            nombre, s["avisos"], s["precision"], s["recall"], cc["avisos"], cc["precision"], cc["recall"]))
    print("  (casos %d; sin los que también son etiquetas, %d)" % (m["casos"], m["sin_las_etiquetas"]))
    s = informe["sugerencia"]
    print("\nSugerencia de hoy:", "ninguna" if not s else json.dumps(s, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
