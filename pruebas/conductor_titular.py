# -*- coding: utf-8 -*-
"""
Conduce unas corridas del titular solo, sobre el árbol de código que se le diga, y escribe lo que
ha hecho el sistema: lo usa pruebas/test_multiperfil.py para enfrentar el código de ahora con el
de antes de MP3, extraído con `git archive`. Van en procesos separados porque los dos árboles
tienen los mismos nombres de módulo.

    python pruebas/conductor_titular.py <raíz-del-árbol> <salida.json> [con-adicional]

Toda la red es de mentira: `scrape_jobs`, las descripciones, Telegram, Jooble y Tecnoempleo son
espías, y solo se usa lo que existe en los dos árboles. Lo que se compara:

- qué se pide a los portales, en qué orden y con qué parámetros;
- qué descripciones se descargan;
- qué se manda a Telegram, botones incluidos, letra por letra;
- qué responde cada botón y qué marca;
- qué queda en perfiles.db y en lo que la web ve de cada corrida.

Con `con-adicional` corre lo que hay desplegado, un segundo chat en el .env, y solo se mira lo del
titular: en el código viejo ese chat recibe una copia y en el nuevo tiene su perfil, pero al titular
tiene que darle igual. Sin esto la equivalencia cubriría una configuración que no es la real: los
pasos 2 a 4 de la corrida van por otro camino cuando hay más de un perfil.

Las marcas de tiempo se sustituyen: dos procesos no corren en el mismo segundo. Las `as_completed`
van en orden de plan y con un solo obrero: en real el orden depende de la red, y eso ya no decidía
nada que se pudiera comparar.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

raiz, salida = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
CON_ADICIONAL = "con-adicional" in sys.argv[3:]
TITULAR = 555
sys.path.insert(0, str(raiz))
AQUI = Path(__file__).resolve().parent
sys.path = [p for p in sys.path if Path(p or ".").resolve() != AQUI]

datos = Path(tempfile.mkdtemp(prefix="conductor-"))
os.environ.update(JOBSPY_DATA_DIR=str(datos), JOBSPY_TELEGRAM_TOKEN="123:ABC",
                  JOBSPY_TELEGRAM_CHAT_ID="555", JOBSPY_JOOBLE_KEY="clave-de-prueba")
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
os.environ.pop("JOBSPY_JOOBLE_LIMITE", None)
if CON_ADICIONAL:
    os.environ["JOBSPY_TELEGRAM_CHAT_IDS_EXTRA"] = "777"

from reglas.titular_2026_10 import CONFIG_PERSONA  # noqa: E402

CONFIG = {k: v for k, v in CONFIG_PERSONA.items() if not k.startswith("jooble_")}
CONFIG.update(_config_version=2, hours_old=38, results_wanted=20, busquedas_en_paralelo=1,
              busqueda_automatica_activa=True, busqueda_automatica_horas=1,
              descripciones_dos_fases=True, linkedin_fetch_description=True)
(datos / "config.json").write_text(json.dumps(CONFIG, ensure_ascii=False), encoding="utf-8")

PLANTILLA = ("Buscamos una persona para el equipo de soporte en Barcelona: atención a usuarios, "
             "gestión de incidencias, tickets y resolución de problemas en el puesto de trabajo. "
             "Ofrecemos contrato estable, formación y buen ambiente de trabajo con el equipo. %s")


def texto(n: int, anos: int | None) -> str:
    return (PLANTILLA % (f"Se requieren {anos} años de experiencia en soporte." if anos else "")) + f" Ref {n}."


def fila(n: int, titulo: str, empresa: str, sitio: str = "linkedin", ubicacion: str = "Barcelona, Spain",
         fecha: str = "2026-10-29") -> dict:
    return {"id": f"li-{n}", "site": sitio, "job_url": f"https://www.{sitio}.com/jobs/view/{n}",
            "title": titulo, "company": empresa, "location": ubicacion, "date_posted": fecha,
            "job_type": None, "interval": None, "min_amount": None, "max_amount": None, "currency": None,
            "is_remote": False, "job_level": None, "company_industry": None, "description": None}


# Lo que devuelve cada portal en cada corrida: {término: filas}. Tiene de todo lo que decide algo:
# encaje alto y bajo, senior, fuera de perfil, presencial lejos, duplicados entre portales y una
# republicación de algo que el titular oculta entre medias.
BASE = {
    "helpdesk": [fila(1, "Técnico/a Helpdesk N1 Junior", "Acme Soporte"),
                 fila(2, "Técnico Helpdesk N1", "Beta IT"),
                 fila(3, "Director Comercial", "Gamma Ventas"),
                 fila(4, "Senior Helpdesk Manager", "Delta"),
                 fila(5, "Técnico Helpdesk Junior", "Acme Madrid", ubicacion="Madrid, Spain")],
    "service desk": [fila(6, "Agente Service Desk N1", "Epsilon"),
                     fila(7, "Agente Service Desk N1", "Epsilon", sitio="indeed")],
    "junior soc": [fila(8, "Analista SOC Junior", "Zeta Seguridad")],
}
SEGUNDA = {
    "tecnico soporte n1": [fila(9, "Técnico de Soporte N1", "Eta Sistemas"),
                           fila(10, "Técnico Helpdesk N1", "Beta IT", fecha="2026-10-30")],
    "soc analyst n1": [fila(11, "Analista SOC Junior", "Zeta Seguridad", fecha="2026-11-02")],
}
TERCERA = {"tecnico informatico": [fila(12, "Técnico Informático Helpdesk Junior", "Theta")]}

ANOS = {1: None, 2: 2, 6: 1, 7: 1, 8: None, 9: 4, 10: 2, 11: None, 12: None}
TEXTOS: dict[str, str] = {}
for grupo in (BASE, SEGUNDA, TERCERA):
    for filas in grupo.values():
        for f in filas:
            numero = int(f["job_url"].rsplit("/", 1)[-1])
            TEXTOS[f["job_url"]] = texto(numero, ANOS.get(numero))
TEXTOS["https://www.linkedin.com/jobs/view/5"] = texto(5, None)

import pandas as pd  # noqa: E402

import requests  # noqa: E402

import almacen  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import perfiles  # noqa: E402
import telegram_botones as tb  # noqa: E402
from fuentes_extra import jooble as jo  # noqa: E402
from fuentes_extra import tecnoempleo as te  # noqa: E402

# MP6 añade a propósito tres cosas que el código de antes no hace, y se apagan aquí para que lo
# demás se siga comparando letra a letra: las ofertas de descubrimiento (mensajes y filas de
# `avisos` nuevas), los datos de los rasgos que cada botón apunta en su marca, y el motivo «salario o
# condiciones», que añade una fila al teclado de 🙈. La capa en sí no hace falta apagarla: con 3
# etiquetas no llega a las 15 y no cambia ninguna nota. Lo que MP6 hace de verdad con todo encendido
# lo prueba test_aprendizaje.py.
try:
    import aprendizaje  # noqa: E402
except ImportError:  # el árbol de antes de MP6
    aprendizaje = None
if aprendizaje is not None:
    aprendizaje.DESCUBRIMIENTO_AL_DIA = 0
    tb._con_rasgos = lambda chat_id, url, extra: extra
    tb.MOTIVOS.pop("s", None)

logging.disable(logging.CRITICAL)
esperar = time.sleep
time.sleep = lambda *_: None
FIXTURES = AQUI / "fixtures"
EXTRACTO = json.loads((FIXTURES / "jooble_extracto.json").read_text(encoding="utf-8"))
FEED = (FIXTURES / "tecnoempleo_extracto.xml").read_bytes()
registro: dict = {"consultas": [], "descargas": [], "telegram": [], "jooble": [], "resumenes": [],
                  "botones": []}
mundo: list[dict] = []


class Resp:
    def __init__(self, cuerpo, contenido=b""):
        self._cuerpo, self.content, self.status_code, self.ok = cuerpo, contenido, 200, True
        self.text, self.url = json.dumps(cuerpo), ""

    def json(self):
        return self._cuerpo

    def raise_for_status(self):
        pass


def espia_post(url, json=None, timeout=None, headers=None):  # noqa: A002
    if "api.telegram.org" in url:
        # El adicional recibe cosas distintas en cada árbol, y aquí se compara lo del titular.
        if str(json.get("chat_id", TITULAR)) == str(TITULAR):
            registro["telegram"].append([url.rsplit("/", 1)[-1], json])
        return Resp({"ok": True, "result": True})
    registro["jooble"].append({k: v for k, v in json.items() if k != "datecreatedfrom"})
    return Resp(EXTRACTO)


def espia_scrape(**k):
    registro["consultas"].append(k)
    filas = [f for grupo in mundo for f in grupo.get(k["search_term"], ())
             if not k.get("is_remote") and "Barcelona" in (k.get("location") or "")]
    return pd.DataFrame([dict(f) for f in filas]) if filas else pd.DataFrame()


def espia_descripciones(df, config, progreso=None, conocidas=None, competencia_conocida=None):
    if df.empty:
        return df
    df = df.copy()
    conocidas = conocidas or {}
    for columna in ("candidatos", "candidatos_tope", "publicada_en"):
        if columna not in df.columns:
            df[columna] = pd.Series([None] * len(df), index=df.index, dtype=object)
    df["description"] = df["description"].astype(object)
    for i, url in zip(df.index, df["job_url"]):
        if not isinstance(df.at[i, "description"], str):
            if url in conocidas:
                df.at[i, "description"] = conocidas[url]
            elif url in TEXTOS:
                registro["descargas"].append(url)
                df.at[i, "description"] = TEXTOS[url]
                df.at[i, "candidatos"] = int(url.rsplit("/", 1)[-1]) * 3
    return df


class SinRed:
    def __init__(self, *a, **k):
        pass

    def geocode(self, *a, **k):
        return None


requests.post = espia_post
requests.get = lambda url, **k: Resp({}, FEED)
buscador.scrape_jobs = espia_scrape
buscador.completar_descripciones = espia_descripciones
buscador.Nominatim = SinRed
buscador.as_completed = lambda futuros: futuros
buscador.FUENTES_PROPIAS.update(infojobs=lambda **k: None, eures=lambda **k: None)
buscador.FUENTES_CON_CORRIDA.pop("infojobs", None)


def corrida(automatica=True, reemplazar=False) -> None:
    # Las marcas van al segundo: dos corridas en el mismo segundo no cambian `visto_ultima`, y eso
    # movería el contador de cambios según lo rápido que vaya cada árbol.
    esperar(1.1)
    app._ejecutar_busqueda(app.config_mod.cargar_config(), reemplazar, automatica)
    registro["resumenes"].append({k: v for k, v in (app.BUSQUEDA.get("resumen") or {}).items()
                                  if k != "marca"} | {"error": app.BUSQUEDA.get("error")})


def pulsar(datos_boton: str, n: int) -> None:
    registro["botones"].append(tb.procesar({"update_id": n, "callback_query": {
        "id": f"cb{n}", "data": datos_boton, "message": {"message_id": n, "chat": {"id": 555}}}},
        app.config_mod.cargar_config()))


def caducar_jooble() -> None:
    uso = json.loads(jo.USO_PATH.read_text(encoding="utf-8"))
    uso["ultima_descarga"] = "2000-01-01T00:00:00+00:00"
    jo.USO_PATH.write_text(json.dumps(uso), encoding="utf-8")


# De partida, lo que dejó la migración: una oculta vieja con texto, para que entre la poda.
hace_tiempo = "2026-08-01T10:00:00"
vieja = {**fila(50, "Técnico Helpdesk Antiguo", "Vieja SL"), "description": texto(50, None),
         "visto_por_primera_vez": hace_tiempo, "visto_por_ultima_vez": hace_tiempo}
(datos / "ofertas.json").write_text(json.dumps([vieja], ensure_ascii=False), encoding="utf-8")
(datos / "estado.json").write_text(json.dumps({"favoritas": {}, "aplicadas": {}, "ocultas": {
    vieja["job_url"]: {"fecha": hace_tiempo, "titulo": vieja["title"], "empresa": vieja["company"]}}}),
    encoding="utf-8")

mundo.append(BASE)
if CON_ADICIONAL and hasattr(perfiles, "crear_adicionales"):
    almacen.urls_excluidas()  # la migración crea al titular antes que a nadie
    perfiles.crear_adicionales(app.config_mod.cargar_config())
elif CON_ADICIONAL and hasattr(perfiles, "datos_de_plantilla"):
    # Desde MP5 el .env no crea perfiles: el segundo chat entra con la plantilla, que es lo que
    # tenía el adicional. Sin esto, «con-adicional» correría un solo perfil y dejaría sin cubrir el
    # camino de la corrida con más de uno, que es para lo que existe.
    almacen.urls_excluidas()
    perfiles.guardar(777, perfiles.datos_de_plantilla(), "activo")
corrida()
alta = tb.clave_de("https://www.linkedin.com/jobs/view/1")
pulsar("f:" + alta, 1)
pulsar("o:" + tb.clave_de("https://www.linkedin.com/jobs/view/2"), 2)
pulsar("mx:" + tb.clave_de("https://www.linkedin.com/jobs/view/2"), 3)
pulsar("f:000000000000", 4)
almacen.marcar("https://www.linkedin.com/jobs/view/8", "oculta", {"title": "Analista SOC Junior"})

mundo.append(SEGUNDA)
corrida()
pulsar("a:" + alta, 5)
te._ULTIMA_DESCARGA["cuando"] = None
caducar_jooble()
corrida(automatica=False)
mundo.append(TERCERA)
corrida()
pulsar("d:" + alta, 6)
mundo.clear()
mundo.append(TERCERA)
corrida(automatica=False, reemplazar=True)
corrida()


# ---------------------------------------------------------------------- lo que ha quedado
_MARCA = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+.][\d:]+)?")


def sin_horas(valor):
    return _MARCA.sub("<hora>", json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str))


def tabla(consulta: str) -> list:
    return sorted(json.loads(sin_horas(list(f))) for f in perfiles.conexion().execute(consulta))


SUYAS = f"url IN (SELECT url FROM ofertas_perfil WHERE chat_id = {TITULAR})"
registro["base"] = {
    "ofertas": tabla(f"SELECT url, datos, bloque_empresa, bloque_titulo FROM ofertas WHERE {SUYAS}"),
    "descripciones": tabla(f"SELECT url, texto, podada FROM descripciones WHERE {SUYAS}"),
    "enlaces": tabla(f"SELECT * FROM ofertas_perfil WHERE chat_id = {TITULAR}"),
    "marcas": tabla(f"SELECT * FROM marcas WHERE chat_id = {TITULAR}"),
    "avisos": tabla(f"SELECT * FROM avisos WHERE chat_id = {TITULAR}"),
}
registro["cupo"] = json.loads(jo.USO_PATH.read_text(encoding="utf-8")).get("peticiones")
if CON_ADICIONAL:
    # Son del sistema y aquí sí cambian a propósito: el adicional descarga lo que el titular
    # descartó, y una descripción que él enlaza no se poda (D1).
    del registro["descargas"]
    # Con «Reemplazar», la fila que el titular suelta sigue viva porque la enlaza otro, y conserva
    # el dato del descubrimiento que el código viejo perdía al borrarla: es lo que pide D1.
    for fila in registro["base"]["ofertas"]:
        campos = json.loads(fila[1])
        for campo in perfiles.CAMPOS_DEL_DESCUBRIMIENTO:
            campos.pop(campo, None)
        fila[1] = json.dumps(campos, ensure_ascii=False, sort_keys=True)
else:
    registro["base"]["todas_las_ofertas"] = tabla(
        "SELECT url, datos, bloque_empresa, bloque_titulo, visto_primera, visto_ultima FROM ofertas")
    registro["base"]["todas_las_descripciones"] = tabla("SELECT url, texto, podada FROM descripciones")
    registro["base"]["perfiles"] = tabla("SELECT chat_id, estado, datos FROM perfiles")
    registro["base"]["meta"] = tabla("SELECT clave, valor FROM meta WHERE clave NOT LIKE 'huellas%'")
    registro["podadas"] = sorted(json.loads(gzip.decompress((datos / "descripciones_podadas.json.gz").read_bytes())))
    # Con la función de siempre y no leyendo el fichero: el caché es un JSON en el árbol de referencia y
    # un SQLite desde el 11-11, y `cargar_descripciones_vistas()` da los mismos enlaces en los dos.
    registro["cache"] = sorted(almacen.cargar_descripciones_vistas())
perfiles.cerrar()
salida.write_text(sin_horas(registro), encoding="utf-8")
