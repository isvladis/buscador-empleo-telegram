# -*- coding: utf-8 -*-
"""
La encuesta inicial y /ajustes (MP8, EVOLUCION-MULTIPERFIL.md §4.1-§4.5 y D6, D9).

Una persona invitada pasa de `/start` a recibir avisos de SU perfil en 3-4 minutos; y cualquiera, el
titular incluido, cambia desde Telegram modalidad, ciudad, distancia, experiencia y el resto sin tocar
ningún fichero. Lo que sale de aquí es un plan de perfil (`perfilado.generador.generar_plan`, MP7) y nada
más: la corrida no mira nunca las respuestas.

CÓMO SE CONVERSA (D6):

- **Un mensaje por paso, editado en el sitio.** Las opciones múltiples llevan ✅ en el botón, «Paso n/12»,
  `⬅️ Atrás` y, en lo opcional, `⏭️ Saltar`. Solo se manda un mensaje nuevo cuando Telegram no deja editar el
  anterior (la persona lo borró, o es muy viejo).
- **El borrador vive en `conversacion`** (perfiles.py) y se guarda en cada paso: un reinicio a mitad se
  recrea y sigue en el mismo paso. A las 24 h sin tocarlo, un recordatorio (una vez); a los 7 días, el
  borrador se borra (`mantenimiento`).
- **`callback_data` corto y versionado**, `ob1:<paso>:<acción>:<valor>`, dentro de los 64 bytes de Telegram.
  Ninguno lleva un chat_id: cada pulsación solo mira la conversación del chat que pulsa (regla 5), y un
  botón de un mensaje que ya no es el de la conversación no hace nada.
- **Texto libre solo donde no hay otra forma** (ForceReply): «Otro», certificaciones, palabras clave y
  empresas, con longitud máxima y separadores, y siempre escapado con `notificaciones._esc` al enseñarlo.
- **Ciudad**: botón «📍 Enviar mi ubicación» (teclado de respuesta, que se retira después) o el nombre
  escrito. Las dos cosas acaban en una pregunta de confirmación, y las coordenadas se guardan a 2 decimales
  (~1 km, D9): las exactas no se escriben en ninguna parte.

CÓMO SE GUARDA: `datos` de un perfil de catálogo lleva las respuestas (§3.1) y, aparte, el `plan` que sale
de ellas. Cambiar algo en /ajustes es cambiar la respuesta y regenerar el plan; lo que la persona aceptó
con las sugerencias de MP6 vive en las respuestas también (aprendizaje.aplicar_sugerencia), así que
regenerar no se lo deshace.

PERFILES CON REGLAS A MANO (el titular, y quien entre con la plantilla): sus secciones de Modalidad, Ciudad y
distancia y Experiencia se traducen a las claves de su config; antes de guardar, la config anterior va al
historial del perfil. Sectores y puestos ofrece rehacer el perfil con la encuesta, que pasa a `catalogo` y
pierde las reglas a mano.

LA CALIBRACIÓN (paso 14) y la corrida de arranque están al final de este fichero y en app.py.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta

import notificaciones
import perfiles
from fuentes_extra import salud
from perfilado import generador

logger = logging.getLogger(__name__)

_esc = notificaciones._esc

# Lo que el servicio le presta (app.arrancar, a través de telegram_bot): lanzar la corrida de arranque.
# Es el mismo dict que telegram_bot.SERVICIO.
SERVICIO: dict = {"lanzar_arranque": None}

# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------
MAX_SECTORES = 3
MAX_PUESTOS = generador.MAX_PUESTOS            # 5 (§4.2, paso 2)
MAX_OTROS = 2                                  # «Otro» escritos en puestos
MAX_TEXTO = 300                                # un mensaje de texto libre
MAX_FRASE = 60                                 # un «Otro»
MAX_ELEMENTO = 40                              # una palabra clave, una empresa, una certificación
MAX_ELEMENTOS = 8
HORAS_PARA_RECORDAR = 24
DIAS_PARA_BORRAR = 7
CADA_CUANTO_MIRAR = 1800                       # segundos entre dos pasadas de `mantenimiento`
UMBRAL_MIN, UMBRAL_MAX, PASO_UMBRAL = 4.0, 6.5, 0.5
MEJORES_DE_LA_CALIBRACION, BORDE_DE_LA_CALIBRACION, BAJAS_DE_LA_CALIBRACION = 3, 2, 1
AVISOS_DEL_ARRANQUE = 5                        # «las 5 mejores como mucho»
# Un perfil pide hasta 6 términos × 4 ámbitos = 24 consultas, y el presupuesto de la corrida son 33 para todos
# (D5): por encima de esto la ficha avisa de que lo nuevo tardará más en llegar (la rotación se alarga).
CONSULTAS_MUCHAS = 12
HISTORIAL_MAXIMO = 20
RADIOS = ((5, "5 km"), (10, "10 km"), (25, "25 km"), (50, "50 km"), (60, "Toda la provincia (60 km)"),
          (0, "Solo remoto"))
TOPES = ((0, "Sin experiencia"), (1, "1 año"), (2, "2 años"), (3, "3 años"), (5, "5 años"), (10, "10 años"),
         (None, "Sin límite"))
NIVELES = (("ninguna", "Ninguna"), ("menos_1", "Menos de 1 año"), ("1_2", "1–2 años"), ("3_5", "3–5 años"),
           ("6_10", "6–10 años"), ("mas_10", "Más de 10 años"))
FORMACIONES = (("eso", "ESO"), ("bachillerato", "Bachillerato"), ("fp_medio", "FP Grado Medio"),
               ("fp_sup", "FP Grado Superior"), ("grado", "Grado"), ("master", "Máster o doctorado"),
               ("nd", "Prefiero no decirlo"))
SALARIOS = ((None, "Sin mínimo"), (15000, "15.000"), (18000, "18.000"), (21000, "21.000"), (25000, "25.000"),
            (30000, "30.000"), (40000, "40.000+"))
IDIOMAS = (("es", "Español"), ("ca", "Catalán"), ("en", "Inglés"), ("fr", "Francés"), ("de", "Alemán"),
           ("pt", "Portugués"), ("it", "Italiano"))
JORNADAS = (("completa", "Completa"), ("parcial", "Parcial"), ("indefinido", "Indefinido"),
            ("temporal", "Temporal"), ("autonomo", "Autónomo"))
INGLES = (("basico", "Básico"), ("intermedio", "Intermedio"), ("alto", "Alto (C1+)"))
ALCANCES = (("es", "España"), ("ue", "Unión Europea"), ("mundial", "Todo el mundo"))
MODALIDADES = (("presencial", "🏢 Presencial"), ("hibrido", "🔀 Híbrido"), ("remoto", "🏠 Remoto"))

# Los pasos, en el orden del §4.2. «Paso n/12» cuenta los doce primeros; la ficha no tiene número.
ORDEN = ("sector", "puestos", "experiencia", "formacion", "ciudad", "distancia", "modalidad", "idiomas",
         "jornada", "salario", "claves", "avisos", "ficha")
CODIGO = {"sector": "se", "puestos": "pu", "experiencia": "ex", "formacion": "fo", "ciudad": "ci",
          "distancia": "di", "modalidad": "mo", "idiomas": "id", "jornada": "jo", "salario": "sa",
          "claves": "cl", "avisos": "av", "ficha": "fi", "bienvenida": "0"}
PASO_DE = {c: p for p, c in CODIGO.items()}
TITULO = {"sector": "Sector", "puestos": "Puestos", "experiencia": "Experiencia", "formacion": "Formación",
          "ciudad": "Ciudad", "distancia": "Distancia máxima", "modalidad": "Modalidad",
          "idiomas": "Idiomas para trabajar", "jornada": "Jornada y condiciones", "salario": "Salario mínimo",
          "claves": "Palabras clave", "avisos": "Avisos"}
OPCIONALES = {"formacion", "jornada", "salario", "claves"}

# Qué pasos abre cada sección de /ajustes (y de «Cambiar algo» en la ficha).
SECCIONES = {
    "mo": ("🏠 Modalidad", ("modalidad",)),
    "ci": ("📍 Ciudad y distancia", ("ciudad", "distancia")),
    "ex": ("⏳ Experiencia", ("experiencia", "formacion")),
    "pu": ("🗂️ Sectores y puestos", ("sector", "puestos")),
    "id": ("🗣️ Idiomas", ("idiomas",)),
    "sa": ("💰 Salario y jornada", ("jornada", "salario")),
    "cl": ("🔎 Palabras clave", ("claves",)),
}
# Las respuestas que se guardan en `datos` (§3.1), además del plan que sale de ellas.
RESPUESTAS = ("sectores", "sector_otro", "puestos", "otros", "experiencia", "experiencia_max_exigida",
              "experiencia_estricta", "practicas", "formacion", "certificaciones", "ubicacion", "modalidad",
              "remoto_alcance", "idiomas", "ingles", "jornada", "turnos_ok", "salario_min", "claves_mas",
              "claves_menos", "empresas_excluidas", "umbral")

# Los textos que llegan sueltos (ForceReply): qué paso y qué subpaso los espera.
_TEXTOS = {("sector", "otro"), ("puestos", "otro"), ("formacion", "cert"), ("ciudad", "pedir"),
           ("claves", "mas"), ("claves", "menos"), ("claves", "emp")}


# ----------------------------------------------------------------------
# El menú del catálogo (ligero)
#
# El catálogo entero son ~155 KB de JSON y MP7 no lo guarda en memoria a propósito. Para pintar los
# botones basta una décima parte (ids y nombres), y se pintan decenas de veces por encuesta: se guarda
# eso, que son unos KB, y se rehace si el fichero cambia.
# ----------------------------------------------------------------------
_MENU: dict = {"firma": None, "familias": [], "puestos": {}, "familia_de": {}, "visible_en": {}}
_lock_menu = threading.Lock()


def menu() -> dict:
    with _lock_menu:
        try:
            firma = generador.RUTA_CATALOGO.stat().st_mtime
        except OSError:
            firma = None
        if _MENU["firma"] != firma or not _MENU["familias"]:
            catalogo = generador.cargar_catalogo()
            familias, puestos, familia_de, visible_en = [], {}, {}, {}
            for familia in catalogo["familias"]:
                familias.append({"id": familia["id"], "emoji": familia.get("emoji", ""), "nombre": familia["nombre"],
                                 "otro": familia["otro"]["id"]})
                for puesto in familia["puestos"]:
                    puestos[puesto["id"]] = puesto["nombre"]
                    familia_de[puesto["id"]] = familia["id"]
                    for donde in [familia["id"]] + list(puesto.get("tambien_en") or []):
                        visible_en.setdefault(donde, []).append(puesto["id"])
            del catalogo
            _MENU.update(firma=firma, familias=familias, puestos=puestos, familia_de=familia_de, visible_en=visible_en)
        return _MENU


# ----------------------------------------------------------------------
# Hablar con Telegram (todo por el emisor: ritmo, 429 y 403)
# ----------------------------------------------------------------------
def _boton(texto: str, datos: str) -> dict:
    return {"text": texto, "callback_data": datos}


def _fuera_de_la_ventana(texto: str) -> str:
    return texto[:notificaciones.MAX_CARACTERES_TELEGRAM]


def _enviar(chat, texto: str, teclado: dict | None = None) -> int | None:
    cuerpo = {"chat_id": chat.chat_id, "text": _fuera_de_la_ventana(texto), "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if teclado:
        cuerpo["reply_markup"] = teclado
    respuesta = notificaciones.llamar(chat.token, "sendMessage", cuerpo)
    if not respuesta.ok:
        logger.warning("Telegram sendMessage (encuesta) respondió %s: %s", respuesta.codigo, respuesta.descripcion[:200])
        return None
    resultado = respuesta.resultado
    return resultado.get("message_id") if isinstance(resultado, dict) else None


def _editar(chat, mensaje_id, texto: str, teclado: dict | None = None) -> bool:
    """Edita el mensaje. «message is not modified» (dos pulsaciones seguidas) cuenta como bien."""
    cuerpo = {"chat_id": chat.chat_id, "message_id": mensaje_id, "text": _fuera_de_la_ventana(texto),
              "parse_mode": "HTML", "disable_web_page_preview": True}
    if teclado:
        cuerpo["reply_markup"] = teclado
    respuesta = notificaciones.llamar(chat.token, "editMessageText", cuerpo)
    if respuesta.ok or "not modified" in respuesta.descripcion.lower():
        return True
    logger.info("No se ha podido editar el mensaje de la encuesta (%s): se manda uno nuevo.", respuesta.codigo)
    return False


def _borrar_mensaje(chat, mensaje_id) -> None:
    if mensaje_id:
        notificaciones.llamar(chat.token, "deleteMessage", {"chat_id": chat.chat_id, "message_id": mensaje_id})


# ----------------------------------------------------------------------
# Texto libre: separadores, longitud máxima, nada raro
# ----------------------------------------------------------------------
def _limpio(texto) -> str:
    """Sin caracteres de control y con espacios simples: lo que se guarda y lo que irá a un portal."""
    sin_control = "".join(c if c in " \n" or unicodedata.category(c)[0] != "C" else " " for c in str(texto or ""))
    return " ".join(sin_control.split())


def lista_de_texto(texto: str) -> tuple[list[str] | None, str | None]:
    """
    «comercial, call center; ETT» -> ["comercial", "call center", "ETT"]. Devuelve (lista, None) o
    (None, por qué no). Separadores: coma, punto y coma y salto de línea. Como mucho MAX_TEXTO caracteres
    en total, MAX_ELEMENTOS elementos y MAX_ELEMENTO por elemento.
    """
    if len(texto or "") > MAX_TEXTO:
        return None, f"Es demasiado largo ({len(texto)} caracteres): máximo {MAX_TEXTO}."
    vistos, salida = set(), []
    for trozo in re.split(r"[,;\n]+", texto or ""):
        elemento = _limpio(trozo)
        if not elemento:
            continue
        if len(elemento) > MAX_ELEMENTO:
            return None, f"«{elemento[:20]}…» es muy larga: máximo {MAX_ELEMENTO} letras cada una."
        if elemento.casefold() not in vistos:
            vistos.add(elemento.casefold())
            salida.append(elemento)
    if len(salida) > MAX_ELEMENTOS:
        return None, f"Son demasiadas ({len(salida)}): máximo {MAX_ELEMENTOS}."
    return salida, None


def frase(texto: str) -> tuple[str | None, str | None]:
    """Un «Otro»: una sola frase de hasta MAX_FRASE letras."""
    limpia = _limpio(texto)
    if not limpia:
        return None, "No he entendido nada. Escríbelo con letras."
    if len(limpia) > MAX_FRASE:
        return None, f"Es demasiado largo ({len(limpia)} letras): máximo {MAX_FRASE}."
    return limpia, None


# ----------------------------------------------------------------------
# Las fuentes que hoy buscan (para decirlo claro cuando no sale nada)
# ----------------------------------------------------------------------
NOMBRES_DE_FUENTE = {"linkedin": "LinkedIn", "indeed": "Indeed", "infojobs": "InfoJobs", "eures": "EURES",
                     "jooble": "Jooble", "tecnoempleo": "Tecnoempleo"}


# Las fuentes que no responden a una consulta remota: Jooble devuelve vacío si `is_remote` (jooble.py) y el feed de
# Tecnoempleo es el de la provincia de Barcelona (buscador._duenos_de_corrida). Contarlas en un perfil solo remoto
# sería decirle que se busca donde no se busca.
SIN_REMOTO = {"jooble", "tecnoempleo"}


def fuentes_del_plan(plan: dict | None = None) -> tuple[list[str], list[str]]:
    """(las que buscan, las desactivadas) entre las del plan, o entre todas si no hay plan."""
    sitios = set()
    for ambito in ((plan or {}).get("ambitos") or {}).values():
        if ambito.get("activo", True):
            propios = set(ambito.get("sitios") or [])
            sitios |= (propios - SIN_REMOTO) if ambito.get("is_remote") else propios
    if not sitios:
        sitios = set(NOMBRES_DE_FUENTE) - {"tecnoempleo"}
    activas = sorted(NOMBRES_DE_FUENTE.get(s, s) for s in sitios if salud.esta_activa(s))
    caidas = sorted(NOMBRES_DE_FUENTE.get(s, s) for s in sitios if not salud.esta_activa(s))
    return activas, caidas


def _y(nombres: list[str]) -> str:
    """«A, B y C», y «A e Indeed»: en español la y delante de una i- (o hi-, salvo hie-) es «e»."""
    if len(nombres) <= 1:
        return "".join(nombres)
    conjuncion = "e" if re.match(r"(?i)(?:hi(?!e)|i)", nombres[-1]) else "y"
    return ", ".join(nombres[:-1]) + f" {conjuncion} " + nombres[-1]


# ----------------------------------------------------------------------
# Respuestas <-> datos
# ----------------------------------------------------------------------
def respuestas_de(datos: dict) -> dict:
    """Las respuestas que guarda un perfil de catálogo (copia). Lo que falte se queda sin la clave."""
    r = {k: copy.deepcopy(datos[k]) for k in RESPUESTAS if k in datos}
    avisos = datos.get("avisos") or {}
    r["descubrimiento"] = avisos.get("descubrimiento", True) is not False
    if "sectores" not in r:
        r["sectores"] = sorted({p.split(".")[0] for p in r.get("puestos") or []})
    return r


def para_generar(r: dict) -> dict:
    """
    Lo que se le da a `generar_plan`: las respuestas y, si no se eligió ningún puesto del catálogo, el
    «otro» de cada familia (que es lo que le da sus fuentes: ver generador.py).

    Lo escrito en «Otro» de sector y de puestos va **por separado** desde el 11-11: el puesto escrito es un
    puesto (con su núcleo) y el sector escrito sigue siendo una palabra clave. Antes iban juntos y los dos
    eran palabras clave, que no llegaban nunca al listón.
    """
    salida = {k: copy.deepcopy(v) for k, v in r.items() if k not in ("sectores", "descubrimiento")}
    puestos = [p for p in r.get("puestos") or [] if not p.endswith(".otro")]
    otros = list(r.get("otros") or [])
    if not puestos:
        familias = menu()["familias"]
        puestos = [f["otro"] for f in familias if f["id"] in (r.get("sectores") or [])]
    salida["puestos"], salida["otros"] = puestos, otros
    if "experiencia_max_exigida" not in salida:
        salida.pop("experiencia_max_exigida", None)
    return salida


def plan_de(r: dict) -> dict:
    return generador.generar_plan(para_generar(r))


def _familias_de(r: dict) -> set[str]:
    """Para saber si hay cambio de sector (y entonces lo aprendido de antes ya no vale, MP6)."""
    return set(r.get("sectores") or []) | ({"otro"} if r.get("sector_otro") else set())


# ----------------------------------------------------------------------
# El borrador
# ----------------------------------------------------------------------
def _nuevo_borrador(modo: str, r: dict, **resto) -> dict:
    return {"v": 1, "modo": modo, "paso": "bienvenida" if modo == "encuesta" else None, "sub": None, "pila": [],
            "r": r, **resto}


def _clave_de_paso(b: dict) -> str:
    return ("enc" if b.get("modo") in ("encuesta", "rehacer") else "aju") + ":" + str(b.get("paso"))


def _guardar(chat, b: dict, mensaje_id) -> None:
    perfiles.guardar_conversacion(chat.chat_id, _clave_de_paso(b), b, mensaje_id)


def _cargar(chat) -> tuple[dict | None, int | None]:
    conv = perfiles.conversacion(chat.chat_id)
    if conv is None or not conv["borrador"].get("paso"):
        return None, None
    return conv["borrador"], conv["mensaje_id"]


# ----------------------------------------------------------------------
# Las pantallas
# ----------------------------------------------------------------------
def _numero(paso: str) -> str:
    return f"{ORDEN.index(paso) + 1}/12" if paso in ORDEN[:12] else ""


def _cabecera(b: dict) -> str:
    paso = b["paso"]
    if b.get("modo") == "editar":
        return f"<b>✏️ {_esc(TITULO.get(paso, paso))}</b>"
    return f"<b>Paso {_numero(paso)} · {_esc(TITULO.get(paso, paso))}</b>"


def _marca(texto: str, activo: bool) -> str:
    return ("✅ " if activo else "") + texto


def _fila_de_nav(b: dict, *, saltar: bool = False, siguiente: str | None = None, atras: bool = True) -> list[dict]:
    c = CODIGO[b["paso"]]
    fila = []
    if atras and _puede_volver(b):
        fila.append(_boton("⬅️ Atrás" if _hay_paso_anterior(b) else "↩️ Ajustes", f"ob1:{c}:b"))
    if saltar:
        fila.append(_boton("⏭️ Saltar", f"ob1:{c}:k"))
    if siguiente:
        fila.append(_boton(siguiente, f"ob1:{c}:n"))
    return fila


def _alcance_directo(b: dict) -> bool:
    """¿Está en el alcance del remoto sin haber pasado por «cómo quieres trabajar»? (tras «Solo remoto»)"""
    return (b.get("paso"), b.get("sub")) == ("modalidad", "alcance") and _es_solo_remoto(b["r"]) and not b.get("vengo_de_tipo")


def _hay_paso_anterior(b: dict) -> bool:
    """¿Atrás va a otro paso (o subpaso)? Si no, en /ajustes vuelve al menú y en la encuesta no hay botón."""
    if b.get("pila") or (b.get("paso") == "ciudad" and b.get("candidata")):
        return True
    clave = (b.get("paso"), b.get("sub"))
    return clave in _SUB_ANTERIOR and not _alcance_directo(b)


def _puede_volver(b: dict) -> bool:
    return _hay_paso_anterior(b) or b.get("modo") in ("editar", "rehacer")


def _siguiente_texto(b: dict) -> str:
    """El botón que sigue en el último paso de una sección en /ajustes dice que guarda."""
    if b.get("modo") == "editar" and _siguiente_paso(b) == "fin":
        return "💾 Guardar"
    return "Siguiente ▶️"


def _rejilla(botones: list[dict], por_fila: int = 1) -> list[list[dict]]:
    return [botones[i:i + por_fila] for i in range(0, len(botones), por_fila)]


SUBS = {"sector": (None, "otro"), "puestos": (None, "otro"), "experiencia": ("nivel", "tope", "practicas"),
        "formacion": ("nivel", "cert"), "ciudad": ("pedir",), "modalidad": ("tipo", "alcance"),
        "idiomas": ("lista", "ingles"), "claves": ("mas", "menos", "emp")}


def _pantalla_bienvenida(chat, b):
    activas, _ = fuentes_del_plan()
    texto = ("👋 <b>Hola</b>. Te aviso de ofertas de empleo que encajen contigo, buscando en "
             f"{_esc(_y(activas))} y otras. Son unas 12 preguntas, 3-4 minutos.\n\n"
             + TEXTO_DE_DATOS)
    filas = [[_boton("▶️ Empezar (3 min)", "ob1:0:go")]]
    if b.get("sub") != "datos":
        filas.append([_boton("ℹ️ Qué datos guardas", "ob1:0:dat")])
    return texto, {"inline_keyboard": filas}


TEXTO_DE_DATOS = (
    "<b>Qué guardo:</b> solo tus respuestas y tu número de chat de Telegram. Ni nombre, ni teléfono, ni CV. "
    "Las coordenadas de tu ciudad, redondeadas (~1 km).\n"
    "<b>Quién ve qué:</b> quien administra el bot puede ver tu ficha (qué buscas, experiencia, modalidad, "
    "ciudad y distancia), pero no qué ofertas recibes ni cuáles marcas.\n"
    # MP10, texto aprobado por el titular el 06-11. Lo pide D9 (transparencia): desde el 05-11 hay una copia
    # diaria de perfiles.db en el servidor y otra en el PC del titular, y /borrar no las alcanza hasta que
    # rotan (7 diarias + 4 semanales). «Comprobar que sirve» porque comprobar_copia.py abre cada una (cuenta
    # filas y huellas, no lee lo de nadie): decir solo «para recuperar el servicio» prometía menos de lo que hay.
    "<b>Copias:</b> hay una copia de seguridad al día, en el servidor y en el ordenador de quien administra, "
    "que solo se abre para comprobar que sirve y para recuperar el servicio si se estropea. Lo que borres "
    "sale también de ellas en unas 5 semanas.\n"
    "Tus datos: /misdatos · para borrarlos: /borrar.")


def _pantalla_sector(chat, b):
    r = b["r"]
    if b.get("sub") == "otro":
        return (_cabecera(b) + "\n✏️ Escribe a qué te dedicas o qué buscas, en pocas palabras (por ejemplo "
                f"«recepcionista de spa»). Máximo {MAX_FRASE} letras.", {"inline_keyboard": [_fila_de_nav(b)]})
    elegidos = set(r.get("sectores") or [])
    cuantos = len(elegidos) + (1 if r.get("sector_otro") else 0)
    botones = [_boton(_marca(f"{f['emoji']} {f['nombre']}", f["id"] in elegidos), f"ob1:se:t:{f['id']}")
               for f in menu()["familias"]]
    otro = "✏️ Otro" + (f": {r['sector_otro']}" if r.get("sector_otro") else " (escribir)")
    botones.append(_boton(_marca(otro, bool(r.get("sector_otro"))), "ob1:se:t:otro"))
    filas = _rejilla(botones) + [_fila_de_nav(b, siguiente=_siguiente_texto(b) if cuantos else None)]
    return (_cabecera(b) + f"\n¿En qué sector buscas trabajo? Elige hasta {MAX_SECTORES} ({cuantos} elegidos).",
            {"inline_keyboard": filas})


def _puestos_visibles(r: dict) -> list[str]:
    visibles = []
    for f in r.get("sectores") or []:
        for p in menu()["visible_en"].get(f, []):
            if p not in visibles:
                visibles.append(p)
    return visibles


def _pantalla_puestos(chat, b):
    r = b["r"]
    if b.get("sub") == "otro":
        return (_cabecera(b) + "\n✏️ Escribe el puesto que buscas y no está en la lista. Máximo "
                f"{MAX_FRASE} letras.", {"inline_keyboard": [_fila_de_nav(b)]})
    elegidos = set(p for p in r.get("puestos") or [] if not p.endswith(".otro"))
    botones = [_boton(_marca(menu()["puestos"][p], p in elegidos), f"ob1:pu:t:{p}") for p in _puestos_visibles(r)]
    otros = list(r.get("otros") or [])
    botones.append(_boton("✏️ Otro" + (f": {', '.join(otros)}" if otros else " (escribir)"), "ob1:pu:o"))
    hay = bool(elegidos or otros)
    texto = (_cabecera(b) + f"\n¿Qué puestos buscas? Elige hasta {MAX_PUESTOS} ({len(elegidos)} elegidos)."
             + ("\nSi no está el tuyo, usa «Otro»." if not hay else ""))
    return texto, {"inline_keyboard": _rejilla(botones) + [_fila_de_nav(b, siguiente=_siguiente_texto(b) if hay else None)]}


def _pantalla_experiencia(chat, b):
    r, sub = b["r"], b.get("sub") or "nivel"
    if sub == "nivel":
        botones = [_boton(_marca(texto, r.get("experiencia") == codigo), f"ob1:ex:s:{codigo}") for codigo, texto in NIVELES]
        return (_cabecera(b) + "\n¿Cuánta experiencia tienes en lo que buscas?",
                {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b)]})
    if sub == "tope":
        sugerido = generador.TOPE_SUGERIDO[r["experiencia"]]
        tope = r.get("experiencia_max_exigida", sugerido)
        botones = [_boton(_marca(texto + (" (sugerido)" if valor == sugerido else ""), valor == tope), f"ob1:ex:t:{'x' if valor is None else valor}")
                   for valor, texto in TOPES]
        estricta = r.get("experiencia_estricta", True)
        modo = [_boton(_marca("Descarta las que pidan más", bool(estricta)), "ob1:ex:e:1"),
                _boton(_marca("Solo bájalas de puntuación", not estricta), "ob1:ex:e:0")]
        return (_cabecera(b) + "\n¿Cuánta experiencia pueden pedir como mucho las ofertas que te aviso? "
                "Las que no dicen cuántos años piden te llegan siempre.",
                {"inline_keyboard": _rejilla(botones, 3) + [[modo[0]], [modo[1]],
                                                            _fila_de_nav(b, siguiente="Siguiente ▶️")]})
    botones = [_boton(_marca("Sí", r.get("practicas") is True), "ob1:ex:p:1"),
               _boton(_marca("No", r.get("practicas") is False), "ob1:ex:p:0")]
    return (_cabecera(b) + "\n¿Te valen prácticas, becas o programas de recién titulado?",
            {"inline_keyboard": [botones, _fila_de_nav(b)]})


def _pantalla_formacion(chat, b):
    r = b["r"]
    if b.get("sub") == "cert":
        actuales = ", ".join(r.get("certificaciones") or [])
        return (_cabecera(b) + "\n¿Alguna certificación? Escríbelas separadas por comas (por ejemplo «CCNA, "
                f"Prevención de riesgos»)." + (f"\nAhora: {_esc(actuales)}" if actuales else ""),
                {"inline_keyboard": [_fila_de_nav(b, saltar=True)]})
    botones = [_boton(_marca(texto, r.get("formacion") == codigo), f"ob1:fo:s:{codigo}") for codigo, texto in FORMACIONES]
    return (_cabecera(b) + "\n¿Qué formación tienes? (opcional)",
            {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b, saltar=True)]})


def _pantalla_ciudad(chat, b):
    r, candidata = b["r"], b.get("candidata")
    if candidata:
        texto = f"¿Es <b>{_esc(candidata['nombre'])}</b>" + (f", {_esc(candidata['region'])}" if candidata.get("region") else "") + "?"
        return (_cabecera(b) + "\n" + texto, {"inline_keyboard": [[_boton("Sí", "ob1:ci:y"), _boton("No", "ob1:ci:x")],
                                                                    _fila_de_nav(b)]})
    ahora = (r.get("ubicacion") or {}).get("nombre")
    texto = (_cabecera(b) + "\n¿En qué ciudad buscas? Escribe el nombre del municipio o toca «📍 Enviar mi ubicación» "
             "(solo en el móvil). Solo guardo la ciudad, redondeada a ~1 km.")
    if ahora:
        texto += f"\nAhora: <b>{_esc(ahora)}</b>. Escribe otra o pulsa Siguiente para dejarla."
    return texto, {"inline_keyboard": [_fila_de_nav(b, siguiente=_siguiente_texto(b) if ahora else None)]}


def _pantalla_distancia(chat, b):
    radio = (b["r"].get("ubicacion") or {}).get("radio_km")
    botones = [_boton(_marca(texto, radio == km), f"ob1:di:s:{km}") for km, texto in RADIOS]
    ciudad = _esc((b["r"].get("ubicacion") or {}).get("nombre") or "tu ciudad")
    return (_cabecera(b) + f"\n¿Hasta qué distancia de {ciudad} irías a trabajar? «Solo remoto» busca únicamente "
            "trabajo desde casa.", {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b)]})


def _es_solo_remoto(r: dict) -> bool:
    return (r.get("ubicacion") or {}).get("radio_km") == 0


def _pantalla_modalidad(chat, b):
    r = b["r"]
    sub = b.get("sub") or ("alcance" if _es_solo_remoto(r) else "tipo")
    if sub == "alcance":
        botones = [_boton(_marca(texto, (r.get("remoto_alcance") or "es") == codigo), f"ob1:mo:a:{codigo}")
                   for codigo, texto in ALCANCES]
        return (_cabecera(b) + "\nEl trabajo remoto, ¿desde dónde puede ser?",
                {"inline_keyboard": [botones, _fila_de_nav(b)]})
    elegidas = set(r.get("modalidad") or [])
    botones = [_boton(_marca(texto, codigo in elegidas), f"ob1:mo:t:{codigo}") for codigo, texto in MODALIDADES]
    return (_cabecera(b) + "\n¿Cómo quieres trabajar? Elige las que valgan.",
            {"inline_keyboard": [botones, _fila_de_nav(b, siguiente=_siguiente_texto(b) if elegidas else None)]})


def _pantalla_idiomas(chat, b):
    r = b["r"]
    if b.get("sub") == "ingles":
        botones = [_boton(_marca(texto, r.get("ingles") == codigo), f"ob1:id:i:{codigo}") for codigo, texto in INGLES]
        return (_cabecera(b) + "\n¿Qué nivel de inglés tienes para trabajar? Con «Básico» no te mando ofertas en inglés.",
                {"inline_keyboard": [botones, _fila_de_nav(b)]})
    elegidos = set(r.get("idiomas") or ["es"])
    botones = [_boton(_marca(texto, codigo in elegidos), f"ob1:id:t:{codigo}") for codigo, texto in IDIOMAS]
    return (_cabecera(b) + "\n¿En qué idiomas puedes trabajar? Solo te mando ofertas escritas en ellos.",
            {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b, siguiente=_siguiente_texto(b) if elegidos else None)]})


def _pantalla_jornada(chat, b):
    r = b["r"]
    elegidas = set(r.get("jornada") or [])
    botones = [_boton(_marca(texto, codigo in elegidas), f"ob1:jo:t:{codigo}") for codigo, texto in JORNADAS]
    botones.append(_boton(_marca("Acepto turnos, noches o fines de semana", bool(r.get("turnos_ok"))), "ob1:jo:t:turnos"))
    return (_cabecera(b) + "\n¿Qué jornada y contrato te valen? (opcional) Solo resta puntos a las ofertas que lo "
            "dicen y no casan.", {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b, saltar=True, siguiente=_siguiente_texto(b))]})


def _pantalla_salario(chat, b):
    salario = b["r"].get("salario_min")
    botones = [_boton(_marca(texto, valor == salario), f"ob1:sa:s:{'x' if valor is None else valor}") for valor, texto in SALARIOS]
    return (_cabecera(b) + "\n¿Qué sueldo mínimo buscas, en € brutos al año? (opcional) Solo actúa si la oferta lo dice, "
            "y resta puntos: no la descarta.", {"inline_keyboard": _rejilla(botones, 2) + [_fila_de_nav(b, saltar=True)]})


def _pantalla_claves(chat, b):
    r, sub = b["r"], b.get("sub") or "mas"
    pregunta, clave = {
        "mas": ("¿Qué palabras te interesan? Si salen en el título puntúan más (por ejemplo «contabilidad, A3»).", "claves_mas"),
        "menos": ("¿Y qué NO quieres ver? (por ejemplo «comercial, call center, ETT»). Restan puntos si salen en el título.", "claves_menos"),
        "emp": ("¿Hay empresas que no quieras ver? Escribe sus nombres.", "empresas_excluidas")}[sub]
    ahora = ", ".join(r.get(clave) or [])
    return (_cabecera(b) + f"\n{pregunta} Separadas por comas." + (f"\nAhora: {_esc(ahora)}" if ahora else ""),
            {"inline_keyboard": [_fila_de_nav(b, saltar=True)]})


def _pantalla_avisos(chat, b):
    activo = b["r"].get("descubrimiento", True)
    botones = [_boton(_marca("Sí", activo), "ob1:av:d:1"), _boton(_marca("No", not activo), "ob1:av:d:0")]
    return (_cabecera(b) + "\nTe aviso <b>al momento</b>, cuando sale algo que encaja; de 22:00 a 8:00 (hora de Madrid) no te "
            "molesto y lo guardo para la mañana, y como mucho te mando 10 al día: lo que sobra espera, no se pierde. "
            "Si prefieres un resumen diario o cambiar esto, en /ajustes → Avisos.\n\n"
            "🔍 <b>Ofertas para afinar</b> (como mucho 2 al día): "
            "algunas que se quedan justo por debajo de lo que te aviso, con 👍 / 👎. Ayudan a acertar más.",
            {"inline_keyboard": [botones, _fila_de_nav(b, siguiente=_siguiente_texto(b))]})


def _pantalla_ficha(chat, b):
    try:
        plan = plan_de(b["r"])
    except ValueError as e:
        return (f"<b>Tu perfil</b>\nFalta algo: {_esc(e)}.", {"inline_keyboard": [_fila_de_nav(b)]})
    activas, caidas = fuentes_del_plan(plan)
    texto = "📋 <b>Tu perfil</b>\n\n" + _esc(plan["explicacion"]["ficha"])
    texto += f"\n\nBusco en: {_esc(_y(activas))}."
    if caidas:
        texto += f" ({_esc(_y(caidas))}: desactivada{'s' if len(caidas) > 1 else ''} por bloqueos.)"
    if plan["explicacion"]["consultas"] > CONSULTAS_MUCHAS:
        texto += (f"\n\n⚠️ Son {plan['explicacion']['consultas']} consultas por vuelta: lo nuevo te llegará más despacio. "
                  "Con menos puestos o menos zonas va más rápido.")
    if b.get("modo") == "rehacer":
        texto += "\n\n⚠️ Al confirmar, tus reglas escritas a mano se sustituyen por este perfil (queda copia en tu historial)."
    if b.get("menu_de_cambios"):
        filas = [[_boton(nombre, f"ob1:fi:g:{codigo}")] for codigo, (nombre, _) in SECCIONES.items()]
        filas.append([_boton("🔔 Avisos", "ob1:fi:g:av"), _boton("↩️ Volver", "ob1:fi:v")])
        return texto + "\n\n¿Qué quieres cambiar?", {"inline_keyboard": filas}
    return texto, {"inline_keyboard": [[_boton("✅ Confirmar", "ob1:fi:c"), _boton("✏️ Cambiar algo", "ob1:fi:m")],
                                       _fila_de_nav(b)]}


_PANTALLAS = {"bienvenida": _pantalla_bienvenida, "sector": _pantalla_sector, "puestos": _pantalla_puestos,
              "experiencia": _pantalla_experiencia, "formacion": _pantalla_formacion, "ciudad": _pantalla_ciudad,
              "distancia": _pantalla_distancia, "modalidad": _pantalla_modalidad, "idiomas": _pantalla_idiomas,
              "jornada": _pantalla_jornada, "salario": _pantalla_salario, "claves": _pantalla_claves,
              "avisos": _pantalla_avisos, "ficha": _pantalla_ficha}


def _espera_texto(b: dict) -> bool:
    paso, sub = b.get("paso"), b.get("sub")
    if paso == "ciudad":
        return not b.get("candidata")
    return (paso, sub) in _TEXTOS


# ----------------------------------------------------------------------
# Pintar: edita el mensaje de la conversación y, solo si hace falta, manda uno nuevo
# ----------------------------------------------------------------------
def mostrar(chat, b: dict, nuevo: bool = False, mensaje_id=None) -> None:
    if mensaje_id is None:
        _, mensaje_id = _cargar(chat)
    texto, teclado = _PANTALLAS[b["paso"]](chat, b)
    if nuevo or not mensaje_id or not _editar(chat, mensaje_id, texto, teclado):
        mensaje_id = _enviar(chat, texto, teclado)
    _auxiliares(chat, b)
    _guardar(chat, b, mensaje_id)


def _auxiliares(chat, b: dict) -> None:
    """
    Los dos mensajes que acompañan a un paso y no son el paso: la pregunta con ForceReply (texto libre) y
    el teclado de «📍 Enviar mi ubicación». Se abren al entrar en ese paso y se cierran al salir: ninguno
    se queda en el chat.
    """
    texto = _espera_texto(b)
    if texto and not b.get("pregunta"):
        placeholder = {"ciudad": "Escribe tu ciudad"}.get(b["paso"], "Escribe aquí")
        b["pregunta"] = _enviar(chat, "👇 Escríbelo aquí" if b["paso"] != "ciudad" else "👇 Escribe tu ciudad, o toca el botón",
                                {"force_reply": True, "input_field_placeholder": placeholder})
    elif not texto and b.get("pregunta"):
        _borrar_mensaje(chat, b.pop("pregunta"))
    pide_ubicacion = b["paso"] == "ciudad" and not b.get("candidata")
    if pide_ubicacion and not b.get("teclado_ubicacion"):
        b["teclado_ubicacion"] = _enviar(chat, "📍 O comparte tu ubicación con el botón de abajo.", {
            "keyboard": [[{"text": "📍 Enviar mi ubicación", "request_location": True}]], "resize_keyboard": True,
            "one_time_keyboard": True, "input_field_placeholder": "Escribe tu ciudad"})
    elif not pide_ubicacion and b.get("teclado_ubicacion"):
        # Para quitar un teclado de respuesta hay que mandar un mensaje con `remove_keyboard`; se borra al momento.
        retirada = _enviar(chat, "👍", {"remove_keyboard": True})
        _borrar_mensaje(chat, retirada)
        _borrar_mensaje(chat, b.pop("teclado_ubicacion"))


def _cerrar_auxiliares(chat, b: dict) -> None:
    """Antes de dejar la conversación (confirmar, cancelar): que no quede ningún auxiliar abierto."""
    saliente = {**b, "paso": "ficha", "candidata": True}
    _auxiliares(chat, saliente)
    b.pop("pregunta", None)
    b.pop("teclado_ubicacion", None)


# ----------------------------------------------------------------------
# Navegar
# ----------------------------------------------------------------------
def _siguiente_paso(b: dict) -> str:
    """El paso que sigue al actual: en una sección, el siguiente de la cola; en la encuesta, el del §4.2."""
    paso, r = b["paso"], b["r"]
    cola = b.get("cola")
    candidatos = list(cola) if cola is not None else list(ORDEN)
    if paso in candidatos:
        candidatos = candidatos[candidatos.index(paso) + 1:]
    else:
        candidatos = []
    for siguiente in candidatos:
        if siguiente == "puestos" and not r.get("sectores"):
            continue  # solo «Otro» de sector: no hay menú de puestos
        return siguiente
    return "fin"


def _sub_inicial(r: dict, paso: str):
    """El subpaso con el que se entra en un paso: el primero de SUBS, salvo quien busca solo remoto en modalidad."""
    if paso == "modalidad":
        return "alcance" if _es_solo_remoto(r) else "tipo"
    subs = SUBS.get(paso)
    return subs[0] if subs else None


def _ir(b: dict, paso: str) -> None:
    if b.get("paso") and b["paso"] != paso and b["paso"] != "bienvenida":
        b.setdefault("pila", []).append(b["paso"])
    b.update(paso=paso, sub=_sub_inicial(b["r"], paso), candidata=None, vengo_de_tipo=False)
    if paso == "puestos":
        # Si cambió de sector, los puestos de los sectores que ya no están se van.
        permitidos = set(_puestos_visibles(b["r"]))
        b["r"]["puestos"] = [p for p in b["r"].get("puestos") or [] if p in permitidos]


def _adelante(chat, b: dict) -> str | None:
    siguiente = _siguiente_paso(b)
    if siguiente != "fin":
        _ir(b, siguiente)
        return None
    return _fin(chat, b)


# El subpaso anterior de cada subpaso (None = no hay: se sale del paso). Los dos de texto de «Otro» vuelven
# a la lista; experiencia, formación, idiomas, modalidad y palabras clave, a su pregunta de antes.
_SUB_ANTERIOR = {("sector", "otro"): None, ("puestos", "otro"): None,
                 ("experiencia", "tope"): "nivel", ("experiencia", "practicas"): "tope",
                 ("formacion", "cert"): "nivel", ("idiomas", "ingles"): "lista",
                 ("modalidad", "alcance"): "tipo", ("claves", "menos"): "mas", ("claves", "emp"): "menos"}


def _atras(chat, b: dict):
    """Atrás: primero el subpaso anterior del mismo paso, luego el paso anterior; en /ajustes, el menú."""
    paso, sub = b["paso"], b.get("sub")
    if paso == "ciudad" and b.get("candidata"):
        b["candidata"] = None
        return None
    if _alcance_directo(b):
        pass  # quien busca solo remoto llegó aquí sin pasar por «cómo quieres trabajar»: sale del paso
    elif (paso, sub) in _SUB_ANTERIOR:
        b["sub"] = _SUB_ANTERIOR[(paso, sub)]
        return None
    if b.get("pila"):
        anterior = b["pila"].pop()
        b.update(paso=anterior, sub=_sub_inicial(b["r"], anterior), candidata=None)
        if anterior == "ficha":
            b.update(cola=None, menu_de_cambios=False)
        return None
    if b.get("modo") in ("editar", "rehacer"):
        return "menu"
    return None


def _fin(chat, b: dict):
    """Se acabaron los pasos de la sección: en /ajustes se guarda; en la encuesta se vuelve a la ficha."""
    if b.get("modo") == "editar":
        return "guardar"
    if b.get("cola") is not None:
        b.update(cola=None, pila=[], paso="ficha", sub=None, candidata=None, menu_de_cambios=False)
        return None
    return None


# ----------------------------------------------------------------------
# Las pulsaciones de cada paso. Cada una cambia `b` y devuelve None (seguir), un texto (el aviso al
# pulsar) o una orden para el que llama: «menu» (volver a /ajustes) o «guardar».
# ----------------------------------------------------------------------
def _alternar(lista: list, valor, maximo: int | None = None):
    lista = list(lista or [])
    if valor in lista:
        lista.remove(valor)
    elif maximo is not None and len(lista) >= maximo:
        return None
    else:
        lista.append(valor)
    return lista


def _p_bienvenida(chat, b, accion, valor):
    if accion == "dat":
        b["sub"] = "datos"
        return None
    if accion == "go":
        _ir(b, "sector")
        return None
    return "Botón desconocido."


def _p_sector(chat, b, accion, valor):
    r = b["r"]
    if accion == "t" and valor == "otro":
        if r.get("sector_otro"):
            r["sector_otro"] = None
            return None
        if len(r.get("sectores") or []) >= MAX_SECTORES:
            return f"Como mucho {MAX_SECTORES} sectores."
        b["sub"] = "otro"
        return None
    if accion == "t" and valor in {f["id"] for f in menu()["familias"]}:
        otros = 1 if r.get("sector_otro") else 0
        actuales = list(r.get("sectores") or [])
        if valor not in actuales and len(actuales) + otros >= MAX_SECTORES:
            return f"Como mucho {MAX_SECTORES} sectores."
        r["sectores"] = _alternar(actuales, valor)
        return None
    if accion == "n":
        if not (r.get("sectores") or r.get("sector_otro")):
            return "Elige al menos un sector."
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_puestos(chat, b, accion, valor):
    r = b["r"]
    if accion == "t" and valor in menu()["puestos"] and valor in _puestos_visibles(r):
        nuevo = _alternar(r.get("puestos"), valor, MAX_PUESTOS)
        if nuevo is None:
            return f"Como mucho {MAX_PUESTOS} puestos."
        r["puestos"] = nuevo
        return None
    if accion == "o":
        if len(r.get("otros") or []) >= MAX_OTROS:
            r["otros"] = []  # volver a pulsar «Otro» con los dos usados los borra
            return None
        b["sub"] = "otro"
        return None
    if accion == "n":
        if not (r.get("puestos") or r.get("otros")):
            return "Elige al menos un puesto."
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_experiencia(chat, b, accion, valor):
    r = b["r"]
    sub = b.get("sub") or "nivel"
    if accion == "s" and sub == "nivel" and valor in dict(NIVELES):
        r["experiencia"] = valor
        r["experiencia_max_exigida"] = generador.TOPE_SUGERIDO[valor]
        r["experiencia_estricta"] = True
        if valor not in ("ninguna", "menos_1"):
            r.pop("practicas", None)
        b["sub"] = "tope"
        return None
    if accion == "t" and sub == "tope":
        valores = {("x" if v is None else str(v)): v for v, _ in TOPES}
        if valor in valores:
            r["experiencia_max_exigida"] = valores[valor]
            return None
    if accion == "e" and sub == "tope" and valor in ("0", "1"):
        r["experiencia_estricta"] = valor == "1"
        return None
    if accion == "n" and sub == "tope":
        if r.get("experiencia") in ("ninguna", "menos_1"):
            b["sub"] = "practicas"
            return None
        return _adelante(chat, b)
    if accion == "p" and sub == "practicas" and valor in ("0", "1"):
        r["practicas"] = valor == "1"
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_formacion(chat, b, accion, valor):
    r = b["r"]
    if accion == "s" and valor in dict(FORMACIONES) and b.get("sub") != "cert":
        r["formacion"] = valor
        b["sub"] = "cert"
        return None
    if accion == "k":
        if b.get("sub") == "cert":
            return _adelante(chat, b)
        b["sub"] = "cert"
        return None
    return "Botón desconocido."


def _p_ciudad(chat, b, accion, valor):
    r = b["r"]
    if accion == "y" and b.get("candidata"):
        c = b["candidata"]
        anterior = (r.get("ubicacion") or {}).get("radio_km")
        r["ubicacion"] = {"nombre": c["nombre"], "lat": c["lat"], "lon": c["lon"]}
        if anterior is not None:
            r["ubicacion"]["radio_km"] = anterior
        b["candidata"] = None
        return _adelante(chat, b)
    if accion == "x":
        b["candidata"] = None
        return None
    if accion == "n" and (r.get("ubicacion") or {}).get("nombre"):
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_distancia(chat, b, accion, valor):
    r = b["r"]
    if accion == "s" and valor in {str(km) for km, _ in RADIOS}:
        km = int(valor)
        r.setdefault("ubicacion", {})["radio_km"] = km
        if km == 0:
            r["modalidad"] = ["remoto"]
            r["remoto_alcance"] = r.get("remoto_alcance") or "es"
        elif r.get("modalidad") == ["remoto"]:
            r["modalidad"] = []  # antes solo remoto: que elija de nuevo, ahora que acepta presencial
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_modalidad(chat, b, accion, valor):
    r = b["r"]
    sub = b.get("sub") or ("alcance" if _es_solo_remoto(r) else "tipo")
    if accion == "t" and sub == "tipo" and valor in dict(MODALIDADES):
        r["modalidad"] = _alternar(r.get("modalidad"), valor)
        return None
    if accion == "n" and sub == "tipo":
        elegidas = r.get("modalidad") or []
        if not elegidas:
            return "Elige al menos una."
        if "remoto" in elegidas:
            r["remoto_alcance"] = r.get("remoto_alcance") or "es"
            b["sub"], b["vengo_de_tipo"] = "alcance", True  # Atrás vuelve a «cómo quieres trabajar»
            if len(elegidas) == 1:
                r.setdefault("ubicacion", {})["radio_km"] = 0
            return None
        r["remoto_alcance"] = None
        _arreglar_radio(r)
        return _adelante(chat, b)
    if accion == "a" and sub == "alcance" and valor in dict(ALCANCES):
        r["remoto_alcance"] = valor
        if r.get("modalidad") != ["remoto"]:
            _arreglar_radio(r)
        return _adelante(chat, b)
    return "Botón desconocido."


def _arreglar_radio(r: dict) -> None:
    """Quien acepta presencial o híbrido necesita una distancia: si estaba en «solo remoto», 25 km."""
    ubicacion = r.setdefault("ubicacion", {})
    if set(r.get("modalidad") or []) & {"presencial", "hibrido"} and not ubicacion.get("radio_km"):
        ubicacion["radio_km"] = 25


def _p_idiomas(chat, b, accion, valor):
    r = b["r"]
    sub = b.get("sub") or "lista"
    if accion == "t" and sub == "lista" and valor in dict(IDIOMAS):
        nuevo = _alternar(r.get("idiomas") or ["es"], valor)
        r["idiomas"] = nuevo
        return None
    if accion == "n" and sub == "lista":
        if not r.get("idiomas"):
            return "Elige al menos un idioma."
        if "en" in r["idiomas"]:
            b["sub"] = "ingles"
            return None
        r["ingles"] = None
        return _adelante(chat, b)
    if accion == "i" and sub == "ingles" and valor in dict(INGLES):
        r["ingles"] = valor
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_jornada(chat, b, accion, valor):
    r = b["r"]
    if accion == "t" and valor == "turnos":
        r["turnos_ok"] = not r.get("turnos_ok")
        return None
    if accion == "t" and valor in dict(JORNADAS):
        r["jornada"] = _alternar(r.get("jornada"), valor)
        return None
    if accion in ("n", "k"):
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_salario(chat, b, accion, valor):
    r = b["r"]
    if accion == "s":
        valores = {("x" if v is None else str(v)): v for v, _ in SALARIOS}
        if valor in valores:
            r["salario_min"] = valores[valor]
            return _adelante(chat, b)
    if accion == "k":
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_claves(chat, b, accion, valor):
    if accion == "k":
        subs = SUBS["claves"]
        indice = subs.index(b.get("sub") or "mas")
        if indice + 1 < len(subs):
            b["sub"] = subs[indice + 1]
            return None
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_avisos(chat, b, accion, valor):
    if accion == "d" and valor in ("0", "1"):
        b["r"]["descubrimiento"] = valor == "1"
        return None
    if accion == "n":
        return _adelante(chat, b)
    return "Botón desconocido."


def _p_ficha(chat, b, accion, valor):
    if accion == "m":
        b["menu_de_cambios"] = True
        return None
    if accion == "v":
        b["menu_de_cambios"] = False
        return None
    if accion == "g" and (valor in SECCIONES or valor == "av"):
        # «Cambiar algo» desde la ficha: los pasos de esa sección y, al acabar, otra vez la ficha. Atrás desde
        # el primero también vuelve a ella (queda en la pila).
        pasos = list(SECCIONES[valor][1]) if valor in SECCIONES else ["avisos"]
        b.update(cola=pasos, menu_de_cambios=False)
        _ir(b, pasos[0])
        return None
    if accion == "c":
        return "confirmar"
    return "Botón desconocido."


_PULSAR = {"bienvenida": _p_bienvenida, "sector": _p_sector, "puestos": _p_puestos, "experiencia": _p_experiencia,
           "formacion": _p_formacion, "ciudad": _p_ciudad, "distancia": _p_distancia, "modalidad": _p_modalidad,
           "idiomas": _p_idiomas, "jornada": _p_jornada, "salario": _p_salario, "claves": _p_claves,
           "avisos": _p_avisos, "ficha": _p_ficha}
_ORDENES = {"menu", "guardar", "confirmar"}


def boton(chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    """
    Una pulsación `ob1:...` (partes = lo que va detrás de «ob1:», separado por «:»). Devuelve
    (qué ha hecho, el aviso que sale al pulsar). Ni calibración, ni /ajustes de reglas a mano, ni los
    pasos de la encuesta miran más que a este chat.
    """
    if not partes:
        return "desconocido", "Botón desconocido."
    cabeza = partes[0]
    if cabeza == "cal":
        return _votar_calibracion(chat, partes[1:], mensaje_id)
    if cabeza == "E":
        return _entrar_en_ajustes(chat, partes[1:], mensaje_id)
    if cabeza == "H":
        return _a_mano(chat, partes[1:], mensaje_id)
    b, guardado = _cargar(chat)
    if b is None:
        return "sin_conversacion", "Esto ya no está abierto. Prueba con /ajustes o /start."
    if mensaje_id != guardado:
        # El de un mensaje que ya no es el de la conversación (otro paso viejo, o el de antes de un reinicio).
        return "antiguo", "Ese mensaje es antiguo: sigue por el último."
    paso = PASO_DE.get(cabeza)
    if paso is None or paso != b.get("paso"):
        return "otro_paso", "Ese botón ya no vale."
    accion = partes[1] if len(partes) > 1 else ""
    valor = partes[2] if len(partes) > 2 else None
    if accion == "b":
        resultado = _atras(chat, b)
    else:
        resultado = _PULSAR[paso](chat, b, accion, valor)
    return _despues(chat, b, resultado, mensaje_id)


def _despues(chat, b: dict, resultado, mensaje_id=None) -> tuple[str, str]:
    """Lo que sigue a una pulsación: una orden (menú, guardar, confirmar), un aviso sin cambios, o pintar el paso."""
    if resultado == "menu":
        return _cancelar_edicion(chat, b)
    if resultado == "guardar":
        return _guardar_edicion(chat, b)
    if resultado == "confirmar":
        return _confirmar(chat, b)
    if isinstance(resultado, str):
        return "aviso", resultado  # no ha cambiado nada: solo el aviso al pulsar, sin repintar
    mostrar(chat, b, mensaje_id=mensaje_id)
    return "paso:" + str(b["paso"]), ""


# ----------------------------------------------------------------------
# Lo que se escribe y lo que se comparte (ForceReply y teclado de ubicación)
# ----------------------------------------------------------------------
_ULTIMA_PREGUNTA_A_NOMINATIM = [0.0]
_lock_nominatim = threading.Lock()


def _consultar_nominatim(texto: str | None = None, coordenadas: tuple | None = None):
    """
    La consulta de verdad a Nominatim (la misma librería y el mismo `user_agent` que usa la corrida), a
    menos de una por segundo. Aparte para poder sustituirla en las pruebas (`GEOCODIFICAR`).
    Devuelve {nombre, region, lat, lon} o None.
    """
    import buscador  # aquí y no arriba: arrastra jobspy y pandas, y el servicio ya lo tiene cargado

    if not buscador.GEOCODING_DISPONIBLE:
        return None
    with _lock_nominatim:
        espera = 1.0 - (time.monotonic() - _ULTIMA_PREGUNTA_A_NOMINATIM[0])
        if espera > 0:
            time.sleep(espera)
        _ULTIMA_PREGUNTA_A_NOMINATIM[0] = time.monotonic()
    geolocalizador = buscador.Nominatim(user_agent="jobspy_busqueda_personal_bcn")
    try:
        if coordenadas is not None:
            sitio = geolocalizador.reverse(coordenadas, language="es", zoom=10, addressdetails=True, timeout=10)
        else:
            sitio = geolocalizador.geocode(texto, country_codes="es", language="es", addressdetails=True, timeout=10)
    except Exception as e:
        # A la persona le sale lo mismo que con una ciudad que no existe («no la encuentro»), así que sin
        # esta línea un Nominatim caído no se distinguía en el log de un nombre mal escrito (MP10). Solo el
        # tipo de error: el texto es lo que escribió la persona y no va al log.
        logger.warning("Nominatim no ha contestado a la encuesta: %s.", type(e).__name__)
        return None
    if not sitio:
        return None
    raw = getattr(sitio, "raw", None) or {}
    direccion = raw.get("address") or {}
    if raw.get("addresstype") in buscador._TIPOS_QUE_NO_SON_UN_SITIO:
        return None
    nombre = next((direccion[c] for c in ("city", "town", "village", "municipality", "hamlet") if direccion.get(c)),
                  None) or (raw.get("name") if raw.get("addresstype") not in (None, "road") else None)
    if not nombre:
        return None
    return {"nombre": nombre, "region": direccion.get("state") or direccion.get("province") or "",
            "lat": sitio.latitude, "lon": sitio.longitude}


# Sustituible. Recibe el texto, o (None, (lat, lon)) para una ubicación compartida.
GEOCODIFICAR = _consultar_nominatim


def _a_dos_decimales(candidata: dict) -> dict:
    return {**candidata, "lat": round(float(candidata["lat"]), 2), "lon": round(float(candidata["lon"]), 2)}


def buscar_ciudad(texto: str | None = None, coordenadas: tuple | None = None) -> dict | None:
    """
    La ciudad que corresponde a lo escrito, o a la ubicación compartida, con las coordenadas ya a 2
    decimales. Las exactas de una ubicación compartida no salen de esta función (D9). Se recuerdan en
    `meta` (no en geocode_cache.json): ese fichero se reescribe entero y lo escribe también la corrida,
    y aquí hace falta el nombre además de las coordenadas.
    """
    if coordenadas is not None:
        coordenadas = (round(float(coordenadas[0]), 2), round(float(coordenadas[1]), 2))
        clave = None
    else:
        normal = " ".join(unicodedata.normalize("NFKD", texto.casefold()).encode("ascii", "ignore").decode().split())
        clave = "ciudad:" + hashlib.sha1(normal.encode("utf-8")).hexdigest()[:12] + ":v1"
        guardada = perfiles.meta_get(clave)
        if guardada:
            try:
                return json.loads(guardada)
            except ValueError:
                pass
    encontrada = GEOCODIFICAR(texto, coordenadas)
    if not encontrada:
        return None
    encontrada = _a_dos_decimales(encontrada)
    if clave:
        perfiles.meta_set(clave, json.dumps(encontrada, ensure_ascii=False))
    return encontrada


def texto_libre(chat, mensaje: dict) -> str:
    """
    Un mensaje de texto o una ubicación mientras hay una conversación abierta. Lo que no espera el paso
    actual se contesta con una pista, sin tocar nada.
    """
    b, mensaje_id = _cargar(chat)
    if b is None:
        if chat.perfil and chat.perfil.get("estado") == "encuesta":
            return reanudar(chat) or "encuesta:reanudada"
        chat.decir("No te entiendo. Mira /ayuda.")
        return "texto"
    paso = b["paso"]
    ubicacion = mensaje.get("location")
    if ubicacion and paso == "ciudad" and not b.get("candidata"):
        sitio = buscar_ciudad(coordenadas=(ubicacion["latitude"], ubicacion["longitude"]))
        if sitio is None:
            chat.decir("No he podido saber qué ciudad es. Escríbela con letras, por favor.")
        else:
            b["candidata"] = sitio
        mostrar(chat, b, mensaje_id=mensaje_id)
        return "ubicacion"
    if ubicacion or not _espera_texto(b):
        chat.decir("Ahora usa los botones del mensaje de la encuesta 👆" if paso != "bienvenida"
                   else "Toca «▶️ Empezar» cuando quieras.")
        return "pista"
    texto = mensaje.get("text") or ""
    error = _p_texto(chat, b, texto)
    if error:
        chat.decir("⚠️ " + _esc(error))
        return "texto:error"
    orden = b.pop("orden_pendiente", None)
    if orden:
        _despues(chat, b, orden, mensaje_id)  # el texto era el último de una sección: guardar o volver
        return "texto:" + paso
    mostrar(chat, b, mensaje_id=mensaje_id)
    return "texto:" + paso


def _p_texto(chat, b: dict, texto: str) -> str | None:
    """Aplica un texto al paso que lo espera. Devuelve por qué no vale, o None."""
    r, paso, sub = b["r"], b["paso"], b.get("sub")
    if paso == "ciudad":
        limpio, error = frase(texto)
        if error:
            return error
        sitio = buscar_ciudad(texto=limpio)
        if sitio is None:
            return (f"No encuentro «{limpio[:30]}». Prueba solo con el nombre del municipio (por ejemplo «Valencia») "
                    "o comparte tu ubicación.")
        b["candidata"] = sitio
        return None
    if (paso, sub) in (("sector", "otro"), ("puestos", "otro")):
        limpia, error = frase(texto)
        if error:
            return error
        if paso == "sector":
            r["sector_otro"] = limpia
        else:
            r["otros"] = (list(r.get("otros") or []) + [limpia])[:MAX_OTROS]
        b["sub"] = None
        return None
    elementos, error = lista_de_texto(texto)
    if error:
        return error
    if paso == "formacion":
        r["certificaciones"] = elementos
        return _salto_tras_texto(chat, b)
    if paso == "claves":
        clave = {"mas": "claves_mas", "menos": "claves_menos", "emp": "empresas_excluidas"}[sub or "mas"]
        r[clave] = elementos
        subs = SUBS["claves"]
        indice = subs.index(sub or "mas")
        if indice + 1 < len(subs):
            b["sub"] = subs[indice + 1]
            return None
        return _salto_tras_texto(chat, b)
    return "No esperaba texto aquí."


def _salto_tras_texto(chat, b: dict) -> str | None:
    resultado = _adelante(chat, b)
    if resultado in _ORDENES:
        b["orden_pendiente"] = resultado
    return None


# ----------------------------------------------------------------------
# Empezar, retomar y guardar
# ----------------------------------------------------------------------
def iniciar(chat) -> None:
    """Quien entra con la encuesta: la bienvenida con sus dos botones. Queda como `encuesta` hasta confirmar."""
    b = _nuevo_borrador("encuesta", {"descubrimiento": True, "idiomas": ["es"]})
    mostrar(chat, b, nuevo=True, mensaje_id=None)


def reanudar(chat) -> str | None:
    """`/start` de quien está en `encuesta`, o escribe con el borrador ya borrado: sigue donde estaba."""
    b, _ = _cargar(chat)
    if b is None:
        iniciar(chat)
        return "encuesta:nueva"
    mostrar(chat, b, nuevo=True)
    return "encuesta:retomada"


def _cancelar_edicion(chat, b: dict) -> tuple[str, str]:
    _cerrar_auxiliares(chat, b)
    _, mensaje_id = _cargar(chat)
    perfiles.borrar_conversacion(chat.chat_id)
    chat.perfil = perfiles.obtener(chat.chat_id) or chat.perfil
    texto, teclado = texto_de_ajustes(chat.perfil, chat.es_titular)
    if not _editar(chat, mensaje_id, texto, teclado):
        _enviar(chat, texto, teclado)
    return "menu", ""


def _guardar_edicion(chat, b: dict) -> tuple[str, str]:
    """El último paso de una sección en /ajustes: regenera el plan, sube la versión y lo dice."""
    _cerrar_auxiliares(chat, b)
    _, mensaje_id = _cargar(chat)
    try:
        plan = plan_de(b["r"])
    except ValueError as e:
        _editar(chat, mensaje_id, f"⚠️ No he podido guardarlo: {_esc(e)}.", {"inline_keyboard": [[_boton("↩️ Ajustes", "aj1:aj")]]})
        perfiles.borrar_conversacion(chat.chat_id)
        return "error", "No se ha guardado"
    r = b["r"]
    perfil = perfiles.obtener(chat.chat_id)
    cambio_de_sector = _familias_de(r) != _familias_de(respuestas_de(perfil["datos"])) if perfil else False

    def cambiar(datos):
        _aplicar_respuestas(datos, r, plan)
        if cambio_de_sector:
            # Lo marcado antes de cambiar de sector es de otro perfil (MP6): no cuenta, como en el reenfoque.
            aprendido = datos.setdefault("ajustes_aprendidos", {})
            aprendido.update(desde=perfiles._ahora(), olvidados=[], apagada=None, semanas=[])

    perfiles.actualizar_datos(chat.chat_id, cambiar)
    perfiles.borrar_conversacion(chat.chat_id)
    chat.perfil = perfiles.obtener(chat.chat_id)
    logger.info("Perfil cambiado desde /ajustes (%s).", b.get("seccion") or "sección")
    texto = "✅ <b>Guardado.</b> " + _esc(_ahora_busco(plan))
    if cambio_de_sector:
        texto += "\nComo has cambiado de sector, lo que habías marcado hasta ahora no cuenta para lo aprendido."
    _editar(chat, mensaje_id, texto, {"inline_keyboard": [[_boton("↩️ Ajustes", "aj1:aj")]]})
    return "guardado", "Guardado"


def _ahora_busco(plan: dict) -> str:
    busqueda = plan["explicacion"]["busqueda"].replace("🔎 Buscaré:", "Ahora busco:")
    return busqueda + f" · {plan['explicacion']['consultas']} consultas por vuelta."


def _aplicar_respuestas(datos: dict, r: dict, plan: dict) -> None:
    """Las respuestas y el plan que salen de ellas, en `datos`, sin tocar nada más (lo aprendido, avisos…)."""
    for clave in RESPUESTAS:
        if clave in r:
            datos[clave] = copy.deepcopy(r[clave])
        else:
            datos.pop(clave, None)
    datos["reglas"] = "catalogo"
    datos["v"] = 1
    datos["plan"] = plan
    datos.setdefault("avisos", {})
    datos["avisos"].update(modo=datos["avisos"].get("modo") or "instantaneo",
                           descubrimiento=bool(r.get("descubrimiento", True)))


def _confirmar(chat, b: dict) -> tuple[str, str]:
    """El paso 13: se guarda el perfil, se activa y se lanza la corrida de arranque (paso 14)."""
    _cerrar_auxiliares(chat, b)
    _, mensaje_id = _cargar(chat)
    r = b["r"]
    try:
        plan = plan_de(r)
    except ValueError as e:
        _editar(chat, mensaje_id, f"⚠️ Falta algo para crear tu perfil: {_esc(e)}.")
        return "error", "Falta algo"
    perfil = perfiles.obtener(chat.chat_id)
    rehace = b.get("modo") == "rehacer"
    vacia = perfiles.contar_ofertas(chat.chat_id) == 0

    def cambiar(datos):
        if rehace:
            # Vuelta atrás (regla 7): lo que tenía queda en el historial del perfil, con su fecha.
            anterior = {k: v for k, v in datos.items() if k not in ("historial", "ajustes_aprendidos")}
            datos["historial"] = ((datos.get("historial") or []) + [
                {"fecha": perfiles._ahora(), "seccion": "rehacer con la encuesta", "datos_anteriores": anterior}])[-HISTORIAL_MAXIMO:]
            for clave in [c for c in datos if c not in ("historial", "ajustes_aprendidos", "avisos")]:
                del datos[clave]
            aprendido = datos.setdefault("ajustes_aprendidos", {})
            aprendido.update(desde=perfiles._ahora(), olvidados=[], apagada=None, semanas=[])
        _aplicar_respuestas(datos, r, plan)
        if not rehace and (chat.chat_id != perfiles.titular() or (perfil and perfil["estado"] == "encuesta")):
            # MP9: quien entra con la encuesta empieza con silencio de noche y tope diario (valores provisionales,
            # sin calibrar), que es lo que dice el paso 12. Un perfil que ya existía, o el titular, conserva lo suyo;
            # salvo el titular que sale de `encuesta` (12-11: se ha reiniciado, o estrena la instalación): no le
            # queda nada que conservar, y el paso 12 se lo ha prometido igual que a cualquiera.
            import avisos_perfil

            avisos = datos.setdefault("avisos", {})
            avisos.setdefault("silencio", list(avisos_perfil.SILENCIO_POR_DEFECTO))
            avisos.setdefault("max_dia", avisos_perfil.MAX_DIA_POR_DEFECTO)
        datos["arranque"] = {"estado": "pendiente" if vacia else "no_hace_falta"}
        if not vacia and chat.chat_id != perfiles.titular():
            # Que lo nuevo de su primera corrida no sea una avalancha. El titular no lo necesita: sus avisos
            # los decide él. (Su corrida sí mira la marca desde el 12-11, pero solo la del arranque, con la
            # lista vacía: ver app._ejecutar_busqueda.)
            datos["primera_pendiente"] = True

    perfiles.actualizar_datos(chat.chat_id, cambiar)
    if perfil and perfil["estado"] == "encuesta":
        perfiles.cambiar_estado(chat.chat_id, "activo")
    perfiles.borrar_conversacion(chat.chat_id)
    chat.perfil = perfiles.obtener(chat.chat_id)
    logger.info("Perfil de catálogo %s (%d puestos, %d consultas).", "rehecho" if rehace else "creado",
                len(plan["puestos"]), plan["explicacion"]["consultas"])
    lanzar = SERVICIO.get("lanzar_arranque")
    if vacia and lanzar:
        _editar(chat, mensaje_id, "✅ <b>Perfil guardado.</b>\n\nVoy a buscar ofertas para ti ahora mismo: tarda un par de "
                                  "minutos. Cuando tenga algo te pido que me digas si acierto (1 minuto), y así afino. 🙌")
        try:
            lanzar(chat.chat_id)
        except Exception:
            # Queda `pendiente` en el perfil: el servicio lo retoma al arrancar, y la corrida normal le trae lo suyo.
            logger.exception("No se ha podido encolar la corrida de arranque")
    elif vacia:
        _editar(chat, mensaje_id, "✅ <b>Perfil guardado.</b>\n\nEn la próxima vuelta de búsqueda te llegan las primeras ofertas.")
    else:
        _editar(chat, mensaje_id, "✅ <b>Perfil guardado.</b>\n\n" + _esc(_ahora_busco(plan)))
    return "confirmado", "Perfil guardado"


# ----------------------------------------------------------------------
# /ajustes
# ----------------------------------------------------------------------
def es_de_catalogo(perfil: dict) -> bool:
    return (perfil.get("datos") or {}).get("reglas") == "catalogo"


def texto_de_ajustes(perfil: dict, es_titular: bool = False) -> tuple[str, dict]:
    """El menú de /ajustes: un botón por sección, y los dos atajos."""
    estado = perfil.get("estado")
    pausa = (_boton("▶️ Reanudar", "ob1:E:re") if estado == "pausado" else _boton("⏸️ Pausar", "ob1:E:pa"))
    atajos = [pausa, _boton("🏠 Solo remoto", "ob1:E:sr")]
    if es_de_catalogo(perfil):
        secciones = [_boton(nombre, f"ob1:E:{codigo}") for codigo, (nombre, _) in SECCIONES.items()]
        nota = "Cada cambio regenera tu perfil y te cuenta qué busco ahora."
    else:
        secciones = [_boton(SECCIONES[c][0], f"ob1:H:{c}") for c in ("mo", "ci", "ex", "pu")]
        nota = ("Tu perfil usa reglas escritas a mano: aquí cambias modalidad, distancia y experiencia. "
                "«Sectores y puestos» te deja rehacerlo con la encuesta.")
    secciones += [_boton("🔔 Avisos", "aj1:av"), _boton("🧠 Lo aprendido", "aj1:la")]
    return ("⚙️ <b>Ajustes</b>\n" + nota, {"inline_keyboard": _rejilla(secciones, 2) + [atajos]})


def _entrar_en_ajustes(chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    """`ob1:E:<sección>`: abre una sección de /ajustes en el mismo mensaje del menú."""
    if not partes or chat.perfil is None:
        return "desconocido", "Botón desconocido."
    codigo = partes[0]
    perfil = chat.perfil
    if perfil.get("estado") == "encuesta":
        return "encuesta", "Termina primero la encuesta."
    if codigo in ("pa", "re"):
        nuevo = "pausado" if codigo == "pa" else "activo"
        if perfiles.obtener(chat.chat_id) is None:
            perfiles.guardar(chat.chat_id, perfiles.PERFIL_GLOBAL["datos"], nuevo)
        else:
            perfiles.cambiar_estado(chat.chat_id, nuevo)
        chat.perfil = perfiles.obtener(chat.chat_id)
        _editar(chat, mensaje_id, *texto_de_ajustes(chat.perfil, chat.es_titular))
        return "pausa" if codigo == "pa" else "reanuda", "En pausa" if codigo == "pa" else "Reanudado"
    if not es_de_catalogo(perfil):
        return _solo_remoto_a_mano(chat, mensaje_id) if codigo == "sr" else ("desconocido", "Botón desconocido.")
    if codigo == "sr":
        r = respuestas_de(perfil["datos"])
        if _es_solo_remoto(r) and r.get("modalidad") == ["remoto"]:
            return "ya_solo_remoto", "Ya buscas solo en remoto"
        r.setdefault("ubicacion", {})["radio_km"] = 0
        r["modalidad"], r["remoto_alcance"] = ["remoto"], r.get("remoto_alcance") or "es"
        b = _nuevo_borrador("editar", r, paso="modalidad", seccion="sr", cola=["modalidad"], fin="guardar")
        _guardar(chat, b, mensaje_id)
        return _guardar_edicion(chat, b)
    if codigo not in SECCIONES:
        return "desconocido", "Botón desconocido."
    r = respuestas_de(perfil["datos"])
    pasos = list(SECCIONES[codigo][1])
    if pasos[0] == "puestos" and not r.get("sectores"):
        pasos = pasos[-1:]
    b = _nuevo_borrador("editar", r, paso=pasos[0], seccion=codigo, cola=pasos, fin="guardar")
    # En la sección Modalidad siempre se empieza por «cómo quieres trabajar»: quien busca solo remoto puede
    # querer volver a aceptar presencial. El salto directo al alcance es de la encuesta (tras «Solo remoto»).
    b["sub"] = "tipo" if pasos[0] == "modalidad" else _sub_inicial(r, pasos[0])
    perfiles.guardar_conversacion(chat.chat_id, _clave_de_paso(b), b, mensaje_id)
    mostrar(chat, b, mensaje_id=mensaje_id)
    return "editar:" + codigo, ""


# ----------------------------------------------------------------------
# /ajustes de un perfil con reglas a mano (el titular, y quien entró con la plantilla)
#
# Cada sección se traduce a las claves de su config: ámbitos, distancia y años. Antes de guardar, lo que
# había en esas claves va al historial del perfil (regla 7): ahí se ve qué cambió y cuándo, y se puede
# devolver. Para el titular (`config: "global"`) esas claves viven en config.json y se escriben con
# `perfiles.escribir_claves_de_config`, que solo toca ellas; para los demás, en su bloque `config`.
# ----------------------------------------------------------------------
CLAVES_A_MANO = {"mo": ("ambitos",), "ci": ("max_distancia_km", "distancia_portal_millas"),
                 "ex": ("anos_experiencia_max",)}
KM_A_MANO = (5, 10, 25, 50, 60)
TOPES_A_MANO = (0, 1, 2, 3, 5, 10)


def _config_global() -> dict:
    import config as config_mod

    return config_mod.cargar_config()


def _ambitos_a_mano(efectiva: dict) -> list[tuple[str, dict]]:
    return sorted((efectiva.get("ambitos") or {}).items(), key=lambda kv: (kv[1].get("is_remote", False), kv[0]))


def _resumen_a_mano(efectiva: dict) -> str:
    partes = []
    ambitos = [a for _, a in _ambitos_a_mano(efectiva) if a.get("activo", True)]
    locales = [a for a in ambitos if not a.get("is_remote")]
    remotos = [a.get("etiqueta") or "remoto" for a in ambitos if a.get("is_remote")]
    if locales:
        partes.append(f"presencial hasta {notificaciones._num(float(efectiva.get('max_distancia_km') or 0))} km")
    if remotos:
        partes.append("remoto (" + ", ".join(_esc(x) for x in remotos) + ")")
    tope = efectiva.get("anos_experiencia_max")
    partes.append("experiencia: " + ("sin tope" if tope is None else f"hasta {tope} año{'s' if tope != 1 else ''}"))
    return "Ahora: " + "; ".join(partes) + "."


def _pantalla_a_mano(chat, codigo: str) -> tuple[str, dict]:
    efectiva = perfiles.config_efectiva(chat.perfil, _config_global())
    volver = [_boton("↩️ Ajustes", "aj1:aj")]
    if codigo == "mo":
        botones = [_boton(_marca(a.get("etiqueta") or clave, a.get("activo", True)), f"ob1:H:mo:t:{i}")
                   for i, (clave, a) in enumerate(_ambitos_a_mano(efectiva))]
        return ("<b>🏠 Modalidad</b>\nQué zonas se buscan. Al menos una tiene que quedar activa.\n" + _resumen_a_mano(efectiva),
                {"inline_keyboard": _rejilla(botones) + [volver]})
    if codigo == "ci":
        km = float(efectiva.get("max_distancia_km") or 0)
        botones = [_boton(_marca(f"{k} km", km == k), f"ob1:H:ci:s:{k}") for k in KM_A_MANO]
        zona = efectiva.get("centro_nombre")
        nota = (f"Tu zona es {_esc(zona)}: para otra ciudad hay que rehacer el perfil con la encuesta "
                "(Sectores y puestos).") if zona else ""
        return ("<b>📍 Ciudad y distancia</b>\nHasta dónde se acepta una oferta presencial.\n" + nota + "\n" + _resumen_a_mano(efectiva),
                {"inline_keyboard": _rejilla(botones, 3) + [volver]})
    if codigo == "ex":
        tope = efectiva.get("anos_experiencia_max")
        botones = [_boton(_marca("Sin experiencia" if t == 0 else f"{t} año{'s' if t != 1 else ''}", tope == t), f"ob1:H:ex:s:{t}")
                   for t in TOPES_A_MANO] + [_boton(_marca("Sin límite", tope is None), "ob1:H:ex:s:x")]
        estricta = bool(efectiva.get("experiencia_estricta"))
        nota = ("Las que piden más años se descartan." if estricta else
                "Las que piden más años restan puntos: con tus reglas no se descartan.")
        return ("<b>⏳ Experiencia</b>\nCuántos años pueden pedir como mucho las ofertas. Las que no los dicen, pasan.\n"
                + nota + "\n" + _resumen_a_mano(efectiva), {"inline_keyboard": _rejilla(botones, 3) + [volver]})
    return ("<b>🗂️ Sectores y puestos</b>\nTu perfil usa reglas escritas a mano para un sector concreto. Para cambiarlo hay que "
            "rehacerlo con la encuesta: pasa a un perfil de catálogo y <b>se pierden esas reglas</b> (queda una copia en tu "
            "historial), y lo que hayas marcado hasta ahora deja de contar para lo aprendido.",
            {"inline_keyboard": [[_boton("🆕 Rehacer con la encuesta", "ob1:H:pu:r")], volver]})


def _apuntar_historial(chat, codigo: str, antes: dict) -> None:
    def cambiar(datos):
        datos["historial"] = ((datos.get("historial") or []) + [
            {"fecha": perfiles._ahora(), "seccion": codigo, "antes": antes}])[-HISTORIAL_MAXIMO:]

    perfiles.actualizar_datos(chat.chat_id, cambiar)


def _escribir_a_mano(chat, codigo: str, nuevos: dict) -> None:
    """Escribe esas claves donde viva la config de este perfil, y antes apunta las de ahora en su historial."""
    efectiva = perfiles.config_efectiva(chat.perfil, _config_global())
    antes = {c: copy.deepcopy(efectiva.get(c)) for c in nuevos}
    if all(antes[c] == v for c, v in nuevos.items()):
        return
    _apuntar_historial(chat, codigo, antes)
    if (chat.perfil["datos"].get("config")) == "global":
        perfiles.escribir_claves_de_config(nuevos)
    else:
        def cambiar(datos):
            bloque = datos.get("config")
            if not isinstance(bloque, dict):
                raise ValueError("el perfil no tiene bloque de config")
            bloque.update(copy.deepcopy(nuevos))

        perfiles.actualizar_datos(chat.chat_id, cambiar)
    chat.perfil = perfiles.obtener(chat.chat_id) or chat.perfil


def _solo_remoto_a_mano(chat, mensaje_id) -> tuple[str, str]:
    """🏠 Solo remoto de un perfil con reglas a mano: apaga los ámbitos que no son remotos, y nada más."""
    efectiva = perfiles.config_efectiva(chat.perfil, _config_global())
    ambitos = copy.deepcopy(efectiva.get("ambitos") or {})
    remotos = [c for c, a in ambitos.items() if a.get("is_remote")]
    if not remotos:
        return "aviso", "Tu perfil no tiene ninguna búsqueda en remoto configurada."
    if not any(a.get("activo", True) for c, a in ambitos.items() if c in remotos):
        ambitos[sorted(remotos)[0]]["activo"] = True  # sin ningún remoto activo no quedaría nada que buscar
    if all(not a.get("activo", True) for c, a in ambitos.items() if not a.get("is_remote")):
        return "ya_solo_remoto", "Ya buscas solo en remoto"
    for a in ambitos.values():
        if not a.get("is_remote"):
            a["activo"] = False
    _escribir_a_mano(chat, "sr", {"ambitos": ambitos})
    efectiva = perfiles.config_efectiva(chat.perfil, _config_global())
    _editar(chat, mensaje_id, "✅ <b>Guardado.</b> Ahora solo busco en remoto, desde la siguiente vuelta.\n" + _resumen_a_mano(efectiva),
            {"inline_keyboard": [[_boton("↩️ Ajustes", "aj1:aj")]]})
    return "solo_remoto", "Guardado"


def _a_mano(chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    """`ob1:H:<sección>[:<acción>:<valor>]` de un perfil con reglas a mano."""
    if chat.perfil is None or es_de_catalogo(chat.perfil) or not partes:
        return "desconocido", "Botón desconocido."
    codigo = partes[0]
    if codigo not in ("mo", "ci", "ex", "pu"):
        return "desconocido", "Botón desconocido."
    accion = partes[1] if len(partes) > 1 else ""
    valor = partes[2] if len(partes) > 2 else None
    aviso = ""
    if codigo == "pu" and accion == "r":
        b = _nuevo_borrador("rehacer", {"descubrimiento": True, "idiomas": ["es"]}, paso="sector")
        perfiles.guardar_conversacion(chat.chat_id, _clave_de_paso(b), b, mensaje_id)
        mostrar(chat, b, mensaje_id=mensaje_id)
        return "rehacer", ""
    if codigo == "mo" and accion == "t" and valor is not None and valor.isdigit():
        efectiva = perfiles.config_efectiva(chat.perfil, _config_global())
        ambitos = copy.deepcopy(efectiva.get("ambitos") or {})
        lista = _ambitos_a_mano(efectiva)
        if int(valor) >= len(lista):
            return "desconocido", "Botón desconocido."
        clave = lista[int(valor)][0]
        nuevo = not ambitos[clave].get("activo", True)
        if not nuevo and sum(1 for a in ambitos.values() if a.get("activo", True)) <= 1:
            return "aviso", "Al menos una zona tiene que quedar activa."
        ambitos[clave]["activo"] = nuevo
        _escribir_a_mano(chat, "mo", {"ambitos": ambitos})
        aviso = "Guardado"
    elif codigo == "ci" and accion == "s" and valor in {str(k) for k in KM_A_MANO}:
        # El portal recibe la distancia en millas: el titular las tiene iguales que los km (8 y 8), que es
        # un radio de portal algo mayor que el filtro por km. Se mantiene esa convención.
        _escribir_a_mano(chat, "ci", {"max_distancia_km": int(valor), "distancia_portal_millas": int(valor)})
        aviso = "Guardado"
    elif codigo == "ex" and accion == "s" and valor in {str(t) for t in TOPES_A_MANO} | {"x"}:
        _escribir_a_mano(chat, "ex", {"anos_experiencia_max": None if valor == "x" else int(valor)})
        aviso = "Guardado"
    elif accion:
        return "desconocido", "Botón desconocido."
    texto, teclado = _pantalla_a_mano(chat, codigo)
    if aviso:
        texto = "✅ <b>Guardado.</b> Cambia en la siguiente vuelta de búsqueda.\n\n" + texto
    _editar(chat, mensaje_id, texto, teclado)
    return "a_mano:" + codigo, aviso


# ----------------------------------------------------------------------
# PASO 14: calibración
#
# Seis ofertas reales, puntuadas con el perfil nuevo: las 3 mejores, 2 del borde y 1 baja, con 👍 / 👎. Cada
# voto es una etiqueta de MP6 (`origen: "calibracion"`) y mueve el umbral: si rechaza 2 o más de las 3
# mejores, +0,5; si acepta 2 o más del borde, −0,5; siempre entre 4,0 y 6,5. Es una FUNCIÓN de los votos
# (no se acumula): cambiar un voto, o repetirlo, no puede mover el umbral dos veces.
#
# **Y nunca por encima del techo de esa persona** (07-11, con el piloto): votó 👎 sus seis, el listón
# subió de 5,0 a 5,5 y la mejor oferta que había tenido nunca valía 5,0, así que se quedó con CERO
# avisos, no «pocos». Y sin salida: sin avisos no hay botones, sin botones no hay etiquetas y sin
# etiquetas nada vuelve a bajarlo. El techo es la nota más alta que ya ha tenido una de sus ofertas; con
# él, al menos la mejor pasa. El suelo de 4,0 sigue mandando: si su techo no llega a 4,0, el problema es
# cómo se puntúa su sector y no el listón, y de eso avisa `avisos_perfil._vigilar_silencio`.
# ----------------------------------------------------------------------
def umbral_calibrado(base: float, votos_mejores: list[str], votos_borde: list[str],
                     techo: float | None = None) -> float:
    umbral = float(base)
    if sum(1 for v in votos_mejores if v == "no_encaja") >= 2:
        umbral += PASO_UMBRAL
    if sum(1 for v in votos_borde if v == "encaja") >= 2:
        umbral -= PASO_UMBRAL
    if techo is not None:
        umbral = min(umbral, float(techo))  # solo baja: un techo alto no sube el listón
    return round(min(UMBRAL_MAX, max(UMBRAL_MIN, umbral)), 1)


def techo_de(chat_id: int) -> float | None:
    """La mejor nota que ha tenido una oferta de este perfil, o None si todavía no tiene ninguna."""
    filas = perfiles.relevancias(chat_id)
    return float(filas[0][1]) if filas else None


def bajar_umbral_al_techo(chat_id: int) -> float | None:
    """
    Baja el listón de este perfil hasta su techo si estaba por encima, y devuelve el nuevo listón.

    Devuelve None cuando el listón no es la causa de que no reciba nada: porque ya está en su techo o por
    debajo, o porque su techo no llega al mínimo de 4,0 y bajarlo no arreglaría nada. Lo llama
    `avisos_perfil._vigilar_silencio` con los perfiles que llevan 48 h sin un solo aviso; arregla también
    los que ya estaban así antes de que la calibración mirase el techo (el piloto del 05-11).
    """
    perfil = perfiles.obtener(chat_id)
    if perfil is None:
        return None
    datos = perfil["datos"]
    actual = float(datos.get("umbral") or (datos.get("plan") or {}).get("umbral") or 5.0)
    techo = techo_de(chat_id)
    if techo is None or techo < UMBRAL_MIN or actual <= techo:
        return None
    nuevo = round(min(UMBRAL_MAX, max(UMBRAL_MIN, techo)), 1)

    def cambiar(d):
        d["umbral"] = nuevo
        d.setdefault("plan", {})["umbral"] = nuevo

    perfiles.actualizar_datos(chat_id, cambiar)
    return nuevo


def elegir_calibracion(filas: list[tuple[str, float]], umbral: float) -> dict[str, list[str]]:
    """
    De [(url, relevancia)] de mayor a menor: las 3 mejores, las 2 que quedan más cerca del umbral y 1 baja
    (la más alta de las que quedan por debajo de umbral − 2; si ninguna, la última). Sin repetir, y con
    menos si no hay tantas.
    """
    mejores = filas[:MEJORES_DE_LA_CALIBRACION]
    resto = filas[MEJORES_DE_LA_CALIBRACION:]
    borde = sorted(resto, key=lambda f: (abs(f[1] - umbral), -f[1], f[0]))[:BORDE_DE_LA_CALIBRACION]
    resto = [f for f in resto if f not in borde]
    bajas = [f for f in resto if f[1] <= umbral - 2.0]
    baja = bajas[:BAJAS_DE_LA_CALIBRACION] or resto[-BAJAS_DE_LA_CALIBRACION:]
    return {"mejores": [u for u, _ in mejores], "borde": [u for u, _ in borde], "baja": [u for u, _ in baja]}


def tras_arranque(chat_id: int, config: dict) -> str:
    """
    Lo que pasa cuando la corrida de arranque ha guardado sus ofertas: la calibración con lo encontrado y,
    de las 5 mejores como mucho, las que no estén ya en ella. Todo sale de la lista de ESE perfil.
    Devuelve qué ha hecho: «sin_nada», «calibracion» o «no_activo».
    """
    import almacen
    import buscador

    perfil = perfiles.obtener(chat_id)
    if perfil is None or perfil.get("estado") != "activo" or not es_de_catalogo(perfil):
        return "no_activo"
    efectiva = perfiles.config_efectiva(perfil, config)
    plan = perfil["datos"].get("plan") or {}
    umbral = float(efectiva.get("umbral") or 5.0)
    filas = perfiles.relevancias(chat_id)

    def marcar_hecho(extra=None):
        def cambiar(datos):
            datos["arranque"] = {"estado": "hecho"}
            datos["primera_pendiente"] = True
            if extra:
                datos["calibracion"] = extra

        perfiles.actualizar_datos(chat_id, cambiar)

    if not filas:
        activas, caidas = fuentes_del_plan(plan)
        texto = ("🤷 <b>De momento no he encontrado nada</b> con lo que me has contado. Puede ser normal, y no pasa nada: "
                 f"sigo buscando cada hora y te escribo en cuanto salga algo que encaje.\n\nAhora busco en {_esc(_y(activas))}.")
        if caidas:
            texto += (f" {_esc(_y(caidas))} no {'están' if len(caidas) > 1 else 'está'} disponible"
                      f"{'s' if len(caidas) > 1 else ''}: bloquean las consultas automáticas y no se insiste. Para algunos "
                      "oficios (hostelería, logística, comercio) eran de las mejores fuentes, así que puede salir menos.")
        texto += "\n\nSi crees que falta algo, prueba con otros puestos o palabras en /ajustes."
        notificaciones.enviar_a_un_perfil(config, chat_id, texto, "arranque sin ofertas")
        marcar_hecho()
        logger.info("Corrida de arranque de un perfil nuevo: sin ofertas.")
        return "sin_nada"

    elegidas = elegir_calibracion(filas, umbral)
    en_calibracion = elegidas["mejores"] + elegidas["borde"] + elegidas["baja"]
    por_url = almacen.ofertas_por_url(en_calibracion + [u for u, _ in filas[:AVISOS_DEL_ARRANQUE + 3]], chat_id=chat_id)
    puntuador = buscador.puntuador_para(perfil, config)
    anos_max = efectiva.get("anos_experiencia_max")
    import telegram_botones

    notificaciones.enviar_a_un_perfil(
        config, chat_id,
        f"✅ <b>Listo: he encontrado {len(filas)} oferta{'s' if len(filas) != 1 else ''}</b> para ti. Dime si estas "
        f"{len(en_calibracion)} te encajan (1 minuto): con tus 👍 y 👎 afino lo que te aviso.", "arranque: calibración")
    cuantas = len(en_calibracion)
    for n, url in enumerate(en_calibracion, 1):
        if url not in por_url:
            continue
        aviso = notificaciones.preparar_aviso(por_url[url], por_url[url], puntuador, anos_max)
        clave = telegram_botones.registrar_aviso(aviso, chat_id, tipo="calibracion")
        teclado = {"inline_keyboard": [[_boton("👍 Sí, me encaja", f"ob1:cal:{clave}:s"), _boton("👎 No", f"ob1:cal:{clave}:n")]]}
        notificaciones._enviar(config, f"🔍 <b>¿Te encaja?</b> ({n}/{cuantas})\n\n" + notificaciones._mensaje_de_una_oferta(aviso),
                               "oferta de calibración", teclado, [chat_id], "1/1")
    # De las 5 mejores, las 3 primeras ya van arriba: aquí solo las que faltan, y solo si llegan al umbral.
    ya = set(en_calibracion)
    mejores_que_llegan = [u for u, rel in filas[:AVISOS_DEL_ARRANQUE] if rel >= umbral]
    faltan = AVISOS_DEL_ARRANQUE - sum(1 for u in mejores_que_llegan if u in ya)
    extra = [u for u in mejores_que_llegan if u not in ya][:max(0, faltan)]
    if extra:
        altas = [notificaciones.preparar_aviso(por_url[u], por_url[u], puntuador, anos_max) for u in extra if u in por_url]
        notificaciones.notificar_ofertas_alto_encaje(config, altas, chat_id=chat_id, tope=AVISOS_DEL_ARRANQUE,
                                                     con_resumen=False, posicion="1/1")
    marcar_hecho({"base": umbral, **elegidas, "estado": "votando"})
    logger.info("Corrida de arranque: calibración de %d ofertas y %d avisos.", cuantas, len(extra))
    return "calibracion"


def _votar_calibracion(chat, partes: list[str], mensaje_id) -> tuple[str, str]:
    """`ob1:cal:<clave>:s|n`: un voto de la calibración, solo de este chat."""
    import almacen
    import telegram_botones

    if len(partes) < 2 or partes[1] not in ("s", "n") or chat.perfil is None:
        return "desconocido", "Botón desconocido."
    clave, voto = partes[0], partes[1]
    aviso = perfiles.aviso(chat.chat_id, clave)
    perfil = perfiles.obtener(chat.chat_id)
    if not aviso or not perfil or (perfil["datos"].get("calibracion") or {}).get("estado") is None:
        return "desconocida", "Esta oferta ya no está en la calibración."
    calibracion = perfil["datos"]["calibracion"]
    if clave not in {telegram_botones.clave_de(u) for u in calibracion.get("mejores", []) + calibracion.get("borde", [])
                     + calibracion.get("baja", [])}:
        return "desconocida", "Esta oferta ya no está en la calibración."
    url = aviso["url"]
    accion, respuesta = ("favorito", "encaja") if voto == "s" else ("oculta", "no_encaja")
    datos_oferta = {"title": aviso.get("titulo"), "company": aviso.get("empresa")}
    almacen.quitar_marcas(url, chat_id=chat.chat_id)
    almacen.fijar_marca(url, accion, datos_oferta, telegram_botones._con_rasgos(chat.chat_id, url, {"origen": "calibracion"}),
                        chat_id=chat.chat_id)
    perfiles.anotar_respuesta(chat.chat_id, clave, respuesta)
    umbral = _recalcular_umbral(chat.chat_id)
    teclado = {"inline_keyboard": [[_boton(_marca("👍 Sí, me encaja", voto == "s"), f"ob1:cal:{clave}:s"),
                                    _boton(_marca("👎 No", voto == "n"), f"ob1:cal:{clave}:n")]]}
    notificaciones.llamar(chat.token, "editMessageReplyMarkup", {"chat_id": chat.chat_id, "message_id": mensaje_id,
                                                                  "reply_markup": teclado})
    perfil = perfiles.obtener(chat.chat_id)
    todas = perfil["datos"]["calibracion"]
    avisos = perfiles.avisos(chat.chat_id)
    clave_de = telegram_botones.clave_de
    pendientes = [u for u in todas["mejores"] + todas["borde"] + todas["baja"]
                  if not (avisos.get(clave_de(u)) or {}).get("respuesta")]
    if not pendientes and todas.get("estado") != "hecha":
        perfiles.actualizar_datos(chat.chat_id, lambda d: d["calibracion"].update(estado="hecha"))
        cambio = "" if umbral == todas["base"] else f" Con lo que me has dicho, te aviso desde {notificaciones._num(umbral)} de encaje."
        notificaciones.enviar_a_un_perfil(chat.config, chat.chat_id,
                                          "🙌 <b>Gracias.</b> Ya sé un poco mejor qué buscas." + cambio
                                          + " Desde ahora te aviso de lo nuevo según salga. /ajustes para cambiar lo que quieras.",
                                          "fin de la calibración")
    return "calibracion:" + respuesta, "Anotado"


def _recalcular_umbral(chat_id: int) -> float:
    """Pone en el plan (y en las respuestas) el umbral que dicen los votos de ahora. Sin escribir si no cambia."""
    import telegram_botones

    perfil = perfiles.obtener(chat_id)
    calibracion = perfil["datos"].get("calibracion") or {}
    avisos = perfiles.avisos(chat_id)

    def votos(grupo):
        return [(avisos.get(telegram_botones.clave_de(u)) or {}).get("respuesta") for u in calibracion.get(grupo, [])]

    # Con el techo: la calibración puede bajar el listón todo lo que quiera, pero no subirlo por encima de
    # lo que esa persona puede recibir (si no, se queda sin un solo aviso y sin forma de salir).
    umbral = umbral_calibrado(calibracion.get("base", 5.0), votos("mejores"), votos("borde"), techo_de(chat_id))

    def cambiar(datos):
        datos["umbral"] = umbral
        datos.setdefault("plan", {})["umbral"] = umbral

    if float((perfil["datos"].get("plan") or {}).get("umbral", 5.0)) != umbral or perfil["datos"].get("umbral") != umbral:
        perfiles.actualizar_datos(chat_id, cambiar)
    return umbral


# ----------------------------------------------------------------------
# Recordatorio a las 24 h y borrador fuera a los 7 días
# ----------------------------------------------------------------------
_ultima_pasada = [0.0]


def mantenimiento(config: dict, ahora: datetime | None = None, forzar: bool = False) -> dict:
    """
    Lo único periódico de la encuesta: a las 24 h sin tocar el borrador, UN recordatorio; a los 7 días, el
    borrador se borra (el perfil sigue en `encuesta` y un /start empieza de nuevo). Una lectura de una
    tabla pequeña cada media hora: nada que ver con una corrida.
    """
    if not forzar and time.monotonic() - _ultima_pasada[0] < CADA_CUANTO_MIRAR:
        return {}
    _ultima_pasada[0] = time.monotonic()
    ahora = ahora or datetime.now()
    hecho = {"recordadas": 0, "borradas": 0}
    for conv in perfiles.conversaciones_abiertas():
        try:
            tocada = datetime.fromisoformat(conv["actualizado"])
        except (TypeError, ValueError):
            continue
        chat_id, b = conv["chat_id"], conv["borrador"]
        if ahora - tocada >= timedelta(days=DIAS_PARA_BORRAR):
            perfiles.borrar_conversacion(chat_id)
            hecho["borradas"] += 1
            logger.info("Borrador de la encuesta borrado: lleva %d días sin tocarse.", DIAS_PARA_BORRAR)
        elif ahora - tocada >= timedelta(hours=HORAS_PARA_RECORDAR) and not b.get("recordada"):
            b["recordada"] = True
            # Se apunta ANTES de mandarlo y sin tocar `actualizado`: si el envío falla, no se repite cada media
            # hora, y el reloj de los 7 días sigue desde la última vez que la persona hizo algo.
            _marcar_recordada(chat_id, b)
            enviado = notificaciones.enviar_a_un_perfil(
                config, chat_id,
                "👋 Te has quedado a medias con tu perfil. Cuando quieras, /start para seguir donde lo dejaste "
                "(si no lo retomas, el borrador se borra a los 7 días).", "recordatorio de la encuesta")
            hecho["recordadas"] += 1 if enviado else 0
    return hecho


def _marcar_recordada(chat_id: int, b: dict) -> None:
    perfiles.anotar_en_borrador(chat_id, b)


def registrar(pasos: dict) -> None:
    """Los pasos que reciben texto o una ubicación, en el PASOS de telegram_bot (clave: lo que guarda `conversacion.paso`)."""
    for paso in list(ORDEN) + ["bienvenida"]:
        for prefijo in ("enc", "aju"):
            pasos[f"{prefijo}:{paso}"] = texto_libre
