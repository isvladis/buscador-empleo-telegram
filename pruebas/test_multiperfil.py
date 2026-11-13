# -*- coding: utf-8 -*-
"""
Pruebas de MP3: una corrida para varios perfiles, y que nada de uno llegue a otro.

Ninguna sale a la red: `scrape_jobs`, las descripciones, Telegram, Jooble y Tecnoempleo son
espías, y los chat_id y el token son inventados. Los perfiles se llaman TITULAR y ADICIONAL como
los de verdad, pero no escriben a nadie.

La equivalencia del titular solo está al final: lanza `pruebas/conductor_titular.py` sobre el
árbol de ahora y sobre el de antes de MP3 (`git archive`), y compara lo que hacen byte a byte.

Los controles negativos que requieren romper el código van en `pruebas/mutaciones_mp3.py`, que
copia el árbol, aplica cada mutación y comprueba que esta prueba falla. Aquí quedan los que se
pueden hacer sin tocar nada: el dato que se busca sí estaba en la base, y aun así no se alcanza.

    python pruebas/test_multiperfil.py
"""

from __future__ import annotations

import copy
import hashlib
import gzip
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ))

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, ADICIONAL, TERCERO, SOMBRA = 91000001, 92000002, 93000003, -1009
COMMIT_DE_ANTES = "d9ede5e"
DATOS = Path(tempfile.mkdtemp(prefix="test-multiperfil-"))
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN,
                  JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR),
                  JOBSPY_TELEGRAM_CHAT_IDS_EXTRA=str(ADICIONAL), JOBSPY_JOOBLE_KEY="clave-de-prueba")
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
from fuentes_extra import jooble as jo  # noqa: E402
from fuentes_extra import salud  # noqa: E402
from fuentes_extra import tecnoempleo as te  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


# ----------------------------------------------------------------------
# La red de mentira
# ----------------------------------------------------------------------
class Resp:
    def __init__(self, cuerpo, contenido=b""):
        self._cuerpo, self.content, self.status_code, self.ok = cuerpo, contenido, 200, True
        self.text, self.url = json.dumps(cuerpo), ""

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        pass


class Red:
    """Lo que el sistema le pide al mundo, y lo que el mundo le contesta."""

    def __init__(self):
        self.mundo: list[dict] = []  # filas, cada una con `_en`: [(término, location)] que la traen
        self.textos: dict[str, str] = {}
        self.consultas: list[dict] = []
        self.descargas: list[str] = []
        self.telegram: list[dict] = []
        self.jooble: list[dict] = []
        self.feeds = 0
        self.ofertas_jooble: dict[str, dict] = {}  # {keywords: respuesta}
        self.feed = b"<rss></rss>"

    def post(self, url, json=None, timeout=None, headers=None):  # noqa: A002
        if "api.telegram.org" in url:
            self.telegram.append({"metodo": url.rsplit("/", 1)[-1], **(json or {})})
            return Resp({"ok": True, "result": True})
        self.jooble.append(json)
        return Resp(self.ofertas_jooble.get(json["keywords"], {"totalCount": 0, "jobs": []}))

    def get(self, url, **k):
        self.feeds += 1
        return Resp({}, self.feed)

    def scrape(self, **k):
        self.consultas.append(k)
        filas = [{c: v for c, v in f.items() if c != "_en"} for f in self.mundo
                 if (k["search_term"], k.get("location")) in f["_en"]]
        return pd.DataFrame(filas) if filas else pd.DataFrame()

    def descripciones(self, df, config, progreso=None, conocidas=None, competencia_conocida=None):
        if df.empty:
            return df
        df = df.copy()
        conocidas = conocidas or {}
        df["description"] = df["description"].astype(object)
        for i, url in zip(df.index, df["job_url"]):
            if isinstance(df.at[i, "description"], str):
                continue
            if url in conocidas:
                df.at[i, "description"] = conocidas[url]
            elif url in self.textos:
                self.descargas.append(url)
                df.at[i, "description"] = self.textos[url]
        return df

    def enviados(self, chat) -> list[dict]:
        return [m for m in self.telegram if m["metodo"] == "sendMessage" and str(m["chat_id"]) == str(chat)]

    def textos_a(self, chat) -> str:
        return "\n".join(m["text"] for m in self.enviados(chat))

    def limpiar_registro(self):
        self.consultas.clear()
        self.descargas.clear()
        self.telegram.clear()
        self.jooble.clear()


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
esperar = time.sleep
time.sleep = lambda *_: None

BARCELONA = "Barcelona, Spain"
_TAREAS = ("redes", "impresoras", "correo", "sistemas", "usuarios", "licencias", "equipos", "servidores",
           "portátiles", "móviles", "telefonía", "accesos", "copias", "inventario", "manuales", "contraseñas")


def texto(n: int, anos: int | None = None) -> str:
    """
    Una descripción en español distinta para cada oferta. Tiene que serlo: con dos casi iguales, el
    dedupe las da por la misma vacante aunque la empresa sea otra, y hace bien.
    """
    tareas = " y ".join(_TAREAS[int(c, 16)] for c in hashlib.sha1(str(n).encode()).hexdigest())
    pide = f" Se requieren {anos} años de experiencia en soporte." if anos else ""
    return f"Buscamos una persona en Barcelona para atender a los usuarios con {tareas}.{pide}"


def oferta(n: int, titulo="Técnico/a Helpdesk N1 Junior", empresa=None, anos=None, sitio="linkedin",
           en=(("helpdesk", BARCELONA),), ubicacion=BARCELONA, fecha="2026-10-29") -> dict:
    url = f"https://www.{sitio}.com/jobs/view/{n}"
    red.textos[url] = texto(n, anos)
    # Empresa también distinta: «Empresa 1» y «Empresa 2» se parecen más que el umbral del dedupe.
    empresa = empresa or hashlib.sha1(f"empresa {n}".encode()).hexdigest()[:12]
    return {"site": sitio, "job_url": url, "title": titulo, "company": empresa,
            "location": ubicacion, "date_posted": fecha, "job_type": None, "interval": None,
            "min_amount": None, "max_amount": None, "currency": None, "is_remote": False,
            "job_level": None, "company_industry": None, "description": None, "_en": list(en)}


def config_de_prueba(**extra) -> dict:
    cfg = copy.deepcopy(config_mod.CONFIG_POR_DEFECTO)
    cfg.update(copy.deepcopy(reglas.TITULAR_2026_10.config))
    cfg.update(hours_old=38, results_wanted=20, busquedas_en_paralelo=1, busqueda_automatica_activa=True,
               busqueda_automatica_horas=1)
    cfg.update(extra)
    return cfg


def limpiar(**extra) -> None:
    """Una carpeta de datos vacía y el titular con su fila, como tras la migración."""
    perfiles.cerrar()
    perfiles._preparadas.clear()
    perfiles._CACHE_EFECTIVA.clear()
    almacen._HUELLAS.clear()
    te._ULTIMA_DESCARGA["cuando"] = None
    for fichero in DATOS.iterdir():
        shutil.rmtree(fichero) if fichero.is_dir() else fichero.unlink()
    config_mod.guardar_config(config_de_prueba(**extra))
    perfiles.guardar(TITULAR, {"v": 1, "reglas": "titular-2026-10", "config": "global"}, "activo")
    red.__init__()


def con_adicional() -> None:
    # Desde MP5 las altas son por invitación y JOBSPY_TELEGRAM_CHAT_IDS_EXTRA ya no crea nada: el
    # adicional entra como entraría hoy alguien con la plantilla, que es lo que él tenía (D8).
    if perfiles.obtener(ADICIONAL) is None:
        perfiles.guardar(ADICIONAL, perfiles.datos_de_plantilla(), "activo")


def corrida(automatica=True, reemplazar=False) -> None:
    app.BUSQUEDA.update(activa=False, error=None)
    app._ejecutar_busqueda(config_mod.cargar_config(), reemplazar, automatica)
    if app.BUSQUEDA["error"]:
        print("    error de la corrida:", app.BUSQUEDA["error"])


def urls_de(chat) -> set[str]:
    return set(perfiles.posiciones(chat))


def pulsacion(datos, chat, n=1):
    return {"update_id": n, "callback_query": {"id": f"cb{n}", "data": datos,
                                               "message": {"message_id": n, "chat": {"id": chat}}}}


@contextmanager
def log_capturado():
    lineas: list[str] = []

    class Recoge(logging.Handler):
        def emit(self, registro):
            lineas.append(registro.getMessage())

    raiz, manejador = logging.getLogger(), Recoge(level=logging.DEBUG)
    nivel = raiz.level
    raiz.addHandler(manejador)
    raiz.setLevel(logging.INFO)
    try:
        yield lineas
    finally:
        raiz.removeHandler(manejador)
        raiz.setLevel(nivel)


def perfil_con_terminos(*terminos: str, etiqueta="Propia") -> dict:
    """Las mismas reglas que el titular y otras consultas: lo que pase, pasa por venir de ellas."""
    bloque = copy.deepcopy(reglas.TITULAR_2026_10.config)
    bloque["categorias"] = {"propia": {"etiqueta": etiqueta, "activa": True, "terminos": list(terminos)}}
    return {"v": 1, "reglas": "titular-2026-10", "config": bloque}


def de_catalogo(fichero: str, **cambios) -> dict:
    plan = json.loads((AQUI / "fixtures" / fichero).read_text(encoding="utf-8"))
    plan.update(cambios)
    return {"v": 1, "reglas": "catalogo", "plan": plan}


# ----------------------------------------------------------------------
# Los casos
# ----------------------------------------------------------------------
def caso_el_perfil_del_adicional() -> None:
    print("\n[1] El perfil del adicional nace bien")
    limpiar()
    cfg = config_mod.cargar_config()
    comprueba(cfg["telegram_bot_token"] == TOKEN, "   (control: la config que se copia SÍ lleva el token)")
    datos = perfiles.datos_de_plantilla()
    crudo = json.dumps(datos, ensure_ascii=False)
    comprueba(TOKEN not in crudo and str(TITULAR) not in crudo and str(ADICIONAL) not in crudo,
              "ni el token ni ningún chat_id van a parar a su perfil")
    comprueba(set(datos["config"]) <= set(reglas.CLAVES_DE_LA_PERSONA), "solo lleva claves de la persona")
    efectiva = perfiles.config_efectiva({"chat_id": ADICIONAL, "version": 1, "datos": datos}, cfg)
    comprueba(efectiva["anos_experiencia_max"] == 1 and efectiva["experiencia_estricta"] is True,
              "tope de 1 año y estricto")
    # La trampa: el juego trae el 3 del titular, y si el bloque no dijera el suyo ganaría ese.
    sin_el_arreglo = copy.deepcopy(datos)
    del sin_el_arreglo["config"]["anos_experiencia_max"]
    sin_el_arreglo["experiencia_max_exigida"] = None
    comprueba(perfiles.config_efectiva({"chat_id": 7, "version": 1, "datos": sin_el_arreglo}, cfg)
              ["anos_experiencia_max"] == 3, "   (control: sin el tope en el bloque se quedaría en el 3 del juego)")
    comprueba(efectiva["usar_modelo_entrenado"] is False, "no hereda el modelo entrenado del titular")

    print("\n    Desde MP5, el .env ya no crea perfiles")
    app._comprobar_almacen()
    comprueba(perfiles.obtener(ADICIONAL) is None, "arrancar con JOBSPY_TELEGRAM_CHAT_IDS_EXTRA puesto no crea nada")
    con_adicional()
    comprueba(perfiles.obtener(ADICIONAL)["estado"] == "activo", "con la plantilla, activo")
    comprueba(perfiles.contar_ofertas(ADICIONAL) == 0, "su lista empieza vacía")
    otra = config_de_prueba(anos_experiencia_max=9, categorias={"x": {"etiqueta": "X", "activa": True, "terminos": ["ciber"]}})
    config_mod.guardar_config(otra)
    cfg = config_mod.cargar_config()
    perfil = perfiles.obtener(ADICIONAL)
    efectiva = perfiles.config_efectiva(perfil, cfg)
    comprueba(efectiva["anos_experiencia_max"] == 1 and "x" not in efectiva["categorias"],
              "lo que cambie luego en config.json no le afecta (MP4)")
    comprueba(red.telegram == [], "crear el perfil no le manda nada")

    print("\n    Y borrarlo es para siempre")
    with redirect_stdout(io.StringIO()):
        codigo = perfiles.main(["borrar", str(ADICIONAL)])
    comprueba(codigo == 2 and perfiles.obtener(ADICIONAL) is not None, "una persona no se borra sin --confirmar")
    with redirect_stdout(io.StringIO()) as salida:
        codigo = perfiles.main(["borrar", str(ADICIONAL), "--confirmar"])
    comprueba(codigo == 0 and perfiles.obtener(ADICIONAL) is None, "con --confirmar, sí")
    comprueba(str(ADICIONAL) not in salida.getvalue(), "el comando no escribe el chat_id")
    comprueba(any((DATOS / "borrados").iterdir()), "y deja una copia antes de borrar (regla 7)")
    app._comprobar_almacen()
    comprueba(perfiles.obtener(ADICIONAL) is None, "reiniciar no lo resucita aunque siga en el .env")
    try:
        perfiles.borrar(TITULAR)
        comprueba(False, "el titular no se puede borrar")
    except ValueError:
        comprueba(True, "el titular no se puede borrar")


def caso_titular_mas_adicional() -> None:
    print("\n[2] Titular + adicional: la misma carga, y cada uno lo suyo")
    limpiar()
    descartada = oferta(9)
    almacen.marcar(descartada["job_url"], "oculta", {"title": descartada["title"]}, chat_id=TITULAR)
    red.mundo = [descartada]
    corrida()
    solas = len(red.consultas)
    comprueba(solas == 33, "el titular solo hace las 33 de siempre (%d)" % solas)
    comprueba(not red.descargas, "y lo que ya descartó no se vuelve a descargar")
    limpiar()
    con_adicional()
    compartida = oferta(1, anos=1)
    dos_anos = oferta(2, anos=2)
    sin_decir = oferta(3)
    red.mundo = [compartida, dos_anos, sin_decir]
    corrida()
    comprueba(len(red.consultas) == 33, "con el adicional, también 33 (%d)" % len(red.consultas))
    comprueba(len({json.dumps(k, sort_keys=True) for k in red.consultas}) == 33, "ninguna repetida")
    comprueba(len(red.descargas) == len(set(red.descargas)) == 3, "cada descripción se baja una vez (%d)" % len(red.descargas))
    comprueba(urls_de(TITULAR) == {compartida["job_url"], dos_anos["job_url"], sin_decir["job_url"]},
              "el titular tiene las tres")
    comprueba(urls_de(ADICIONAL) == {compartida["job_url"], sin_decir["job_url"]},
              "el adicional no tiene la que pide 2 años; la que no dice los años, sí")
    comprueba(dos_anos["job_url"] not in red.textos_a(ADICIONAL) and dos_anos["job_url"] in red.textos_a(TITULAR),
              "   y no se le avisa")
    conn = perfiles.conexion()
    url = compartida["job_url"]
    comprueba(conn.execute("SELECT count(*) FROM ofertas WHERE url = ?", (url,)).fetchone()[0] == 1
              and conn.execute("SELECT count(*) FROM descripciones WHERE url = ?", (url,)).fetchone()[0] == 1
              and conn.execute("SELECT count(*) FROM ofertas_perfil WHERE url = ?", (url,)).fetchone()[0] == 2,
              "la que encaja en los dos: una fila y dos enlaces")
    vistas = conn.execute("SELECT visto_ultima FROM ofertas_perfil WHERE url = ?", (url,)).fetchall()
    comprueba(len(vistas) == 2 and vistas[0] == vistas[1] and vistas[0][0] == conn.execute(
        "SELECT visto_ultima FROM ofertas WHERE url = ?", (url,)).fetchone()[0],
        "una sola marca por corrida: el segundo no reescribe la fila compartida")
    original, recibidas = almacen.fusionar_ofertas, []

    def espia_fusion(*a, **k):
        recibidas.append(k.get("ahora"))
        return original(*a, **k)

    almacen.fusionar_ofertas = espia_fusion
    try:
        corrida()
    finally:
        almacen.fusionar_ofertas = original
    # Lo de arriba solo lo vería si dos perfiles cayeran en segundos distintos: aquí se mira la causa.
    comprueba(len(recibidas) == 2 and recibidas[0] and recibidas[0] == recibidas[1],
              "   y es la misma la que reciben los dos, no una por perfil")

    print("\n    Lo que hace uno no lo nota el otro")
    almacen.marcar(url, "oculta", {"title": compartida["title"]}, chat_id=TITULAR)
    comprueba(url in perfiles.urls_excluidas(TITULAR) and url not in perfiles.urls_excluidas(ADICIONAL),
              "ocultada por uno, el otro no la tiene excluida")
    red.limpiar_registro()
    corrida()
    comprueba(url in urls_de(ADICIONAL), "y sigue en su lista tras otra corrida")
    comprueba(url in perfiles.ofertas_por_url(ADICIONAL, [url]), "   con su registro")
    comprueba(perfiles.descripcion_de(ADICIONAL, url), "   y su descripción")

    print("\n    Lo que exporta el titular es solo suyo (lo que antes enseñaba la web)")
    solo_suya = oferta(4, titulo="Técnico Helpdesk Solo Del Adicional")
    almacen.marcar(solo_suya["job_url"], "oculta", {"title": solo_suya["title"]}, chat_id=TITULAR)
    red.mundo.append(solo_suya)
    corrida()
    comprueba(solo_suya["job_url"] in urls_de(ADICIONAL), "   (control: esa oferta está en la base, enlazada al adicional)")
    cuerpo = json.loads(gzip.decompress(perfiles.exportar_comprimido(TITULAR)))
    comprueba(solo_suya["job_url"] not in {o["job_url"] for o in cuerpo["ofertas"]}, "/misdatos del titular no lleva la del adicional")
    comprueba(len(cuerpo["ofertas"]) == perfiles.contar_ofertas(TITULAR), "solo las del titular")
    comprueba(set(cuerpo["marcas"]["ocultas"]) == set(perfiles.marcas(TITULAR)["ocultas"]), "y sus marcas")


def caso_consultas_de_cada_uno() -> None:
    print("\n[2b] Lo que trae la consulta de uno no es del otro")
    limpiar(max_busquedas_por_corrida=40)  # las suyas van después de las 33 del titular
    perfiles.guardar(ADICIONAL, perfil_con_terminos("soporte propio"), "activo")
    del_titular = oferta(1)
    del_adicional = oferta(2, titulo="Técnico Helpdesk N1", en=[("soporte propio", BARCELONA)])
    red.mundo = [del_titular, del_adicional]
    corrida()
    comprueba(any(k["search_term"] == "soporte propio" for k in red.consultas), "   (control: su consulta se ha hecho)")
    comprueba(urls_de(TITULAR) == {del_titular["job_url"]}, "el titular solo tiene lo de sus consultas")
    comprueba(urls_de(ADICIONAL) == {del_adicional["job_url"]},
              "el otro también, aunque con las mismas reglas le pasarían los filtros las dos")
    fila = perfiles.ofertas_por_url(ADICIONAL, [del_adicional["job_url"]]).get(del_adicional["job_url"], {})
    comprueba(fila.get("categoria") == "propia", "   y con su categoría")

    print("\n    El idioma de un mismo texto se detecta una vez")
    limpiar()
    con_adicional()
    red.mundo = [oferta(3), oferta(4, titulo="Técnico Helpdesk N2 Junior")]
    import langdetect

    original, llamadas = langdetect.detect_langs, []
    langdetect.detect_langs = lambda texto: llamadas.append(texto) or original(texto)
    buscador._idiomas_de.cache_clear()
    try:
        corrida()
        corrida()
    finally:
        langdetect.detect_langs = original
    comprueba(len(llamadas) == len(set(llamadas)) == 2,
              "dos ofertas, dos perfiles y dos corridas: 2 detecciones (%d)" % len(llamadas))


def caso_dedupe_y_poda() -> None:
    print("\n[3] El dedupe y la poda de uno no le quitan nada al otro")
    limpiar()
    con_adicional()
    # Una ubicación escrita distinto: si fuera igual, la copia exacta ya caería antes de llegar aquí.
    indeed = oferta(2, titulo="Técnico Helpdesk N1", empresa="Duplica SL", sitio="indeed",
                    ubicacion="Barcelona, Catalonia, Spain")
    linkedin = oferta(1, titulo="Técnico Helpdesk N1", empresa="Duplica SL")
    red.mundo = [indeed]
    corrida()
    comprueba(indeed["job_url"] in urls_de(TITULAR) and indeed["job_url"] in urls_de(ADICIONAL),
              "   (la de Indeed la tienen los dos)")
    # Sale en LinkedIn la misma vacante. El titular la tiene oculta, así que para él no se compara
    # con nada y se queda con la de Indeed; el adicional las junta.
    almacen.marcar(linkedin["job_url"], "oculta", {"title": linkedin["title"]}, chat_id=TITULAR)
    red.mundo = [indeed, linkedin]
    corrida()
    comprueba(urls_de(ADICIONAL) == {linkedin["job_url"]}, "el dedupe del adicional deja una: le quita su enlace a la de Indeed")
    comprueba(indeed["job_url"] in urls_de(TITULAR), "el titular conserva la de Indeed")
    conn = perfiles.conexion()
    comprueba(conn.execute("SELECT count(*) FROM ofertas WHERE url = ?", (indeed["job_url"],)).fetchone()[0] == 1,
              "la que su dedupe quitó sigue en `ofertas`, porque otro la enlaza")

    print("\n    La descripción se poda cuando los dos la tienen oculta")
    limpiar()
    con_adicional()
    comun = oferta(3)
    red.mundo = [comun]
    corrida()
    url = comun["job_url"]
    comprueba(url in urls_de(TITULAR) and url in urls_de(ADICIONAL), "   (la tienen los dos)")
    viejo = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    conn = perfiles.conexion()

    def ocultar_hace_tiempo(chat):
        almacen.marcar(url, "oculta", {"title": comun["title"]}, chat_id=chat)
        conn.execute("UPDATE marcas SET fecha = ? WHERE chat_id = ? AND url = ?", (viejo, chat, url))

    ocultar_hace_tiempo(TITULAR)
    comprueba(almacen.podar_descripciones_ocultas(TITULAR) == 0 and perfiles.descripcion_de(ADICIONAL, url),
              "oculta solo por uno, el texto se queda")
    ocultar_hace_tiempo(ADICIONAL)
    comprueba(almacen.podar_descripciones_ocultas(ADICIONAL) == 1 and not perfiles.descripcion_de(ADICIONAL, url),
              "oculta por los dos, se poda")

    print("\n    Una descarga fallida no le quita el texto a otro")
    limpiar()
    con_adicional()
    comun = oferta(4)
    red.mundo = [comun]
    corrida()
    url = comun["job_url"]
    registro = perfiles.ofertas_por_url(ADICIONAL, [url])[url]
    almacen.fusionar_ofertas([{**registro, "description": None}], chat_id=ADICIONAL)
    comprueba(perfiles.descripcion_de(TITULAR, url), "actualizarla sin texto desde un perfil no la deja sin texto")
    perfiles.quitar_enlace(TITULAR, url)
    almacen.fusionar_ofertas([{**registro, "description": None}], chat_id=ADICIONAL)
    comprueba(not perfiles.descripcion_de(ADICIONAL, url), "   (solo, sí: con un perfil se comporta como siempre)")

    print("\n    Borrar un perfil")
    limpiar()
    con_adicional()
    comun, solo_suya = oferta(5), oferta(6)
    almacen.marcar(solo_suya["job_url"], "oculta", {"title": solo_suya["title"]}, chat_id=TITULAR)
    red.mundo = [comun, solo_suya]
    corrida()
    conn = perfiles.conexion()
    conn.execute("DELETE FROM marcas WHERE chat_id = ?", (TITULAR,))  # que no la proteja nada
    comprueba(urls_de(ADICIONAL) == {comun["job_url"], solo_suya["job_url"]}, "   (el adicional tiene las dos)")
    comprueba(perfiles.avisos(ADICIONAL) and perfiles.marcas(ADICIONAL) is not None, "   (y sus avisos)")
    perfiles.config_efectiva(perfiles.obtener(ADICIONAL), config_mod.cargar_config())
    quitado = perfiles.borrar(ADICIONAL)
    comprueba(quitado["enlaces"] == 2 and not perfiles.avisos(ADICIONAL), "quita sus enlaces y sus avisos")
    comprueba(comun["job_url"] in urls_de(TITULAR), "lo compartido se queda para el otro")
    comprueba(conn.execute("SELECT count(*) FROM ofertas WHERE url = ?", (solo_suya["job_url"],)).fetchone()[0] == 0
              and conn.execute("SELECT count(*) FROM descripciones WHERE url = ?", (solo_suya["job_url"],)).fetchone()[0] == 0,
              "lo que solo enlazaba él desaparece, con su texto")
    comprueba(ADICIONAL not in perfiles._CACHE_EFECTIVA, "y su config no se queda en memoria")


def caso_avisos_y_botones() -> None:
    print("\n[4] Cada chat recibe lo suyo, y sus botones solo valen en él")
    limpiar()
    con_adicional()
    comun = oferta(1)
    solo_titular = oferta(2, titulo="Técnico Helpdesk Solo Del Titular", anos=2)
    solo_adicional = oferta(3, titulo="Técnico Helpdesk Solo Del Adicional")
    almacen.marcar(solo_adicional["job_url"], "oculta", {"title": solo_adicional["title"]}, chat_id=TITULAR)
    red.mundo = [comun, solo_titular, solo_adicional]
    with log_capturado() as log:
        corrida()
    al_titular, al_adicional = red.textos_a(TITULAR), red.textos_a(ADICIONAL)
    comprueba(comun["job_url"] in al_titular and comun["job_url"] in al_adicional, "la común, a los dos")
    comprueba(solo_titular["job_url"] in al_titular and solo_titular["job_url"] not in al_adicional,
              "la del titular, solo al titular")
    comprueba(solo_adicional["job_url"] in al_adicional and solo_adicional["job_url"] not in al_titular,
              "la del adicional, solo al adicional")
    comprueba(all("reply_markup" in m for m in red.enviados(ADICIONAL)), "el adicional también lleva botones")
    comprueba(set(perfiles.avisos(ADICIONAL)) == {tb.clave_de(comun["job_url"]), tb.clave_de(solo_adicional["job_url"])},
              "sus avisos van a SU índice")
    otros = {tb.clave_de(comun["job_url"]), tb.clave_de(solo_titular["job_url"])}
    comprueba(set(perfiles.avisos(TITULAR)) == otros, "y los del titular, al suyo")

    print("\n    Botones")
    cfg = config_mod.cargar_config()
    clave_titular = tb.clave_de(solo_titular["job_url"])
    with log_capturado() as log_botones:
        pulsada = tb.procesar(pulsacion("f:" + clave_titular, ADICIONAL), cfg)
    comprueba(pulsada == "desconocida", "una pulsación del adicional sobre un aviso del titular no encuentra nada")
    comprueba(not perfiles.marcas(ADICIONAL)["favoritas"] and not perfiles.marcas(TITULAR)["favoritas"],
              "   y no marca nada en ninguna base")
    comprueba(clave_titular in perfiles.avisos(TITULAR), "   (control: ese aviso existe, y es del titular)")
    respuestas = [m["text"] for m in red.telegram if m["metodo"] == "answerCallbackQuery"]
    comprueba(tb.procesar(pulsacion("f:zzzzzzzzzzzz", ADICIONAL), cfg) == "desconocida"
              and [m["text"] for m in red.telegram if m["metodo"] == "answerCallbackQuery"][-1] == respuestas[-1],
              "   y contesta lo mismo que a una clave inventada: no se deduce que otro lo tenga")
    clave_comun = tb.clave_de(comun["job_url"])
    comprueba(tb.procesar(pulsacion("o:" + clave_comun, ADICIONAL), cfg) == "oculta", "sobre su propio aviso, sí")
    comprueba(comun["job_url"] in perfiles.marcas(ADICIONAL)["ocultas"]
              and comun["job_url"] not in perfiles.marcas(TITULAR)["ocultas"], "   y marca en SU base, no en la del titular")
    comprueba((perfiles.avisos(ADICIONAL).get(clave_comun) or {}).get("respuesta") == "oculta"
              and "respuesta" not in (perfiles.avisos(TITULAR).get(clave_comun) or {}), "   y responde en SU aviso")
    comprueba(tb.procesar(pulsacion("f:" + clave_comun, TITULAR), cfg) == "favorito"
              and comun["job_url"] in perfiles.marcas(TITULAR)["favoritas"], "los del titular siguen funcionando")
    comprueba(tb.procesar(pulsacion("d:" + clave_comun, ADICIONAL), cfg) == "deshacer"
              and comun["job_url"] not in perfiles.marcas(ADICIONAL)["ocultas"]
              and comun["job_url"] in perfiles.marcas(TITULAR)["favoritas"], "deshacer solo deshace lo suyo")
    comprueba(tb.procesar(pulsacion("f:" + clave_comun, 94000004), cfg) == "rechazada", "un chat sin perfil se rechaza")
    with log_capturado() as mas_log:
        tb.procesar(pulsacion("f:" + tb.clave_de(solo_adicional["job_url"]), ADICIONAL), cfg)
    todo = "\n".join(log + log_botones + mas_log)
    comprueba(str(TITULAR) not in todo and str(ADICIONAL) not in todo, "ningún chat_id en el log (%d líneas)" % len(log))
    comprueba(solo_adicional["title"] not in todo, "ni qué ofertas recibe o marca el adicional")

    print("\n    Las incidencias son del titular")
    salud.registrar_aviso_presupuesto("jooble", "Lleva 400 de 500")
    red.limpiar_registro()
    app._notificar_incidencias_de_fuentes()
    comprueba(red.enviados(TITULAR) and not red.enviados(ADICIONAL), "la operación no le llega al adicional")

    print("\n    Corridas manuales")
    limpiar()
    con_adicional()
    primera = oferta(4)
    red.mundo = [primera]
    corrida()
    red.limpiar_registro()
    nueva = oferta(5, titulo="Técnico Helpdesk Otra Vacante")
    red.mundo.append(nueva)
    corrida(automatica=False)
    comprueba(not red.enviados(TITULAR), "al titular no: está delante de la web")
    comprueba(nueva["job_url"] in red.textos_a(ADICIONAL), "al adicional sí: si no, esa oferta no la vería nunca")
    red.limpiar_registro()
    red.mundo = [nueva]
    corrida(automatica=False, reemplazar=True)
    comprueba(primera["job_url"] not in urls_de(TITULAR), "«Reemplazar» vacía la lista del titular")
    comprueba(primera["job_url"] in urls_de(ADICIONAL), "y no la del adicional")

    print("\n    No repetir aviso, por persona")
    limpiar()
    con_adicional()
    vacante = oferta(6, titulo="Técnico Helpdesk Repetido", empresa="Repite SL")
    otra_vez = oferta(7, titulo="Técnico Helpdesk Repetido", empresa="Repite SL", fecha="2026-10-20")
    for repetida in (vacante, otra_vez):  # el titular no las tiene: si sale el título, es del adicional
        almacen.marcar(repetida["job_url"], "oculta", {"title": repetida["title"]}, chat_id=TITULAR)
    red.mundo = [vacante]
    corrida()
    comprueba(vacante["job_url"] in red.textos_a(ADICIONAL), "   (se le avisa una vez)")
    red.limpiar_registro()
    red.mundo = [otra_vez]
    with log_capturado() as log:
        corrida()
    comprueba(otra_vez["job_url"] in urls_de(ADICIONAL) and not red.enviados(ADICIONAL),
              "la misma vacante con otro enlace ya no se avisa, tampoco a él")
    comprueba(vacante["title"] not in "\n".join(log), "   y el log no dice cuál (regla 5)")
    perfiles.conexion().execute("DELETE FROM ofertas_perfil WHERE chat_id = ?", (ADICIONAL,))
    red.limpiar_registro()
    tercera = oferta(8, titulo="Técnico Helpdesk Repetido", empresa="Repite SL", fecha="2026-10-10")
    red.mundo = [tercera]
    perfiles.registrar_aviso(ADICIONAL, "marca", "https://x.test/1", titulo="otra", vacante="otra|cosa")
    corrida()
    comprueba(tercera["job_url"] in urls_de(ADICIONAL) and not red.enviados(ADICIONAL),
              "sin nada en su lista, lo para la tabla `avisos`")


def caso_primera_corrida() -> None:
    print("\n[5] La primera corrida no es una avalancha")
    limpiar()
    con_adicional()
    red.mundo = [oferta(100 + i, titulo=f"Técnico Helpdesk N1 Junior Equipo {i}") for i in range(14)]
    corrida()
    comprueba(len(red.enviados(ADICIONAL)) == 5, "como mucho 5 avisos, y sin resumen (%d)" % len(red.enviados(ADICIONAL)))
    comprueba(len(red.enviados(TITULAR)) == 11, "al titular, los 10 y el resumen de siempre (%d)" % len(red.enviados(TITULAR)))
    comprueba(perfiles.contar_ofertas(ADICIONAL) == 14, "las 14 quedan como vistas en su lista")
    comprueba(len(perfiles.avisos(ADICIONAL)) == 5, "y solo 5 cuentan como avisadas")
    red.limpiar_registro()
    corrida()
    comprueba(not red.enviados(ADICIONAL), "la siguiente no vuelve a mandar las otras 9")
    red.limpiar_registro()
    red.mundo = [oferta(200 + i, titulo=f"Técnico Helpdesk N1 Junior Turno {i}") for i in range(7)]
    corrida()
    comprueba(len(red.enviados(ADICIONAL)) == 7, "después, ya sin ese tope (%d)" % len(red.enviados(ADICIONAL)))


def caso_pausa() -> None:
    print("\n[6] Pausa, encuesta, bloqueado: ni consultas ni avisos")
    for estado in ("pausado", "bloqueado", "pendiente", "encuesta"):
        limpiar()
        perfiles.guardar(TERCERO, de_catalogo("plan_mozo_zaragoza.json"), estado)
        filas = [oferta(1, titulo="Mozo de almacén", en=[("mozo de almacén", "Zaragoza, Spain")], ubicacion="Zaragoza, Spain")]
        red.mundo = filas
        corrida()
        comprueba(len(red.consultas) == 33 and not any(k["search_term"] == "mozo de almacén" for k in red.consultas),
                  f"{estado}: sus consultas no se hacen")
        comprueba(not red.enviados(TERCERO) and not urls_de(TERCERO), f"{estado}: ni avisos ni enlaces")
    limpiar(max_busquedas_por_corrida=40)  # sin subirlo, las suyas esperarían a la siguiente vuelta
    perfiles.guardar(TERCERO, de_catalogo("plan_mozo_zaragoza.json"), "activo")
    red.mundo = filas
    corrida()
    comprueba(any(k["search_term"] == "mozo de almacén" for k in red.consultas), "   (control: activo, sí se hacen)")
    comprueba(filas[0]["job_url"] in urls_de(TERCERO), "   y la oferta le llega")
    perfiles.guardar(TITULAR, perfiles.obtener(TITULAR)["datos"], "pausado")
    red.limpiar_registro()
    corrida()
    comprueba(app.BUSQUEDA["error"] is None and not any(k["search_term"] == "helpdesk" for k in red.consultas),
              "el titular también se puede pausar, y la corrida sigue para los demás")


def caso_tres_sectores() -> None:
    print("\n[7] Tres perfiles de sectores distintos")
    limpiar()
    perfiles.guardar(ADICIONAL, de_catalogo("plan_administrativo_valencia.json"), "activo")
    perfiles.guardar(TERCERO, de_catalogo("plan_mozo_zaragoza.json", terminos=[
        "MOZO DE  almacén", "operario de almacén", "preparador de pedidos"]), "activo")
    perfiles.guardar(SOMBRA, {**de_catalogo("plan_mozo_zaragoza.json"), "avisos": {"modo": "ninguno"}}, "activo")
    cfg = config_mod.cargar_config()
    activos = perfiles.activos()
    comprueba(activos[0]["chat_id"] == TITULAR, "el titular va el primero")
    plan = buscador.planificar_consultas(activos, cfg)
    claves = [c.clave for c in plan]
    comprueba(len(claves) == len(set(claves)), "sin claves repetidas")
    mozo = [c for c in plan if "mozo" in c.clave]
    comprueba(len(mozo) == 1 and set(mozo[0].perfiles) == {TERCERO, SOMBRA},
              "«MOZO DE  almacén» y «mozo de almacén» son la misma consulta, de los dos")
    suma = sum(len(buscador.planificar_consultas([p], cfg)) for p in activos)
    comprueba(len(plan) < suma, "deduplicadas: %d de %d" % (len(plan), suma))
    comprueba(all(set(c.etiquetas) == set(c.perfiles) for c in plan), "cada consulta sabe de quién es")

    print("\n    La rotación")
    tope = (len(plan) + 4) // 5
    config_mod.guardar_config(config_de_prueba(max_busquedas_por_corrida=tope))
    cfg = config_mod.cargar_config()
    tandas: list[set[str]] = []
    ultimas: dict = {}
    for _ in range(5):
        esperar(1.05)  # `ultima` va al segundo, y en real entre dos corridas hay una hora
        corrida()
        ahora = {c: v for c, v in perfiles.ultimas_consultas().items() if not c.startswith("jooble|")}
        tandas.append({c for c, v in ahora.items() if v != ultimas.get(c)})
        ultimas = ahora
    comprueba(all(len(t) <= tope for t in tandas), "ninguna corrida pasa del presupuesto (%d)" % tope)
    comprueba(set().union(*tandas) == set(claves), "5 corridas cubren las %d" % len(plan))
    comprueba(all(ultimas.get(c) for c in claves), "todas tienen su `ultima`")
    comprueba(all(tandas[i] == set(claves[i * tope:(i + 1) * tope]) for i in range(4)),
              "en orden de plan: lo nunca hecho, primero")
    # 39 de 8 en 8 son 40 huecos: el que sobra en la quinta no se pierde, repite la más antigua.
    comprueba(tandas[4] == set(claves[4 * tope:]) | set(claves[:5 * tope - len(claves)]),
              "y lo que sobra del presupuesto vuelve a la más antigua, sin repetir nada antes de cubrirlas todas")
    siguiente = buscador.consultas_de_la_corrida(activos, cfg)[1]
    mas_viejas = sorted(claves, key=lambda c: (ultimas.get(c) or "", claves.index(c)))[:tope]
    comprueba([c.clave for c in siguiente] == mas_viejas, "la siguiente tanda son las de `ultima` más antigua")

    print("\n    Si la rotación no llega, se avisa una vez al día")
    limpiar(max_busquedas_por_corrida=2, hours_old=4)
    corrida()
    corrida()
    avisos = [m for m in red.enviados(TITULAR) if "Operación del sistema" in m["text"]]
    comprueba(len(avisos) == 1, "33 consultas de 2 en 2 con 4 h de ventana: un aviso, no dos (%d)" % len(avisos))
    limpiar()
    corrida()
    comprueba(not [m for m in red.enviados(TITULAR) if "Operación del sistema" in m["text"]], "   (con la rotación de hoy, ninguno)")
    comprueba(buscador.periodo_de_rotacion(33, config_mod.cargar_config()) == 1.0, "hoy tarda 1 h")


def caso_jooble_y_tecnoempleo() -> None:
    print("\n[8] Jooble: el cupo se reparte y sus ofertas son de quien las pidió")
    limpiar()
    con_adicional()
    comun = {"title": "Técnico Helpdesk Jooble", "link": "https://es.jooble.org/jdp/1", "location": "Barcelona",
             "snippet": texto(901), "company": "Jooble SL", "source": "x", "updated": "2026-10-29T10:00:00"}
    red.ofertas_jooble = {"helpdesk": {"totalCount": 1, "jobs": [comun]}}
    corrida()
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    comprueba(len(red.jooble) == 4 and uso["peticiones"] == 4,
              "dos perfiles con los mismos términos: 4 peticiones, como con uno (%d)" % len(red.jooble))
    comprueba([c["keywords"] for c in red.jooble] == jo.TERMINOS and {c["location"] for c in red.jooble} == {"Barcelona"},
              "los términos de siempre, en el mismo orden")
    enlace = comun["link"]
    comprueba(enlace in urls_de(TITULAR) and enlace in urls_de(ADICIONAL), "y la oferta es de los dos")
    red.limpiar_registro()
    corrida()
    comprueba(not red.jooble, "cada 12 h, no en cada corrida")

    print("\n    Con términos distintos se turnan, sin gastar más")
    limpiar()
    con_adicional()
    plan = copy.deepcopy(reglas.TITULAR_2026_10.config["ambitos"]["barcelona_local"])
    plan.update(etiqueta="Valencia", location="Valencia, Spain", sitios=["jooble"], usar_distancia=False)
    datos = de_catalogo("plan_administrativo_valencia.json", ambitos={"local": plan},
                        jooble_terminos=["administrativo", "contable", "recepcionista"])
    perfiles.guardar(TERCERO, datos, "activo")
    suya = {**comun, "title": "Administrativo Contable", "link": "https://es.jooble.org/jdp/2", "location": "Valencia"}
    red.ofertas_jooble = {"helpdesk": {"totalCount": 1, "jobs": [comun]},
                          "administrativo": {"totalCount": 1, "jobs": [suya]},
                          "contable": {"totalCount": 1, "jobs": [suya]}}
    cuerpos = []
    for _ in range(3):
        corrida()
        cuerpos.append([(c["keywords"], c["location"]) for c in red.jooble])
        red.jooble.clear()
        uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
        uso["ultima_descarga"] = "2000-01-01T00:00:00+00:00"
        jo.USO_PATH.write_text(json.dumps(uso), encoding="utf-8")
    comprueba(all(len(c) == 4 for c in cuerpos), "cada tanda, 4 peticiones: el ritmo es del sistema, no de cada uno")
    pedidos = [p for c in cuerpos for p in c]
    comprueba(len(set(pedidos[:7])) == 7, "las 7 distintas se piden antes de repetir ninguna")
    comprueba(("administrativo", "Valencia") in pedidos, "cada término, con la ubicación de su perfil")
    comprueba(json.loads(jo.USO_PATH.read_text(encoding="utf-8"))["peticiones"] == 12, "12 en 3 tandas: el cupo, intacto")
    comprueba(suya["link"] in urls_de(TERCERO) and suya["link"] not in urls_de(TITULAR)
              and suya["link"] not in urls_de(ADICIONAL), "lo que trae su término es suyo")
    comprueba(comun["link"] not in urls_de(TERCERO), "y lo de los otros, no")
    fila = perfiles.ofertas_por_url(TERCERO, [suya["link"]]).get(suya["link"], {})
    comprueba(fila.get("busqueda", "").startswith("Mi búsqueda"), "y le llega con SUS etiquetas: %s" % fila.get("busqueda"))

    print("\n[9] Tecnoempleo: de quien lo tenga, con sus etiquetas")
    limpiar()
    fecha = format_datetime(datetime.now(timezone.utc))
    red.feed = ("<rss version='2.0'><channel><item><title>Técnico Helpdesk N1 Junior</title>"
                "<link>https://www.tecnoempleo.com/oferta/1</link><description><![CDATA["
                "<b>Empresa:</b>&nbsp;Tecno SL<br /><b>Provincia:</b>&nbsp;Barcelona<br />"
                f"<b>Descripción:</b>&nbsp;{texto(900)}]]></description><pubDate>{fecha}</pubDate>"
                "</item></channel></rss>").encode("utf-8")
    # Sin ninguna consulta en común con el titular: el feed cae en una de las de él, y aun así es suyo.
    perfiles.guardar(ADICIONAL, perfil_con_terminos("soporte propio", etiqueta="Otra etiqueta"), "activo")
    perfiles.guardar(TERCERO, de_catalogo("plan_mozo_zaragoza.json"), "activo")
    corrida()
    comprueba(red.feeds == 1, "un solo feed por corrida, con tres perfiles")
    url = "https://www.tecnoempleo.com/oferta/1"
    comprueba(url in urls_de(TITULAR) and url in urls_de(ADICIONAL),
              "los dos que lo tienen lo reciben, aunque no compartan ninguna consulta")
    comprueba(not urls_de(TERCERO), "el que no lo tiene, nada")
    suya = perfiles.ofertas_por_url(ADICIONAL, [url]).get(url, {}).get("busqueda", "")
    del_titular = perfiles.ofertas_por_url(TITULAR, [url]).get(url, {}).get("busqueda", "")
    comprueba("Otra etiqueta" in suya and del_titular and "Otra etiqueta" not in del_titular,
              "misma oferta, cada uno con su etiqueta: una no se cuela en la otra")


def caso_sombra_y_fallos() -> None:
    print("\n[10] El perfil sombra")
    limpiar(max_busquedas_por_corrida=40)  # sin subirlo, las suyas esperarían a la siguiente vuelta
    fichero = DATOS / "sombra.json"
    sombra = {"chat_id": SOMBRA, "datos": {**de_catalogo("plan_mozo_zaragoza.json"), "avisos": {"modo": "ninguno"}}}
    fichero.write_text(json.dumps(sombra), encoding="utf-8")
    cfg = config_mod.cargar_config()
    perfiles.crear_desde(fichero, cfg)
    comprueba(perfiles.obtener(SOMBRA)["estado"] == "activo", "crear --desde")
    uno = oferta(1, titulo="Mozo de almacén", en=[("mozo de almacén", "Zaragoza, Spain")], ubicacion="Zaragoza, Spain")
    red.mundo = [uno]
    corrida()
    comprueba(uno["job_url"] in urls_de(SOMBRA), "busca y guarda como cualquiera")
    comprueba(not red.enviados(SOMBRA), "y no recibe nada")
    with redirect_stdout(io.StringIO()):
        comprueba(perfiles.main(["borrar", str(SOMBRA)]) == 0 and perfiles.obtener(SOMBRA) is None,
                  "borrar <chat_id>: un chat_id negativo va sin --confirmar")

    def rechaza(contenido, que):
        fichero.write_text(json.dumps(contenido), encoding="utf-8")
        try:
            perfiles.crear_desde(fichero, cfg)
            comprueba(False, que)
        except (ValueError, TypeError, KeyError):
            comprueba(True, que)

    rechaza({"chat_id": SOMBRA, "datos": de_catalogo("plan_mozo_zaragoza.json")},
            "un chat_id negativo con avisos: rechazado, sería escribir a un grupo")
    rechaza({"chat_id": SOMBRA, "datos": {"reglas": "catalogo", "avisos": {"modo": "ninguno"}}},
            "un perfil sin plan: rechazado aquí y no en mitad de una corrida")
    rechaza({"chat_id": SOMBRA, "datos": {"reglas": "inventadas", "config": {}, "avisos": {"modo": "ninguno"}}},
            "un juego de reglas que no existe: rechazado")
    con_adicional()
    perfiles.guardar(TERCERO, de_catalogo("plan_mozo_zaragoza.json"), "activo")
    rechaza(sombra, "con 3 perfiles, un cuarto: rechazado (tope D4)")

    print("\n[11] Un perfil roto no le cuesta la corrida a los demás")
    limpiar()
    con_adicional()
    perfiles.guardar(TERCERO, {"v": 1, "reglas": "catalogo"}, "activo")  # sin plan
    buena = oferta(1)
    red.mundo = [buena]
    corrida()
    comprueba(app.BUSQUEDA["error"] is None, "la corrida termina")
    comprueba(buena["job_url"] in urls_de(TITULAR) and buena["job_url"] in urls_de(ADICIONAL), "y los demás reciben lo suyo")
    avisos = [m for m in red.enviados(TITULAR) if "Operación del sistema" in m["text"]]
    comprueba(len(avisos) == 1 and "3/3" in avisos[0]["text"], "y al titular se le avisa, por posición")
    comprueba(str(TERCERO) not in "\n".join(a["text"] for a in avisos), "   sin el chat_id")

    limpiar()
    con_adicional()
    red.mundo = [buena]
    original = almacen.fusionar_ofertas

    def falla_con_el_adicional(*a, **k):
        if k.get("chat_id") == ADICIONAL:
            raise RuntimeError("avería de prueba")
        return original(*a, **k)

    almacen.fusionar_ofertas = falla_con_el_adicional
    try:
        corrida()
    finally:
        almacen.fusionar_ofertas = original
    comprueba(app.BUSQUEDA["error"] is None and red.enviados(TITULAR), "si falla al guardar uno, el titular recibe sus avisos")

    def falla_con_el_titular(*a, **k):
        if k.get("chat_id") == TITULAR:
            raise RuntimeError("avería de prueba")
        return original(*a, **k)

    almacen.fusionar_ofertas = falla_con_el_titular
    try:
        corrida()
    finally:
        almacen.fusionar_ofertas = original
    comprueba(app.BUSQUEDA["error"] == "avería de prueba", "si falla con el titular, se ve como siempre")
    app.BUSQUEDA.update(activa=False, error=None)

    limpiar()
    red.mundo = [buena]
    anotar = perfiles.anotar_consultas

    def averia(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    perfiles.anotar_consultas = averia
    try:
        corrida()
    finally:
        perfiles.anotar_consultas = anotar
    comprueba(app.BUSQUEDA["error"] is None and buena["job_url"] in urls_de(TITULAR),
              "no poder apuntar las consultas no tira una corrida ya descargada")

    print("\n[12] Arrancar no le manda nada a nadie")
    limpiar()
    con_adicional()
    app._comprobar_almacen()
    comprueba(not red.telegram, "arrancar no manda nada")
    # `avisar-adicionales` (el mensaje del 01-11 al adicional) se fue con MP5: desde entonces la
    # bienvenida la da el propio /start de quien entra con una invitación (test_bot.py).
    with redirect_stdout(io.StringIO()):
        codigo = perfiles.main(["avisar-adicionales", "--confirmar"])
    comprueba(codigo == 2 and not red.telegram, "y la orden vieja ya no existe")


def caso_siempre_visibles() -> None:
    print("\n[13] Las categorías siempre visibles son de quien las tenga")
    limpiar()
    bloque = copy.deepcopy(reglas.TITULAR_2026_10.config)
    bloque.update(categorias_siempre_visibles=[], anos_experiencia_max=3)
    perfiles.guardar(ADICIONAL, {"v": 1, "reglas": "titular-2026-10", "config": bloque}, "activo")
    soc = oferta(1, titulo="Analista SOC Junior", empresa="Zeta Seguridad", en=[("junior soc", BARCELONA)])
    red.mundo = [soc]
    corrida()
    for chat in (TITULAR, ADICIONAL):
        almacen.marcar(soc["job_url"], "oculta", {"title": soc["title"]}, chat_id=chat)
    otra = oferta(2, titulo="Analista SOC Junior", empresa="Zeta Seguridad", en=[("junior soc", BARCELONA)])
    red.mundo = [otra]
    corrida()
    comprueba(otra["job_url"] in urls_de(TITULAR), "al titular, una republicación del SOC junior le vuelve a entrar")
    comprueba(otra["job_url"] not in urls_de(ADICIONAL), "a quien no la tiene en su juego, se le descarta como republicación")


def caso_equivalencia() -> None:
    print("\n[14] El titular solo: lo mismo que el código de antes, byte a byte")
    trabajo = Path(tempfile.mkdtemp(prefix="equivalencia-mp3-"))
    viejo = trabajo / "viejo"
    viejo.mkdir()
    archivo = subprocess.run(["git", "archive", COMMIT_DE_ANTES], cwd=RAIZ, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archivo)) as tar:
        tar.extractall(viejo, filter="data")

    def conducir(arbol: Path, modo: tuple, nombre: str) -> str:
        salida = trabajo / f"{nombre}.json"
        entorno = {k: v for k, v in os.environ.items() if not k.startswith("JOBSPY_")}
        proceso = subprocess.run([sys.executable, str(AQUI / "conductor_titular.py"), str(arbol), str(salida), *modo],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", env=entorno)
        if proceso.returncode:
            print(proceso.stderr[-1500:])
        comprueba(proceso.returncode == 0, f"el conductor corre: {nombre}")
        return salida.read_text(encoding="utf-8") if salida.exists() else ""

    def comparar(modo: tuple, que: str) -> dict:
        antes, ahora = (conducir(arbol, modo, f"{nombre}-{que}") for nombre, arbol in (("viejo", viejo), ("nuevo", RAIZ)))
        comprueba(antes and antes == ahora, f"{que}: idéntico al código de antes (%d B)" % len(antes))
        if antes != ahora and antes and ahora:
            viejo_d, nuevo_d = json.loads(antes), json.loads(ahora)
            print("    distintas:", [k for k in viejo_d if viejo_d[k] != nuevo_d.get(k)],
                  [k for k in viejo_d.get("base", {}) if viejo_d["base"][k] != nuevo_d["base"].get(k)])
        return json.loads(ahora or "{}")

    datos = comparar((), "solo el titular")
    comprueba(len(datos.get("consultas", ())) == 33 * 6, "6 corridas de 33 consultas")
    comprueba(sum(1 for m, _ in datos.get("telegram", ()) if m == "sendMessage") >= 8, "con avisos de verdad")
    comprueba(len(datos.get("descargas", ())) >= 7 and len(datos.get("jooble", ())) == 8, "descargas y Jooble")
    # Lo desplegado: con dos perfiles los pasos 2 a 4 de la corrida van por otro camino.
    datos = comparar(("con-adicional",), "con el adicional, lo del titular")
    comprueba(len(datos.get("consultas", ())) == 33 * 6, "   con las mismas 33 consultas")
    comprueba(len(datos.get("base", {}).get("avisos", ())) >= 5, "   y con avisos de verdad")
    shutil.rmtree(trabajo, ignore_errors=True)


def main() -> int:
    casos = [caso_el_perfil_del_adicional, caso_titular_mas_adicional, caso_consultas_de_cada_uno,
             caso_dedupe_y_poda, caso_avisos_y_botones, caso_primera_corrida, caso_pausa, caso_tres_sectores,
             caso_jooble_y_tecnoempleo, caso_sombra_y_fallos, caso_siempre_visibles]
    if not os.environ.get("MP3_SIN_EQUIVALENCIA"):  # mutaciones_mp3.py copia el árbol sin .git
        casos.append(caso_equivalencia)
    for caso in casos:
        # Un caso que revienta es un fallo más, y no deja sin correr a los siguientes.
        try:
            caso()
        except Exception as e:
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
