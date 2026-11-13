# -*- coding: utf-8 -*-
"""
Las medidas de MP3, sobre una copia de los datos del servidor. La red es de mentira (espías, como
en test_multiperfil.py): lo que se mide es lo que el sistema añade por perfil, no lo que tarda un
portal. Nunca toca la copia: trabaja sobre carpetas temporales. No imprime ningún chat_id.

    PYTHONUTF8=1 python pruebas/medir_mp3.py <carpeta-de-la-copia> [perdidas|jooble|carga|todo]

- `perdidas`: lo que el adicional deja de recibir con el tope de 1 año, con nombres.
- `jooble`: cuánto trae de verdad cada petición, para juzgar el reparto del cupo.
- `carga`: tiempo de CPU, memoria y escritura de una corrida con 1, 2, 3 y 5 perfiles. Un proceso
  por medida y la mediana de 3. La primera corrida de cada proceso no cuenta como régimen, igual
  que en el Toshiba (CAMBIOS-MP0.md §7).

**De qué está hecha una corrida simulada.** Lo que un portal devuelve y los filtros descartan no
se guarda, así que no se puede reproducir tal cual. Se reconstruye con lo que sí se sabe:

- las que el titular tiene visibles y vistas en la ventana de `hours_old`;
- las que tiene ocultas publicadas en esa ventana: las sigue devolviendo el portal, a él se le
  quitan al principio y al adicional no. Es lo que le cuesta de más, y es un dato real;
- hasta 322 con el resto, presenciales en otra ciudad: las 322 con descripción y ~300 «presenciales
  lejos» que da el log del Toshiba (DESPLIEGUE.md). Salen de ofertas viejas con la ubicación
  cambiada, y caen en el filtro barato como en real.

Sale de ahí un número del PC, que vale como proporción. Para traerlo al Atom se calibra con el coste
de langdetect, que está medido allí (60,43 ms por oferta, buscador.evaluar_caro): es una estimación.
"""

from __future__ import annotations

import ctypes
import gc
import gzip
import json
import logging
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
ADICIONAL = 92000002
REPETICIONES = 3
CORRIDAS_DE_REGIMEN = 3
OFICIOS = {  # los perfiles de más: otros sectores, con sus propias consultas
    3: "plan_mozo_zaragoza.json",
    4: "plan_administrativo_valencia.json",
    5: "plan_mozo_zaragoza.json",
}


def preparar_copia(copia: Path) -> Path:
    carpeta = Path(tempfile.mkdtemp(prefix="medir-mp3-"))
    for nombre in ("perfiles.db", "perfiles.db-wal", "config.json", "descripciones_vistas.json",
                   "descripciones_vistas.db", "descripciones_vistas.db-wal",
                   "geocode_cache.json", "salud_fuentes.json", "jooble_uso.json"):
        if (copia / nombre).exists():  # abrir la copia consolida el -wal en la base
            shutil.copy2(copia / nombre, carpeta / nombre)
    return carpeta


def entorno_de(carpeta: Path) -> None:
    os.environ.update(JOBSPY_DATA_DIR=str(carpeta), JOBSPY_TELEGRAM_TOKEN=TOKEN)
    os.environ.pop("JOBSPY_JOOBLE_KEY", None)


def memoria() -> tuple[float, float]:
    """(RSS, pico) en MiB. Es lo que en el PC corresponde a VmRSS y VmHWM."""

    class Contadores(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    c = Contadores()
    c.cb = ctypes.sizeof(c)
    psapi, kernel = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Contadores), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20


# ----------------------------------------------------------------------
# Lo que pierde el adicional
# ----------------------------------------------------------------------
def perdidas(copia: Path) -> dict:
    carpeta = preparar_copia(copia)
    entorno_de(carpeta)
    logging.disable(logging.WARNING)
    import buscador
    import config as config_mod
    import perfiles

    cfg = config_mod.cargar_config()
    titular = perfiles.titular()
    with gzip.open(copia / "descripciones_podadas.json.gz", "rt", encoding="utf-8") as f:
        podadas = json.load(f)
    ofertas = perfiles.ofertas_de(titular)
    for o in ofertas:
        if not o.get("description") and o.get("job_url") in podadas:
            o["description"] = podadas[o["job_url"]]
    final = max(o["visto_por_primera_vez"] for o in ofertas)
    corte = (datetime.fromisoformat(final) - timedelta(days=30)).isoformat(timespec="seconds")
    en_ventana = [o for o in ofertas if o["visto_por_primera_vez"] >= corte]
    del_adicional = {"chat_id": ADICIONAL, "version": 1, "datos": perfiles.datos_de_plantilla()}
    puntuador = perfiles.puntuador_de(del_adicional, cfg)
    marcas = perfiles.marcas(titular)
    altas, por_tope, por_nota = [], [], []
    for o in en_ventana:
        anos = buscador.extraer_anos_experiencia(o.get("description"))
        nota = buscador.explicar_relevancia(o.get("title"), o.get("location"), anos, o.get("description"))[0]
        if nota < 5:
            continue
        o["_anos"], o["_nota"] = anos, nota
        altas.append(o)
        if anos is not None and anos > 1:
            por_tope.append(o)
        elif buscador.explicar_relevancia(o.get("title"), o.get("location"), anos, o.get("description"),
                                          puntuador, o)[0] < 5:
            por_nota.append(o)
    # Lo que de verdad salió por Telegram, y le llegó también a él como copia.
    avisos = perfiles.avisos(titular)
    con_texto = {o["job_url"]: o for o in ofertas}
    perdidos = [v for v in avisos.values()
                if (buscador.extraer_anos_experiencia((con_texto.get(v["url"]) or {}).get("description")) or 0) > 1]
    resultado = {
        "ventana": [corte[:10], final[:10]], "descubiertas": len(en_ventana), "encaje_alto": len(altas),
        "piden_mas_de_1": len(por_tope), "piden_1_o_menos": sum(1 for o in altas if o["_anos"] is not None and o["_anos"] <= 1),
        "no_dicen": sum(1 for o in altas if o["_anos"] is None),
        "pct_perdido": round(100 * len(por_tope) / max(1, len(altas)), 1),
        "bajan_de_5_por_su_nota": len(por_nota),
        "avisos_enviados": len(avisos), "avisos_que_pedian_mas_de_1": len(perdidos),
        "marcadas_entre_las_perdidas": {
            c: sum(1 for o in por_tope if o["job_url"] in marcas[c]) for c in ("favoritas", "aplicadas", "ocultas")},
        "con_nombres": sorted(
            ({"anos": o["_anos"], "nota": round(o["_nota"], 1), "titulo": o.get("title"), "empresa": o.get("company"),
              "visto": o["visto_por_primera_vez"][:10],
              "marca": next((c[:-1] for c in ("favoritas", "aplicadas", "ocultas") if o["job_url"] in marcas[c]), "")}
             for o in por_tope), key=lambda x: (-x["nota"], x["visto"])),
    }
    perfiles.cerrar()
    return resultado


# ----------------------------------------------------------------------
# Lo que trae Jooble
# ----------------------------------------------------------------------
def jooble(copia: Path) -> dict:
    carpeta = preparar_copia(copia)
    entorno_de(carpeta)
    logging.disable(logging.WARNING)
    import buscador
    import perfiles

    titular = perfiles.titular()
    uso = json.loads((copia / "jooble_uso.json").read_text(encoding="utf-8"))
    ofertas = [o for o in perfiles.ofertas_de(titular) if o.get("site") == "jooble"]
    otras = [o for o in perfiles.ofertas_de(titular)
             if "jooble" in (o.get("fuentes_adicionales") or []) and o.get("site") != "jooble"]
    avisos = [v for v in perfiles.avisos(titular).values() if v.get("fuente") == "jooble"]
    respuestas = [v.get("respuesta") for v in avisos]
    altas = [o for o in ofertas if buscador.explicar_relevancia(
        o.get("title"), o.get("location"), buscador.extraer_anos_experiencia(o.get("description")),
        o.get("description"))[0] >= 5]
    dias = max(1, len(uso.get("por_dia") or {}))
    perfiles.cerrar()
    peticiones = uso.get("peticiones") or 0
    return {
        "peticiones": peticiones, "dias": dias,
        "ofertas_guardadas": len(ofertas), "tambien_en_otra_fuente": len(otras), "encaje_alto": len(altas),
        "avisos": len(avisos), "respuestas": {r or "sin responder": respuestas.count(r) for r in set(respuestas)},
        "ofertas_por_peticion": round(len(ofertas) / max(1, peticiones), 2),
        "encaje_alto_por_peticion": round(len(altas) / max(1, peticiones), 3),
        "avisos_por_peticion": round(len(avisos) / max(1, peticiones), 3),
        "encaje_alto_por_dia_con_8": round(8 * len(altas) / max(1, peticiones), 2),
        "encaje_alto_por_perfil_y_dia_con_3_perfiles": round(8 / 3 * len(altas) / max(1, peticiones), 2),
    }


# ----------------------------------------------------------------------
# La carga: esto corre en un proceso por medida
# ----------------------------------------------------------------------
def una_medida(copia: Path, n: int) -> dict:
    carpeta = preparar_copia(copia)
    entorno_de(carpeta)
    os.environ["JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"] = str(ADICIONAL)
    logging.disable(logging.ERROR)
    esperar, time.sleep = time.sleep, lambda *_: None

    import pandas as pd
    import requests

    class Resp:
        status_code, ok, text = 200, True, "{}"

        def json(self):
            return {"ok": True, "result": True}

    enviados = []
    requests.post = lambda url, json=None, timeout=None, headers=None: enviados.append(json) or Resp()

    import almacen  # noqa: F401
    import app
    import buscador
    import config as config_mod
    import perfiles

    titular = perfiles.titular()
    os.environ["JOBSPY_TELEGRAM_CHAT_ID"] = str(titular)
    with gzip.open(copia / "descripciones_podadas.json.gz", "rt", encoding="utf-8") as f:
        podadas = json.load(f)

    class SinRed:
        def __init__(self, *a, **k):
            pass

        def geocode(self, *a, **k):
            return None

    buscador.Nominatim = SinRed
    buscador.FUENTES_PROPIAS.update({f: (lambda **k: None) for f in buscador.FUENTES_PROPIAS})
    buscador.FUENTES_CON_CORRIDA.clear()

    columnas = ("site", "job_url", "title", "company", "location", "date_posted", "job_type", "interval",
                "min_amount", "max_amount", "currency", "is_remote", "job_level", "company_industry")
    todas = perfiles.ofertas_de(titular)
    final = max(str(o.get("visto_por_ultima_vez") or "") for o in todas)  # las viejas no lo traen
    ventana = (datetime.fromisoformat(final) - timedelta(hours=38)).isoformat(timespec="seconds")
    excluidas = perfiles.urls_excluidas(titular)
    visibles = [o for o in todas if str(o.get("visto_por_ultima_vez") or "") >= ventana and o["job_url"] not in excluidas]
    ocultas = [o for o in todas if o["job_url"] in excluidas and str(o.get("date_posted") or "") >= ventana[:10]]
    usadas = {o["job_url"] for o in visibles + ocultas}
    resto = [o for o in todas if o["job_url"] not in usadas][:max(0, 322 - len(usadas))]
    lejos = [{**o, "location": "Madrid, Community of Madrid, Spain", "is_remote": False} for o in resto]
    textos = {}
    filas = []
    for o in visibles + ocultas + lejos:
        filas.append({c: o.get(c) for c in columnas})
        textos[o["job_url"]] = o.get("description") or podadas.get(o["job_url"])

    cfg = config_mod.cargar_config()
    con_adicional = n >= 2
    if con_adicional:
        # Desde MP5, como entraría hoy con la plantilla: el .env ya no crea perfiles.
        perfiles.guardar(ADICIONAL, perfiles.datos_de_plantilla(), "activo")
    for i in range(3, n + 1):
        plan = json.loads((AQUI / "fixtures" / OFICIOS[i]).read_text(encoding="utf-8"))
        plan["terminos"] = [f"{t} {i}" for t in plan["terminos"]]  # que no se dedupliquen entre sí
        perfiles.guardar(-1000 - i, {"v": 1, "reglas": "catalogo", "plan": plan, "avisos": {"modo": "ninguno"}}, "activo")

    plan = buscador.planificar_consultas(perfiles.activos(), cfg)
    donde = {c.clave: i for i, c in enumerate(plan)}
    # Cada oferta la traen dos consultas, como en real, que el mismo anuncio sale en varios términos.
    por_consulta: dict[int, list] = {}
    propias = [c for c in plan if titular in c.perfiles]
    for k, fila in enumerate(filas):
        for salto in (0, 7):
            por_consulta.setdefault((k + salto) % len(propias), []).append(fila)
    posicion = {donde[c.clave]: i for i, c in enumerate(propias)}
    de_otros = {}
    for c in plan:
        if titular not in c.perfiles:
            termino = c.busqueda["params"]["search_term"]
            de_otros[donde[c.clave]] = [
                {**{k: None for k in columnas}, "site": "linkedin", "job_url": f"https://www.linkedin.com/jobs/view/{donde[c.clave]}{j}",
                 "title": f"Mozo de almacén {termino} {j}", "company": f"Logística {donde[c.clave]} {j}",
                 "location": c.busqueda["params"].get("location"), "date_posted": final[:10]} for j in range(20)]
            for fila in de_otros[donde[c.clave]]:
                textos[fila["job_url"]] = "Buscamos mozo de almacén con carretilla para turno de mañana. " * 6

    def scrape(**k):
        i = donde[buscador._clave_de_consulta(k_completo(k))]
        lista = por_consulta.get(posicion[i], []) if i in posicion else de_otros.get(i, [])
        return pd.DataFrame(lista) if lista else pd.DataFrame()

    sitios_por_clave = {}
    for c in plan:
        params = c.busqueda["params"]
        sitios_por_clave[(params["search_term"], params.get("location"), bool(params.get("is_remote")))] = params

    def k_completo(k):
        return sitios_por_clave[(k["search_term"], k.get("location"), bool(k.get("is_remote")))]

    def descripciones(df, config, progreso=None, conocidas=None, competencia_conocida=None):
        if df.empty:
            return df
        df = df.copy()
        conocidas = conocidas or {}
        df["description"] = [conocidas.get(u) or textos.get(u) for u in df["job_url"]]
        return df

    buscador.scrape_jobs = scrape
    buscador.completar_descripciones = descripciones

    def corrida() -> dict:
        # Las marcas van al segundo: dos corridas en el mismo segundo no cambian `visto_ultima`, y
        # entonces la escritura sale casi a cero. Con 0,3 s por corrida pasa, y en real hay una hora.
        esperar(1.1)
        perfiles.conexion().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        gc.collect()
        inicio = time.perf_counter()
        app.BUSQUEDA.update(activa=False, error=None)
        app._ejecutar_busqueda(config_mod.cargar_config(), False, True)
        duracion = time.perf_counter() - inicio
        if app.BUSQUEDA["error"]:
            raise RuntimeError(app.BUSQUEDA["error"])
        wal = Path(str(perfiles.ruta()) + "-wal").stat().st_size
        return {"s": duracion, "wal": wal}

    primera = corrida()
    regimen = [corrida() for _ in range(CORRIDAS_DE_REGIMEN)]
    gc.collect()
    rss, pico = memoria()
    conn = perfiles.conexion()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    compartidas = conn.execute(
        "SELECT count(*) FROM (SELECT url FROM ofertas_perfil GROUP BY url HAVING count(*) > 1)").fetchone()[0]
    ahorro = conn.execute(
        "SELECT coalesce(sum(length(CAST(o.datos AS BLOB)) + coalesce(length(CAST(d.texto AS BLOB)), 0)), 0)"
        " FROM ofertas o LEFT JOIN descripciones d ON d.url = o.url"
        " WHERE o.url IN (SELECT url FROM ofertas_perfil GROUP BY url HAVING count(*) > 1)").fetchone()[0]
    enlaces = conn.execute("SELECT count(*) FROM ofertas_perfil").fetchone()[0]
    resultado = {
        "n": n, "filas_simuladas": len(filas), "visibles": len(visibles), "ocultas_en_ventana": len(ocultas),
        "consultas_unicas": len(plan), "primera_s": primera["s"], "primera_wal": primera["wal"],
        "regimen_s": [r["s"] for r in regimen], "regimen_wal": [r["wal"] for r in regimen],
        "rss": rss, "pico": pico, "compartidas": compartidas, "bytes_compartidos": ahorro, "enlaces": enlaces,
        "db_bytes": perfiles.ruta().stat().st_size,
        "del_adicional": perfiles.contar_ofertas(ADICIONAL) if con_adicional else 0,
        "mensajes": sum(1 for e in enviados if e and "text" in e),
    }
    perfiles.cerrar()
    return resultado


def calibrar() -> dict:
    """Lo que tarda langdetect aquí, para traer las cifras al Atom: allí son 60,43 ms."""
    from langdetect import DetectorFactory, detect_langs

    DetectorFactory.seed = 0
    return detect_langs


def carga(copia: Path) -> dict:
    filas = {}
    for n in (1, 2, 3, 5):
        medidas = []
        for _ in range(REPETICIONES):
            proceso = subprocess.run([sys.executable, __file__, str(copia), "_una", str(n)], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace")
            if proceso.returncode:
                raise SystemExit(proceso.stderr[-2000:])
            medidas.append(json.loads(proceso.stdout.strip().splitlines()[-1]))
        def mediana(clave):
            return statistics.median(m[clave] for m in medidas)

        filas[n] = {
            "consultas_unicas": medidas[0]["consultas_unicas"], "filas": medidas[0]["filas_simuladas"],
            "visibles": medidas[0]["visibles"], "ocultas_en_ventana": medidas[0]["ocultas_en_ventana"],
            "primera_s": round(mediana("primera_s"), 2),
            "regimen_s": round(statistics.median(s for m in medidas for s in m["regimen_s"]), 2),
            "primera_kb": round(mediana("primera_wal") / 1024),
            "regimen_kb": round(statistics.median(w for m in medidas for w in m["regimen_wal"]) / 1024),
            "rss": round(mediana("rss"), 1), "pico": round(mediana("pico"), 1),
            "compartidas": medidas[0]["compartidas"], "bytes_compartidos": medidas[0]["bytes_compartidos"],
            "enlaces": medidas[0]["enlaces"], "del_adicional": medidas[0]["del_adicional"],
            "db_mb": round(medidas[0]["db_bytes"] / 2**20, 2), "mensajes": medidas[0]["mensajes"],
        }
    return filas


def langdetect_aqui(copia: Path) -> float:
    detect_langs = calibrar()
    # El caché es un SQLite desde el 11-11; las copias de antes traen el JSON.
    if (copia / "descripciones_vistas.db").exists():
        conn = sqlite3.connect(f"file:{(copia / 'descripciones_vistas.db').as_posix()}?mode=ro", uri=True)
        textos = [t[:2000] for (t,) in conn.execute("SELECT texto FROM textos ORDER BY url LIMIT 200")]
        conn.close()
    else:
        datos = json.loads((copia / "descripciones_vistas.json").read_text(encoding="utf-8"))
        textos = [e["d"][:2000] for e in list(datos.values())[:200] if isinstance(e, dict) and e.get("d")]
    inicio = time.perf_counter()
    for texto in textos:
        try:
            detect_langs(texto)
        except Exception:
            pass
    return 1000 * (time.perf_counter() - inicio) / max(1, len(textos))


def main() -> int:
    copia = Path(sys.argv[1])
    que = sys.argv[2] if len(sys.argv) > 2 else "todo"
    if que == "_una":
        print(json.dumps(una_medida(copia, int(sys.argv[3]))))
        return 0
    resultado = {}
    if que == "perdidas":
        resultado["perdidas"] = perdidas(copia)
    if que == "jooble":
        resultado["jooble"] = jooble(copia)
    if que == "carga":
        resultado["carga"] = carga(copia)
        resultado["langdetect_ms_en_el_pc"] = round(langdetect_aqui(copia), 2)
    print(json.dumps(resultado, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
