# -*- coding: utf-8 -*-
"""
Pruebas de la corrida de arranque (MP8, paso 14 de la encuesta): lo que pasa entre «Confirmar» y la
calibración, con la corrida de verdad (`app._ejecutar_arranque`, `buscador.buscar_arranque`).

Ninguna sale a la red: `scrape_jobs`, las descripciones, Telegram y Jooble son espías, y los chat_id y el
token, inventados. Lo que se comprueba es lo que el arranque promete:

- solo hace consultas SUYAS, como mucho 8, y no pide nada a Jooble (su cupo es del sistema);
- no toca lo de los demás: ni su lista, ni las filas de `consultas` con las que rota la corrida;
- la calibración y los avisos son de su lista y de nadie más, y nunca más de 5 avisos;
- su primera corrida normal también va limitada a 5, y la siguiente ya no;
- si se reinicia con el arranque en cola, se retoma; y si falla, se dice y la corrida normal lo recoge.

    python pruebas/test_arranque.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B = 91000001, 92000002, 93000003
DATOS = Path(tempfile.mkdtemp(prefix="test-arranque-"))
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR),
                  JOBSPY_JOOBLE_KEY="clave-de-prueba")
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
os.environ.pop("JOBSPY_JOOBLE_LIMITE", None)

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import almacen  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import reglas  # noqa: E402
import telegram_botones as tb  # noqa: E402
import telegram_encuesta as enc  # noqa: E402
from fuentes_extra import jooble as jo  # noqa: E402
from fuentes_extra import tecnoempleo as te  # noqa: E402
from perfilado import generador  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, cuerpo, contenido=b""):
        self._cuerpo, self.content, self.status_code, self.ok = cuerpo, contenido, 200, True
        self.text, self.url = json.dumps(cuerpo), ""

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        pass


class Red:
    def __init__(self):
        self.mundo: list[dict] = []
        self.consultas: list[dict] = []
        self.telegram: list[dict] = []
        self.jooble: list[dict] = []
        self.fallar = False
        self.mensaje_id = 1000

    def post(self, url, json=None, timeout=None, headers=None, data=None, files=None):  # noqa: A002
        if "api.telegram.org" in url:
            self.telegram.append({"metodo": url.rsplit("/", 1)[-1], **(json or {})})
            self.mensaje_id += 1
            return Resp({"ok": True, "result": {"message_id": self.mensaje_id}})
        self.jooble.append(json)
        return Resp({"totalCount": 0, "jobs": []})

    def get(self, url, **k):
        return Resp({}, b"<rss></rss>")

    def scrape(self, **k):
        self.consultas.append(k)
        if self.fallar:
            raise RuntimeError("la red de mentira se ha caído")
        filas = [{c: v for c, v in f.items() if c != "_en"} for f in self.mundo
                 if (k["search_term"], k.get("location")) in f["_en"] or "*" in f["_en"]]
        return pd.DataFrame(filas) if filas else pd.DataFrame()

    def descripciones(self, df, config, progreso=None, conocidas=None, competencia_conocida=None):
        if df.empty:
            return df
        df = df.copy()
        df["description"] = df["description"].astype(object)
        for i, url in zip(df.index, df["job_url"]):
            if not isinstance(df.at[i, "description"], str):
                df.at[i, "description"] = (conocidas or {}).get(url) or texto(url)
        return df

    def a(self, chat) -> list[dict]:
        return [m for m in self.telegram if m["metodo"] == "sendMessage" and str(m.get("chat_id")) == str(chat)]

    def textos_a(self, chat) -> str:
        return "\n".join(m["text"] for m in self.a(chat))


class SinRed:
    def __init__(self, *a, **k):
        pass

    def geocode(self, *a, **k):
        return None


red = Red()
requests.post = red.post
requests.get = red.get
buscador.scrape_jobs = red.scrape
buscador.completar_descripciones = red.descripciones
buscador.Nominatim = SinRed
buscador.as_completed = lambda futuros: futuros
buscador.FUENTES_PROPIAS.update(infojobs=lambda **k: None, eures=lambda **k: None)
buscador.FUENTES_CON_CORRIDA.pop("infojobs", None)
time.sleep = lambda *_: None

VALENCIA = "Valencia, Spain"
_TAREAS = ("facturas", "archivo", "agenda", "correo", "pedidos", "albaranes", "llamadas", "contratos", "nóminas", "viajes",
           "reuniones", "informes", "cobros", "pagos", "inventario", "clientes")


def texto(url: str) -> str:
    """Una descripción en español distinta para cada oferta: con dos casi iguales, el dedupe las da por la misma."""
    tareas = " y ".join(_TAREAS[int(c, 16)] for c in hashlib.sha1(url.encode()).hexdigest())
    return f"Buscamos una persona para ocuparse de {tareas} en nuestra oficina."



def oferta(n: int, titulo: str, ubicacion=VALENCIA, en=("*",)) -> dict:
    url = f"https://www.linkedin.com/jobs/view/{n}"
    empresa = hashlib.sha1(f"empresa {n}".encode()).hexdigest()[:12]
    return {"site": "linkedin", "job_url": url, "title": titulo, "company": empresa, "location": ubicacion,
            "date_posted": "2026-11-05", "job_type": None, "interval": None, "min_amount": None, "max_amount": None,
            "currency": None, "is_remote": False, "job_level": None, "company_industry": None, "description": None,
            "_en": list(en)}


def config_de_prueba() -> dict:
    cfg = copy.deepcopy(config_mod.CONFIG_POR_DEFECTO)
    cfg.update(copy.deepcopy(reglas.TITULAR_2026_10.config))
    cfg.update(hours_old=38, results_wanted=20, busquedas_en_paralelo=1, busqueda_automatica_activa=True,
               busqueda_automatica_horas=1, max_perfiles=3)
    return cfg


def preparar() -> None:
    perfiles.cerrar()
    perfiles._preparadas.clear()
    perfiles._CACHE_EFECTIVA.clear()
    almacen._HUELLAS.clear()
    te._ULTIMA_DESCARGA["cuando"] = None
    for fichero in DATOS.iterdir():
        shutil.rmtree(fichero) if fichero.is_dir() else fichero.unlink()
    config_mod.guardar_config(config_de_prueba())
    perfiles.guardar(TITULAR, {"v": 1, "reglas": "titular-2026-10", "config": "global"}, "activo")
    red.__init__()
    app._arranques.clear()


def datos_de(chat_id, puestos, ciudad="Valencia", lat=39.47, lon=-0.38, **extra) -> dict:
    r = {"puestos": puestos, "experiencia": "ninguna", "practicas": False,
         "ubicacion": {"nombre": ciudad, "lat": lat, "lon": lon, "radio_km": 25},
         "modalidad": ["presencial", "remoto"], "remoto_alcance": "es", "idiomas": ["es"], **extra}
    plan = generador.generar_plan(r)
    return {"v": 1, "reglas": "catalogo", **r, "sectores": sorted({p.split(".")[0] for p in puestos}), "plan": plan,
            "avisos": {"modo": "instantaneo", "descubrimiento": True}, "arranque": {"estado": "pendiente"}}


def consultas_en_la_base() -> dict:
    return {f[0]: f[1] for f in perfiles.conexion().execute("SELECT clave, ultima FROM consultas")}


def corrida(automatica=True) -> None:
    app.BUSQUEDA.update(activa=False, error=None)
    app._ejecutar_busqueda(config_mod.cargar_config(), False, automatica)
    if app.BUSQUEDA["error"]:
        print("    error de la corrida:", app.BUSQUEDA["error"])


def tipos(chat) -> dict:
    cuenta = {}
    for (t,) in perfiles.conexion().execute("SELECT tipo FROM avisos WHERE chat_id = ?", (chat,)):
        cuenta[t] = cuenta.get(t, 0) + 1
    return cuenta


LECTURAS_DEL_CATALOGO: list[int] = []
_cargar_catalogo = generador.cargar_catalogo


def _cargar_contando():
    LECTURAS_DEL_CATALOGO.append(1)
    return _cargar_catalogo()


generador.cargar_catalogo = _cargar_contando


def caso_solo_lo_suyo() -> None:
    print("\n[1] El arranque hace solo consultas suyas (≤ 8), sin Jooble y sin tocar lo de los demás")
    preparar()
    red.mundo = [oferta(i, t) for i, t in enumerate(
        ["Administrativo/a contable", "Auxiliar administrativo/a", "Administrativo de compras", "Administrativo/a de RRHH",
         "Auxiliar administrativo (Valencia)", "Administrativo/a comercial", "Recepcionista de oficina", "Asistente de dirección",
         "Contable junior", "Camarero/a", "Operario"], 1)]
    # El titular tiene su historia: una corrida normal antes, con su lista y sus consultas apuntadas.
    red.mundo.append(oferta(900, "Técnico/a Helpdesk N1 Junior", ubicacion="Barcelona, Spain",
                            en=[(t, "Barcelona, Spain") for t in ("helpdesk", "soporte técnico")]))
    corrida()
    lista_titular = set(perfiles.posiciones(TITULAR))
    consultas_antes = consultas_en_la_base()
    comprueba(len(consultas_antes) > 0 and all(u for u in consultas_antes.values()),
              "el titular ya tiene sus consultas apuntadas (%d)" % len(consultas_antes))

    datos = datos_de(A, ["admin.administrativo", "admin.auxiliar", "admin.backoffice"], modalidad=["presencial", "remoto"])
    perfiles.guardar(A, datos, "activo")
    propias = buscador.planificar_consultas([perfiles.obtener(A)], config_mod.cargar_config())
    comprueba(len(propias) > buscador.CONSULTAS_DE_ARRANQUE, "A tiene más consultas (%d) que el tope del arranque (%d)" % (
        len(propias), buscador.CONSULTAS_DE_ARRANQUE))
    claves_propias = {c.clave for c in propias}
    # Que a Jooble le TOQUE descargar (han pasado las 12 h): aun así, el arranque no le pide nada.
    uso = jo._leer_uso()
    uso.pop("ultima_descarga", None)
    jo._escribir_uso(uso)
    antes_jooble = len(red.jooble)
    red.consultas.clear()
    red.telegram.clear()
    LECTURAS_DEL_CATALOGO.clear()
    app._ejecutar_arranque(A)
    hechas_ = red.consultas
    terminos_propios = set(perfiles.obtener(A)["datos"]["plan"]["terminos"])
    comprueba(0 < len(hechas_) <= buscador.CONSULTAS_DE_ARRANQUE, "hace %d consultas, como mucho %d" % (len(hechas_), buscador.CONSULTAS_DE_ARRANQUE))
    comprueba(all(k["search_term"] in terminos_propios for k in hechas_), "   todas con términos de A (ninguno del titular)")
    comprueba(len(red.jooble) == antes_jooble and jo._toca_descargar(),
              "no pide nada a Jooble aunque le toque descargar: su cupo y su reloj de 12 h son del sistema")
    despues = consultas_en_la_base()
    comprueba(set(consultas_antes) <= set(despues) and all(despues[k] == consultas_antes[k] for k in consultas_antes),
              "las filas del titular en `consultas` siguen todas, con su misma fecha (anotar_consultas no borra lo de los demás)")
    marcadas = {k for k in claves_propias if despues.get(k)}
    comprueba(0 < len(marcadas) <= buscador.CONSULTAS_DE_ARRANQUE, "   y las de A hechas quedan apuntadas (%d)" % len(marcadas))
    comprueba(set(perfiles.posiciones(TITULAR)) == lista_titular, "la lista del titular no ha cambiado")
    comprueba(len(perfiles.posiciones(A)) >= 1, "la de A tiene ofertas (%d)" % len(perfiles.posiciones(A)))
    comprueba(red.textos_a(TITULAR) == "", "al titular no le llega nada del arranque de A")
    datos_a = perfiles.obtener(A)["datos"]
    comprueba(datos_a["arranque"] == {"estado": "hecho"} and datos_a["primera_pendiente"] is True, "A queda con el arranque hecho y su primera tanda limitada")
    cal = datos_a.get("calibracion") or {}
    comprueba(len(cal.get("mejores", [])) == 3 and len(cal.get("borde", [])) == 2 and len(cal.get("baja", [])) == 1,
              "la calibración tiene 3 mejores, 2 del borde y 1 baja")
    n = tipos(A)
    comprueba(n.get("calibracion") == 6 and n.get("aviso", 0) + 3 <= 5, "6 de calibración y, con ellas, como mucho 5 de las mejores (%s)" % n)
    comprueba(all(str(m["chat_id"]) == str(A) for m in red.telegram if m["metodo"] == "sendMessage"), "todo lo que se envía va a A")

    corrida()
    comprueba(not LECTURAS_DEL_CATALOGO and enc._MENU["firma"] is None,
              "ni el arranque ni una corrida normal parsean el catálogo ni construyen el menú de la encuesta (MP7: la RAM va justa)")
    # Segundo arranque: no repite
    red.consultas.clear()
    red.telegram.clear()
    app._ejecutar_arranque(A)
    comprueba(not red.consultas and not red.telegram, "un arranque que ya está hecho no se repite")


def caso_titular_sin_consultas_hechas() -> None:
    print("\n[1b] Aunque las consultas del titular no se hayan hecho nunca, el arranque no las toca")
    preparar()
    perfiles.guardar(A, datos_de(A, ["admin.administrativo", "admin.auxiliar"]), "activo")
    red.mundo = [oferta(i, "Administrativo/a de %s" % t) for i, t in enumerate("compras ventas logística calidad".split(), 1)]
    app._ejecutar_arranque(A)
    terminos_a = set(perfiles.obtener(A)["datos"]["plan"]["terminos"])
    terminos_titular = {c.busqueda["params"]["search_term"] for c in
                        buscador.planificar_consultas([perfiles.obtener(TITULAR) or perfiles.PERFIL_GLOBAL], config_mod.cargar_config())}
    comprueba(red.consultas and all(k["search_term"] in terminos_a for k in red.consultas) and not (terminos_a & terminos_titular),
              "las %d consultas del arranque son de A, aunque las del titular (la rotación les daría prioridad) estén sin hacer" % len(red.consultas))
    comprueba(consultas_en_la_base() and sum(1 for u in consultas_en_la_base().values() if u) <= buscador.CONSULTAS_DE_ARRANQUE,
              "   y solo quedan como hechas las de A")


def caso_primera_corrida_normal() -> None:
    print("\n[2] Su primera corrida normal también va limitada a 5, y la siguiente ya no")
    preparar()
    corrida()  # el titular ya había rotado sus consultas: las que nunca se han hecho (las de A) van las primeras
    perfiles.guardar(A, datos_de(A, ["admin.administrativo", "admin.auxiliar"]), "activo")
    # El arranque ve solo 8 consultas; en la corrida normal llegan muchas ofertas más.
    red.mundo = [oferta(i, "Administrativo/a de %s" % t) for i, t in enumerate(
        "compras ventas logística calidad finanzas legal obras viajes seguros banca pedidos facturas nóminas archivo".split(), 1)]
    app._ejecutar_arranque(A)
    comprueba(perfiles.obtener(A)["datos"].get("primera_pendiente") is True, "tras el arranque queda la marca de «primera tanda»")
    red.mundo += [oferta(100 + i, "Administrativo/a de %s" % t) for i, t in enumerate(
        "proyectos tesorería subvenciones exportación aduanas flota almacén producción obra ventas internacionales "
        "patrimonio concursos expedientes licitaciones".split(), 1)]
    red.telegram.clear()
    corrida()
    avisos = [m for m in red.a(A) if "Encaje" in m.get("text", "") or "🎯" in m.get("text", "")]
    resumen = [m for m in red.a(A) if "Y " in m.get("text", "") and "más de Encaje alto" in m.get("text", "")]
    comprueba(len(perfiles.posiciones(A)) > 14, "la corrida normal le trae las ofertas de sus otras consultas (%d en su lista)" % len(perfiles.posiciones(A)))
    comprueba(0 < len(avisos) <= 5 and not resumen, "la primera corrida normal manda como mucho 5 y sin resumen (%d)" % len(avisos))
    comprueba(not perfiles.obtener(A)["datos"].get("primera_pendiente"), "   y la marca se quita al acabar")
    red.mundo += [oferta(200 + i, "Auxiliar administrativo/a de %s" % t) for i, t in enumerate(
        "hostelería farmacia academia clínica gestoría notaría taller imprenta".split(), 1)]
    red.telegram.clear()
    corrida()
    avisos2 = [m for m in red.a(A) if "🎯" in m.get("text", "")]
    comprueba(len(avisos2) == 8, "la siguiente ya es normal: sin el tope de 5 (%d avisos de 8 nuevas)" % len(avisos2))


def caso_marca_sin_nada() -> None:
    print("\n[2b] Una corrida en la que no le llega nada no gasta su «primera tanda»")
    preparar()
    perfiles.guardar(A, {**datos_de(A, ["admin.administrativo"]), "arranque": {"estado": "hecho"}, "primera_pendiente": True}, "activo")
    # La rotación puede dejar a un perfil sin consultas en una corrida concreta (hay más que presupuesto): se
    # comprueba directamente con lo que `_ejecutar_busqueda` le entrega a `_notificar_perfiles`.
    app._notificar_perfiles([(perfiles.obtener(A), {"ofertas_nuevas": []}, True)], True)
    comprueba(perfiles.obtener(A)["datos"].get("primera_pendiente") is True, "sin ofertas nuevas, la marca sigue puesta")
    llegada = {k: v for k, v in oferta(1, "Administrativo/a de compras").items() if k != "_en"}
    app._notificar_perfiles([(perfiles.obtener(A), {"ofertas_nuevas": [llegada]}, True)], True)
    comprueba(not perfiles.obtener(A)["datos"].get("primera_pendiente"), "en cuanto le llega algo, se quita")
    version = perfiles.obtener(A)["version"]
    app._notificar_perfiles([(perfiles.obtener(A), {"ofertas_nuevas": [llegada]}, False)], True)
    comprueba(perfiles.obtener(A)["version"] == version, "sin la marca no se escribe nada (cada escritura cuenta)")


def caso_recuperar_y_fallar() -> None:
    print("\n[3] Recuperar tras un reinicio, no duplicar, y qué pasa si falla")
    preparar()
    perfiles.guardar(A, datos_de(A, ["admin.administrativo"]), "activo")
    perfiles.guardar(B, {**datos_de(B, ["admin.auxiliar"]), "arranque": {"estado": "hecho"}}, "activo")
    while not app._cola.empty():
        app._cola.get_nowait()
    comprueba(app.recuperar_arranques() == 1, "al arrancar se retoma el arranque pendiente de A, y solo ese")
    comprueba(app.iniciar_arranque(A) is False and app._cola.qsize() == 1, "   y pedirlo otra vez no lo duplica en la cola")
    nombre, trabajo = app._cola.get_nowait()
    comprueba(nombre == "arranque", "   está en la cola del trabajador, detrás de lo que haya")
    app._arranques.clear()
    # Un perfil que ya no está activo no arranca
    perfiles.cambiar_estado(A, "pausado")
    red.consultas.clear()
    app._ejecutar_arranque(A)
    comprueba(not red.consultas, "un perfil pausado no hace el arranque")
    perfiles.cambiar_estado(A, "activo")
    # Falla la red: se dice, se anota y la corrida normal lo recoge
    real = buscador.buscar_arranque

    def revienta(*a, **k):
        raise RuntimeError("fallo de mentira")

    buscador.buscar_arranque = revienta
    red.telegram.clear()
    app._arranques.add(A)
    app._ejecutar_arranque(A)
    buscador.buscar_arranque = real
    d = perfiles.obtener(A)["datos"]
    comprueba(d["arranque"] == {"estado": "fallido"} and d.get("primera_pendiente") is True,
              "si el arranque falla queda «fallido» y su primera corrida normal irá limitada")
    comprueba("No he podido hacer la primera búsqueda" in red.textos_a(A), "   se le dice, con lo que pasará (cada hora)")
    comprueba(app._arranques == set(), "   y no queda marcado como en marcha")
    # Con la red caída (cada consulta falla) no es un fallo del arranque: es «no he encontrado nada», y se dice
    perfiles.actualizar_datos(A, lambda d: d.update(arranque={"estado": "pendiente"}))
    red.fallar = True
    red.telegram.clear()
    app._ejecutar_arranque(A)
    red.fallar = False
    comprueba(perfiles.obtener(A)["datos"]["arranque"] == {"estado": "hecho"} and "no he encontrado nada" in red.textos_a(A),
              "con la red caída, cada consulta falla por su cuenta: «no he encontrado nada», y sigue buscando cada hora")


def main() -> int:
    for caso in (caso_solo_lo_suyo, caso_titular_sin_consultas_hechas, caso_primera_corrida_normal, caso_marca_sin_nada, caso_recuperar_y_fallar):
        try:
            caso()
        except Exception as e:  # una excepción es un fallo, no una prueba que no corre
            import traceback

            traceback.print_exc()
            comprueba(False, "%s ha reventado: %r" % (caso.__name__, e))
    perfiles.cerrar()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  -", f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
