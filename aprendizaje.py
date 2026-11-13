# -*- coding: utf-8 -*-
"""
La capa aprendida de cada perfil (MP6, EVOLUCION-MULTIPERFIL.md D10 y §5.4).

Cada botón que pulsa una persona es una etiqueta de SU perfil, y con ellas su puntuación se corrige
sola: un motivo más, «+1,2 lo aprendido (soc, linkedin)», entre −3 y +3, que se puede ver, olvidar
rasgo a rasgo y deshacer entero.

POR QUÉ ASÍ Y NO UN MODELO. El proyecto ya entrenó uno (README.md, «El modelo entrenado») y no ganó
a las reglas: con 29 positivos, la regresión sobre el título se quedó a 0,10 de AUC, y el híbrido
solo se acercaba cuanto más se le regularizaba, hasta quedarse en «hazle caso a las reglas y corrige
un poquito». Esto es justo eso, a propósito: las reglas mandan y la capa corrige poco y con topes.
Un log-odds por rasgo se explica en una línea, y con ~150 etiquetas por perfil no hay con qué
ajustar nada más grande.

LAS ETIQUETAS (§4.4). ✅ +1,5 · ⭐ y 👍 +1 · 🙈 y 👎 −1, una por oferta. Sin pulsar no es etiqueta.
Solo cuentan las marcas de origen telegram, descubrimiento o calibracion: las ocultas en lote de la
web no (perfiles.ORIGENES_DE_ETIQUETA).

LOS RASGOS de una oferta: palabras y pares del título (`dedupe_difuso.normalizar_texto`), empresa
(`normalizar_empresa`), fuente, modalidad y el tramo de años que pide (≤1 · 2 · 3 o más). El prompt
decía «pide años de más» respecto al tope; con el tope de 3 del titular eso se daba en 2 de sus 125
etiquetas, y sus quejas de experiencia son de ofertas de 2 y 3 años (medido el 04-11). Con el tramo,
cada perfil aprende dónde empieza su «de más».

LAS ETIQUETAS DE ANTES DE UN CAMBIO DE SECTOR NO CUENTAN (decidido por el titular el 04-11, con el
dato): describen un perfil que ya no se busca. El titular tenía 125, 115 de antes del reenfoque a
ciberseguridad y 10 de después. Medido sobre la muestra de medir.py (157 casos, umbral 5; sin capa,
37 avisos con 81 % de precisión y 100 % de recall):
  - con las 125 enteras, la capa aprendía +1,5 a «user support» y «desk N1» y +1,5 a «ciberseguridad
    cloud», que MP0 dejó fuera;
  - con las de antes solo para lo que no es el título (empresa, fuente, modalidad, años), lo aprobado
    primero, 74 % y 93 %: Seidor +1,4 (favorita en soporte) subía una «Ciberseguridad Cloud», y «pide
    2 años» −1,5 hundía a un analista que la muestra da por bueno;
  - con las de antes solo para los años, 82 % y 93 % (pierde 2 buenas);
  - solo con las de después, 83 % y 100 %.
En los botones de después del reenfoque todas quedan entre 4 de 9 y 5 de 10: ruido. Así que el corte
es duro: `desde`, el mismo que usa «empezar de cero». Para `titular-ciber`, por defecto, el momento del
reenfoque; `reenfocar-titular` lo apunta desde MP6, y MP8 lo hará al cambiar de sector.

EL PESO de un rasgo: log-odds suavizado entre sus positivas y sus negativas, con un prior de α = 2
en la tasa base del perfil (una etiqueta sola no decide nada), solo si aparece en ≥ 3 etiquetas,
recortado a ±1,5. Como mucho 300 rasgos (los de más peso) y ±3,0 por oferta.

DÓNDE SE GUARDAN: en ninguna parte. Los pesos son una función pura de las etiquetas, de lo que la
persona ha olvidado y de su fecha de corte, y se recalculan en memoria cuando cambian sus marcas (una
consulta lo dice: perfiles.firma_de_marcas). Un clic no escribe un byte más, el esquema de la base no
cambia, y «empezar de cero» devuelve la puntuación de antes exacta por construcción. En
`datos.ajustes_aprendidos` solo va lo que decide la persona: pocos cientos de bytes.

LA PROTECCIÓN (el principio de la auditoría): tras calcular, ninguna ✅ o ⭐ del perfil que llega a su
umbral sin la capa puede quedar debajo con ella; si pasa, se encogen sus rasgos negativos hasta que
no. Lo que el juego de reglas ya deja debajo no es cosa de la capa: subirlo desharía MP4.

AISLAMIENTO (regla 5): todo sale de funciones de perfiles.py que exigen el chat_id, y la caché va por
chat_id. Un perfil no puede aprender de las etiquetas de otro: test_aprendizaje.py lo comprueba.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import threading
from collections import Counter
from datetime import datetime, timedelta

import perfiles
from dedupe_difuso import normalizar_empresa, normalizar_texto
from reglas import titular_ciber

logger = logging.getLogger(__name__)

# Subido de 15 a 30 el 04-11-2026, decisión del titular al verificar MP6. El motivo, medido: con
# las 10 etiquetas que había, los cuatro rasgos aprendidos eran «técnico» +0,41, «en jooble» −0,24,
# «modalidad no indicada» −0,24 y «cyber» −0,29. O sea, la capa penalizaba la palabra que define el
# perfil, porque varias ocultas llevaban «cyber» en el título por otros motivos (senior, lejos). Con
# 15 no mejora: 5 de 10 en los botones tiene un intervalo de Wilson de [24 %, 76 %]. A ~4 avisos al
# día son unos 8 días de clics en vez de 4. Se descartó dejarlo en 15 «para que aprenda antes»:
# lo que aprendería es ruido, y la protección y los topes acotan el daño pero no lo evitan.
# El prompt de la fase pedía 15 (CAMBIOS-MP6.md §2.9).
MIN_ETIQUETAS = 30
MIN_APARICIONES = 3
ALFA = 2.0
TOPE_RASGO = 1.5
TOPE_OFERTA = 3.0
MAX_RASGOS = 300
PESO_DE_LA_ACCION = {"aplicada": 1.5, "favorita": 1.0, "oculta": 1.0}
POSITIVAS = ("aplicada", "favorita")
# Lo que la capa enseña en «Lo aprendido» y cuántos olvidados se guardan como mucho (los 300 rasgos).
EN_LO_APRENDIDO = 10
# Palabras que no dicen nada del puesto. Cortas a propósito: «de» y «la» repetidas en todos los
# títulos acabarían con peso por casualidad.
_VACIAS = frozenset("de del la el los las y e o u a en para con por al the of and for to in at on i d l s "
                    "un una an".split())

# Desde cuándo cuentan las etiquetas, por juego, si el perfil no lo dice (`ajustes_aprendidos.desde`):
# lo marcado antes de que el juego rigiera es de otro perfil. `reenfocar-titular` lo apunta desde MP6.
DESDE_POR_JUEGO = {"titular-ciber": titular_ciber.VIGENTE_DESDE}


# ----------------------------------------------------------------------
# Rasgos
# ----------------------------------------------------------------------
def datos_de_rasgos(oferta: dict, anos=None) -> dict:
    """
    Lo que hace falta de una oferta, además de título y empresa, para sacar sus rasgos. Se guarda en
    la etiqueta al pulsar (y una vez para las que ya había), para no depender de la descripción, que
    la poda se lleva a los 30 días.
    """
    import buscador  # diferido: arrastra jobspy, y el servicio ya lo tiene cargado

    if anos is None:
        anos = buscador.extraer_anos_experiencia(oferta.get("description"))
    modalidad = oferta.get("modalidad_label") or buscador.modalidad(oferta)
    return {"fuente": oferta.get("site"), "modalidad": modalidad, "anos": anos}


def _tramo(anos) -> str | None:
    if anos is None:
        return None
    return "1" if anos <= 1 else "2" if anos == 2 else "3+"


def rasgos(titulo, empresa, datos: dict) -> set[str]:
    """Los rasgos de una oferta, con prefijo para que una palabra del título no sea la empresa."""
    salida: set[str] = set()
    palabras = [p for p in normalizar_texto(titulo).split() if len(p) > 1 and p not in _VACIAS]
    salida.update("t:" + p for p in palabras)
    salida.update("t:%s_%s" % par for par in zip(palabras, palabras[1:]))
    empresa = normalizar_empresa(empresa)
    if empresa:
        salida.add("e:" + empresa)
    if datos.get("fuente"):
        salida.add("f:" + str(datos["fuente"]))
    modalidad = normalizar_texto(datos.get("modalidad"))
    if modalidad:
        salida.add("m:" + modalidad)
    tramo = _tramo(datos.get("anos"))
    if tramo:
        salida.add("x:" + tramo)
    return salida


def nombre_de_rasgo(rasgo: str) -> str:
    """Cómo se le enseña a la persona: «soc analyst», «empresa jobleads», «pide 2 años»…"""
    tipo, _, valor = rasgo.partition(":")
    if tipo == "t":
        return valor.replace("_", " ")
    if tipo == "e":
        return "empresa " + valor
    if tipo == "f":
        return "en " + valor
    if tipo == "m":
        return valor
    if tipo == "x":
        return {"1": "pide 1 año o menos", "2": "pide 2 años", "3+": "pide 3 años o más"}.get(valor, valor)
    return rasgo


def huella(rasgo: str) -> str:
    """Para un botón: un rasgo puede pasar de los 64 bytes de `callback_data`."""
    return hashlib.sha1(rasgo.encode("utf-8")).hexdigest()[:8]


# ----------------------------------------------------------------------
# Lo que decide la persona
# ----------------------------------------------------------------------
def ajustes(perfil: dict) -> dict:
    """`datos.ajustes_aprendidos`, con lo que falte puesto por defecto. Una copia: no se toca el perfil."""
    datos = perfil.get("datos") or {}
    salida = copy.deepcopy(datos.get("ajustes_aprendidos") or {})
    # `or`, no setdefault: un `desde` vacío no puede devolver las etiquetas de antes del cambio de juego.
    salida["desde"] = salida.get("desde") or DESDE_POR_JUEGO.get(datos.get("reglas"))
    salida.setdefault("olvidados", [])
    salida.setdefault("apagada", None)
    return salida


def cambiar_ajustes(chat_id: int, cambiar) -> dict:
    """Cambia `datos.ajustes_aprendidos` de ESE perfil en una transacción. Sube su versión: la caché se rehace."""
    def aplicar(datos):
        actual = datos.setdefault("ajustes_aprendidos", {})
        cambiar(actual)
    return perfiles.actualizar_datos(chat_id, aplicar).get("ajustes_aprendidos") or {}


# ----------------------------------------------------------------------
# Etiquetas y pesos
# ----------------------------------------------------------------------
def _completar(chat_id: int, marcas_: list[dict]) -> None:
    """Las etiquetas de antes de MP6 no llevan sus rasgos: se apuntan una vez, de la oferta guardada."""
    faltan = {m["url"] for m in marcas_ if "rasgos" not in m}
    if not faltan:
        return
    import almacen  # diferido: almacen importa perfiles

    ofertas = almacen.ofertas_por_url(faltan, chat_id=chat_id)
    nuevos = {}
    for url in faltan:
        oferta = ofertas.get(url)
        # Sin oferta (se borró) se apunta igual, vacío: así no se vuelve a buscar en cada cálculo.
        nuevos[url] = datos_de_rasgos(oferta) if oferta else {"fuente": None, "modalidad": None, "anos": None}
    tocadas = perfiles.completar_rasgos(chat_id, nuevos)
    logger.info("Capa aprendida: %s etiqueta(s) completadas con sus rasgos (una sola vez).", tocadas)
    for m in marcas_:
        if "rasgos" not in m:
            m["rasgos"] = nuevos[m["url"]]


def etiquetas_de(chat_id: int, ajustes_: dict, hasta: str | None = None) -> list[dict]:
    """
    Una etiqueta por oferta: {url, y (1/0), peso, fecha, motivo, rasgos}. Las de hasta `desde` (un
    cambio de sector, o empezar de cero) no cuentan. Con `hasta`, solo las de antes de esa fecha: así se
    saben los pesos que había hace una semana.
    """
    marcas_ = perfiles.etiquetas(chat_id)
    _completar(chat_id, marcas_)
    por_url: dict[str, dict] = {}
    for m in marcas_:
        previa = por_url.get(m["url"])
        orden = {"aplicada": 0, "favorita": 1, "oculta": 2}
        if previa is None or orden[m["accion"]] < orden[previa["accion"]]:
            por_url[m["url"]] = m
    desde = ajustes_.get("desde")
    salida = []
    for m in por_url.values():
        fecha = m.get("fecha") or ""
        # Lo marcado hasta ese segundo incluido se olvida: «empezar de cero» va después de la última.
        if (desde and fecha <= desde) or (hasta and fecha >= hasta):
            continue
        salida.append({"url": m["url"], "y": 1 if m["accion"] in POSITIVAS else 0,
                       "peso": PESO_DE_LA_ACCION[m["accion"]], "fecha": fecha, "motivo": m.get("motivo"),
                       "accion": m["accion"], "titulo": m.get("titulo"), "empresa": m.get("empresa"),
                       "rasgos": rasgos(m.get("titulo"), m.get("empresa"), m["rasgos"])})
    return salida


def aprender(lista: list[dict], olvidados=()) -> tuple[dict[str, float], Counter]:
    """{rasgo: peso} y cuántas etiquetas tiene cada rasgo. Es la fórmula del §5.4, sin más."""
    positivas = sum(e["peso"] for e in lista if e["y"])
    negativas = sum(e["peso"] for e in lista if not e["y"])
    apariciones: Counter = Counter()
    if not positivas or not negativas:
        # Sin un lado no hay contraste: todo saldría del prior, y el prior no es una opinión.
        for e in lista:
            apariciones.update(e["rasgos"])
        return {}, apariciones
    tasa = positivas / (positivas + negativas)
    pos: Counter = Counter()
    neg: Counter = Counter()
    for e in lista:
        apariciones.update(e["rasgos"])
        (pos if e["y"] else neg).update({r: e["peso"] for r in e["rasgos"]})
    base = math.log(tasa / (1 - tasa))
    olvidados = set(olvidados)
    pesos = {}
    for rasgo, n in apariciones.items():
        if n < MIN_APARICIONES or rasgo in olvidados:
            continue
        valor = math.log((pos[rasgo] + ALFA * tasa) / (neg[rasgo] + ALFA * (1 - tasa))) - base
        valor = max(-TOPE_RASGO, min(TOPE_RASGO, valor))
        if abs(valor) >= 0.005:
            pesos[rasgo] = valor
    mas_pesados = sorted(pesos.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:MAX_RASGOS]
    return dict(mas_pesados), apariciones


def aporte(pesos: dict[str, float], rasgos_: set[str]) -> float:
    """Lo que suma la capa a una oferta, con el tope por oferta y redondeado como los demás motivos."""
    total = sum(pesos.get(r, 0.0) for r in rasgos_)
    return round(max(-TOPE_OFERTA, min(TOPE_OFERTA, total)), 1)


def proteger(pesos: dict[str, float], positivas: list[tuple[float, set[str]]], umbral: float) -> dict[str, float]:
    """
    Encoge los rasgos negativos de cada ✅/⭐ que la capa deja bajo el umbral, hasta que ninguna
    caiga. `positivas` son (suma de sus motivos sin la capa, rasgos), solo las que sin capa llegan.
    Encoger negativos no puede hundir a otra, así que unas pocas vueltas bastan.
    """
    pesos = dict(pesos)
    for _ in range(50):
        caidas = [(s, r) for s, r in positivas if round(s + aporte(pesos, r), 1) < umbral]
        if not caidas:
            break
        for suma, rasgos_ in caidas:
            negativos = [r for r in rasgos_ if pesos.get(r, 0.0) < 0]
            total_negativo = -sum(pesos[r] for r in negativos)
            if total_negativo <= 0:
                continue
            falta = umbral - (suma + aporte(pesos, rasgos_)) + 0.05
            factor = max(0.0, 1.0 - falta / total_negativo)
            for r in negativos:
                pesos[r] *= factor
    return {r: p for r, p in pesos.items() if abs(p) >= 0.005}


class Capa:
    """Lo aprendido de un perfil, listo para puntuar. No se modifica una vez creada."""

    def __init__(self, pesos: dict[str, float], n_etiquetas: int, protegidos: list[str]):
        self.pesos = pesos
        self.n_etiquetas = n_etiquetas
        self.protegidos = protegidos  # los rasgos que la protección encogió, para explicarlo

    def rasgos_de(self, titulo, ubicacion, descripcion, oferta, anos) -> set[str]:
        fila = {**(oferta or {}), "title": titulo, "location": ubicacion, "description": descripcion}
        return rasgos(titulo, fila.get("company"), datos_de_rasgos(fila, anos))

    def termino(self, titulo, ubicacion, descripcion, oferta, anos) -> tuple[str, float] | None:
        presentes = [r for r in self.rasgos_de(titulo, ubicacion, descripcion, oferta, anos) if r in self.pesos]
        valor = aporte(self.pesos, set(presentes))
        if not valor:
            return None
        # Los dos rasgos que más empujan hacia donde va el total: «lo aprendido (soc, en linkedin)».
        a_favor = sorted((r for r in presentes if self.pesos[r] * valor > 0), key=lambda r: -abs(self.pesos[r]))[:2]
        return ("lo aprendido (%s)" % ", ".join(nombre_de_rasgo(r) for r in a_favor), valor)


# ----------------------------------------------------------------------
# La capa de cada perfil, cacheada
# ----------------------------------------------------------------------
_CACHE: dict[int, tuple] = {}
_lock = threading.Lock()


def _puntuador_base(perfil: dict, config_global: dict):
    """El de siempre, sin capa (None es el del titular, como en buscador.puntuador_para)."""
    if perfil["datos"].get("config") == "global":
        return None
    return perfiles.puntuador_de(perfil, config_global)


def _positivas_para_proteger(perfil: dict, config_global: dict, umbral: float, pesos) -> list[tuple[float, set[str]]]:
    import almacen
    import buscador

    chat_id = perfil["chat_id"]
    base = _puntuador_base(perfil, config_global)
    salida = []
    for oferta in almacen.ofertas_por_url(perfiles.urls_positivas(chat_id), chat_id=chat_id).values():
        anos = buscador.extraer_anos_experiencia(oferta.get("description"))
        _, motivos = buscador.explicar_relevancia(oferta.get("title"), oferta.get("location"), anos,
                                                  oferta.get("description"), base, oferta)
        suma = sum(p for _, p in motivos)
        if round(suma, 1) < umbral:
            continue  # ya cae sin la capa: es cosa del juego, no de lo aprendido
        r = rasgos(oferta.get("title"), oferta.get("company"), datos_de_rasgos(oferta, anos))
        if any(x in pesos for x in r):
            salida.append((suma, r))
    return salida


def estado(perfil: dict, config_global: dict) -> dict:
    """
    {activa, etiquetas, motivo, capa}: lo que hay que saber de la capa de un perfil para puntuar y para
    enseñarla. `capa` va también cuando no está activa (pocas etiquetas, apagada) para poder mostrar
    qué se aprendería; puntuar solo se hace con `activa`.
    """
    chat_id = perfil["chat_id"]
    clave = (perfiles.firma_de_marcas(chat_id), perfil.get("version"), perfiles._firma_de_config(config_global))
    with _lock:
        en_cache = _CACHE.get(chat_id)
        if en_cache is not None and en_cache[0] == clave:
            return en_cache[1]
    ajustes_ = ajustes(perfil)
    lista = etiquetas_de(chat_id, ajustes_)
    pesos, _ = aprender(lista, ajustes_["olvidados"])
    umbral = float(perfiles.config_efectiva(perfil, config_global).get("umbral") or 5.0)
    protegidos: list[str] = []
    if pesos:
        protegidas = proteger(pesos, _positivas_para_proteger(perfil, config_global, umbral, pesos), umbral)
        protegidos = sorted(r for r in pesos if protegidas.get(r, 0.0) != pesos[r])
        pesos = protegidas
    n = len(lista)
    if ajustes_["apagada"]:
        motivo = "apagada"
    elif n < MIN_ETIQUETAS:
        motivo = "pocas"
    elif not pesos:
        motivo = "nada"
    else:
        motivo = None
    resultado = {"activa": motivo is None, "etiquetas": n, "motivo": motivo,
                 "capa": Capa(pesos, n, protegidos)}
    # Otra vez: la primera pasada completa los rasgos de las etiquetas viejas en `extra`, y eso
    # cambia la firma de sus marcas. Con la de antes, la siguiente llamada lo recalcularía todo.
    clave = (perfiles.firma_de_marcas(chat_id), perfil.get("version"), perfiles._firma_de_config(config_global))
    with _lock:
        _CACHE[chat_id] = (clave, resultado)
    return resultado


def capa_de(perfil: dict, config_global: dict) -> Capa | None:
    """La capa con la que se puntúa a este perfil, o None si no tiene (y entonces nada cambia)."""
    if perfil.get("chat_id") is None:
        return None
    # Por debajo de MIN_ETIQUETAS no hay nada que calcular: ni se aprende ni se completan los rasgos de las
    # etiquetas viejas (que es una escritura). Contar es una consulta; «Lo aprendido» sí lo calcula,
    # para enseñar qué se aprendería.
    ajustes_ = ajustes(perfil)
    if ajustes_["apagada"] or len({e["url"] for e in perfiles.etiquetas(perfil["chat_id"])
                                   if not ajustes_["desde"] or (e.get("fecha") or "") > ajustes_["desde"]}) < MIN_ETIQUETAS:
        return None
    resultado = estado(perfil, config_global)
    return resultado["capa"] if resultado["activa"] else None


def olvidar_cache(chat_id: int | None = None) -> None:
    with _lock:
        if chat_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(chat_id, None)


def separar(motivos) -> tuple[float, float]:
    """(lo que puntúa sin la capa, lo que pone la capa) de unos motivos guardados o recién calculados."""
    capa = 0.0
    total = 0.0
    for motivo in motivos:
        if isinstance(motivo, (list, tuple)):
            nombre, peso = motivo
        else:  # el texto guardado: «lo aprendido (soc) (+1,2)»
            nombre, _, cola = str(motivo).rpartition(" (")
            try:
                peso = float(cola.rstrip(")").replace(",", "."))
            except ValueError:
                continue
        total += peso
        if str(nombre).startswith("lo aprendido"):
            capa += peso
    return round(total - capa, 1), round(capa, 1)


# ----------------------------------------------------------------------
# Vigilancia (§5.4): se apaga sola si empeora, pero no por ruido
# ----------------------------------------------------------------------
# Con ~25 avisos por semana (lo que MP4 predijo y el 03-11 confirmó), una precisión semanal suelta
# tiene ±9-10 puntos de ruido: un apagado «si empeora dos semanas seguidas» saltaría por una oferta.
# Pero con y sin capa se miden sobre LAS MISMAS etiquetas, así que solo difieren en los casos en que
# la capa cambió la decisión. Una semana cuenta solo si tiene bastantes de las dos cosas.
MIN_RESPONDIDOS_SEMANA = 12
MIN_CAMBIADOS_SEMANA = 3
POSITIVAS_DE_AVISO = ("favorito", "aplicada", "encaja")
_MEDIDAS: set[tuple[int, str]] = set()  # (chat_id, lunes) ya medidos en este proceso


def _lunes(fecha: datetime) -> datetime:
    return (fecha - timedelta(days=fecha.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def medir_semana(chat_id: int, umbral: float, lunes: datetime) -> dict:
    """La semana que empieza ese lunes: precisión con y sin capa sobre sus respondidos."""
    desde, hasta = lunes.isoformat(timespec="seconds"), (lunes + timedelta(days=7)).isoformat(timespec="seconds")
    respondidos = [a for a in perfiles.avisos_entre(chat_id, desde, hasta) if a.get("respuesta")]
    motivos = perfiles.motivos_guardados(chat_id, [a["url"] for a in respondidos])
    con_sel = sin_sel = cambiados = 0
    con_pos = sin_pos = 0
    for a in respondidos:
        con = a.get("relevancia")
        if con is None:
            continue
        _, capa = separar(motivos.get(a["url"], []))
        sin = round(con - capa, 1)
        positivo = a["respuesta"] in POSITIVAS_DE_AVISO
        if con >= umbral:
            con_sel += 1
            con_pos += positivo
        if sin >= umbral:
            sin_sel += 1
            sin_pos += positivo
        cambiados += (con >= umbral) != (sin >= umbral)
    p_con = round(100 * con_pos / con_sel, 1) if con_sel else None
    p_sin = round(100 * sin_pos / sin_sel, 1) if sin_sel else None
    valida = len(respondidos) >= MIN_RESPONDIDOS_SEMANA and cambiados >= MIN_CAMBIADOS_SEMANA
    return {"semana": desde[:10], "respondidos": len(respondidos), "cambiados": cambiados,
            "con": p_con, "sin": p_sin, "valida": valida,
            "peor": bool(valida and p_con is not None and p_sin is not None and p_con < p_sin)}


def vigilar(perfil: dict, config_global: dict, ahora: datetime | None = None) -> dict | None:
    """
    Una vez por semana y perfil (la primera corrida tras el lunes): mide la semana anterior y la
    apunta. Si las dos últimas semanas que cuentan son seguidas y las dos peores con capa, la apaga.
    Devuelve la medida si ha medido algo (y `apagada` si la ha apagado).
    """
    chat_id = perfil["chat_id"]
    ahora = ahora or datetime.now()
    anterior = _lunes(ahora) - timedelta(days=7)
    ajustes_ = ajustes(perfil)
    semanas = ajustes_.get("semanas") or []
    clave = (chat_id, anterior.date().isoformat())
    with _lock:
        if clave in _MEDIDAS or (semanas and semanas[-1]["semana"] >= clave[1]):
            return None
        _MEDIDAS.add(clave)
    umbral = float(perfiles.config_efectiva(perfil, config_global).get("umbral") or 5.0)
    medida = medir_semana(chat_id, umbral, anterior)
    if not medida["valida"]:
        # Solo se guardan las semanas que cuentan: una que no cuenta no decide nada, y así la base no
        # se escribe cada lunes por nada. Que se mida otra vez tras un reinicio cuesta una consulta.
        return medida
    semanas = (semanas + [medida])[-8:]
    validas = [s for s in semanas if s["valida"]]
    apagar = False
    if not ajustes_["apagada"] and len(validas) >= 2:
        a, b = validas[-2:]
        seguidas = (datetime.fromisoformat(b["semana"]) - datetime.fromisoformat(a["semana"])).days == 7
        apagar = seguidas and a["peor"] and b["peor"]

    def cambiar(actual):
        actual["semanas"] = semanas
        if apagar:
            actual["apagada"] = {"fecha": ahora.isoformat(timespec="seconds"),
                                 "motivo": "dos semanas seguidas con menos precisión que sin ella"}
    cambiar_ajustes(chat_id, cambiar)
    return {**medida, "apagada": apagar}


# ----------------------------------------------------------------------
# Descubrimiento (D10): ofertas del borde, para aprender también cerca del umbral
# ----------------------------------------------------------------------
DESCUBRIMIENTO_AL_DIA = 2


def descubrimiento_activo(perfil: dict) -> bool:
    return (perfil.get("datos", {}).get("avisos") or {}).get("descubrimiento", True) is not False


def quedan_hoy(chat_id: int, ahora: datetime | None = None) -> int:
    hoy = (ahora or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    return max(0, DESCUBRIMIENTO_AL_DIA - perfiles.contar_avisos(chat_id, "descubrimiento", hoy))


def elegir_descubrimiento(candidatas: list[tuple[dict, float, list]], umbral: float, cuantas: int) -> list[dict]:
    """
    De (oferta, relevancia, motivos) ya filtradas (no avisadas, no repetidas), las del borde
    [umbral − 1, umbral). Primero las que la capa ha bajado del umbral: si no, lo que la capa quita no
    se vería nunca y la vigilancia solo podría juzgar lo que añade. Luego las más altas.
    """
    borde = []
    for oferta, relevancia, motivos in candidatas:
        if not (umbral - 1 <= relevancia < umbral):
            continue
        sin, _ = separar(motivos)
        bajada = sin >= umbral
        borde.append((0 if bajada else 1, -relevancia, str(oferta.get("job_url")), oferta))
    borde.sort(key=lambda x: x[:3])
    return [x[3] for x in borde[:max(0, cuantas)]]


# ----------------------------------------------------------------------
# Sugerencias de fondo (§5.4): no se aplican solas
# ----------------------------------------------------------------------
DIAS_ENTRE_SUGERENCIAS = 7
DIAS_DE_MOTIVOS = 14
VECES_PARA_SUGERIR = 3
MOTIVO_EXPERIENCIA = "pide experiencia o es senior"
MOTIVO_UBICACION = "ubicación o modalidad"
MOTIVO_EMPRESA = "la empresa"


def _clave_de_config(perfil: dict) -> str:
    datos = perfil["datos"]
    if datos.get("config") == "global":
        return "global"
    if datos.get("reglas") == "catalogo":
        return "plan"
    return "bloque"


def candidata(perfil: dict, config_global: dict, ahora: datetime | None = None) -> dict | None:
    """
    La sugerencia que tocaría ofrecer, sin mirar aún si quitaría una ⭐ (eso es `segura`): 3 «pide
    experiencia» en 14 días → bajar el tope un año; 3 «ubicación o modalidad» → menos radio; 3 «la
    empresa» de la misma → excluirla. Como mucho una por semana, y nunca la misma que ya se rechazó.
    """
    ahora = ahora or datetime.now()
    ajustes_ = ajustes(perfil)
    ultima = ajustes_.get("ultima_sugerencia")
    if ultima and datetime.fromisoformat(ultima) > ahora - timedelta(days=DIAS_ENTRE_SUGERENCIAS):
        return None
    if ajustes_.get("sugerencia"):
        return None
    # Aceptada o rechazada, no se vuelve a ofrecer; la que no era segura (quitaba una ⭐), solo se
    # aparta una semana: con marcas nuevas puede dejar de quitarla.
    hace_una_semana = (ahora - timedelta(days=DIAS_ENTRE_SUGERENCIAS)).isoformat(timespec="seconds")
    ya = {(s.get("clave"), str(s.get("valor"))) for s in ajustes_.get("sugerencias") or []
          if s.get("resultado") != "no_segura" or (s.get("fecha") or "") >= hace_una_semana}
    # Los motivos de antes del corte (un cambio de sector, empezar de cero) hablan de otro perfil.
    corte = max((ahora - timedelta(days=DIAS_DE_MOTIVOS)).isoformat(timespec="seconds"), ajustes_["desde"] or "")
    ocultas = [m for m in perfiles.etiquetas(perfil["chat_id"])
               if m["accion"] == "oculta" and (m.get("fecha") or "") > corte]
    efectiva = perfiles.config_efectiva(perfil, config_global)
    propuestas = []
    tope = efectiva.get("anos_experiencia_max")
    if (sum(1 for m in ocultas if m.get("motivo") == MOTIVO_EXPERIENCIA) >= VECES_PARA_SUGERIR
            and isinstance(tope, int) and tope > 0):
        propuestas.append({"tipo": "experiencia", "clave": "anos_experiencia_max", "antes": tope, "valor": tope - 1,
                           "texto": f"Varias veces me has dicho que piden demasiada experiencia. ¿Bajo tu tope de "
                                    f"años exigidos de {tope} a {tope - 1}?"})
    radio = efectiva.get("max_distancia_km")
    if (sum(1 for m in ocultas if m.get("motivo") == MOTIVO_UBICACION) >= VECES_PARA_SUGERIR
            and isinstance(radio, (int, float)) and radio > 2):
        nuevo = max(1, round(radio * 0.75))
        propuestas.append({"tipo": "radio", "clave": "max_distancia_km", "antes": radio, "valor": nuevo,
                           "texto": f"Varias veces me has dicho que la ubicación no te va. ¿Bajo la distancia "
                                    f"máxima de las presenciales de {radio:g} a {nuevo:g} km?"})
    empresas = Counter(normalizar_empresa(m.get("empresa")) for m in ocultas
                       if m.get("motivo") == MOTIVO_EMPRESA and normalizar_empresa(m.get("empresa")))
    excluidas = [normalizar_empresa(e) for e in efectiva.get("empresas_excluidas") or []]
    for empresa, veces in empresas.most_common():
        if veces >= VECES_PARA_SUGERIR and empresa not in excluidas:
            original = next(m.get("empresa") for m in ocultas if normalizar_empresa(m.get("empresa")) == empresa)
            propuestas.append({"tipo": "empresa", "clave": "empresas_excluidas", "antes": None, "valor": original,
                               "texto": f"Has descartado {veces} ofertas de {original} por la empresa. "
                                        "¿Dejo de enseñarte las suyas?"})
            break
    for p in propuestas:
        if (p["clave"], str(p["valor"])) not in ya:
            p["id"] = hashlib.sha1(f"{p['clave']}={p['valor']}@{ahora:%Y%m%d}".encode()).hexdigest()[:6]
            return p
    return None


def config_con(perfil: dict, config_global: dict, sugerencia: dict) -> dict:
    """La config efectiva que tendría el perfil con la sugerencia aplicada (para medir antes)."""
    efectiva = perfiles.config_efectiva(perfil, config_global)
    if sugerencia["clave"] == "empresas_excluidas":
        efectiva["empresas_excluidas"] = list(efectiva.get("empresas_excluidas") or []) + [sugerencia["valor"]]
    else:
        efectiva[sugerencia["clave"]] = sugerencia["valor"]
    return efectiva


def efecto(perfil: dict, config_global: dict, sugerencia: dict, urls) -> dict:
    """
    De esas ofertas del perfil, cuáles dejaría fuera la sugerencia (por los filtros o por bajar del
    umbral): {fuera: [url], de: n}. Es lo que decide si se ofrece (`segura`) y lo que se le enseña.
    """
    import almacen
    import buscador
    import pandas as pd

    chat_id = perfil["chat_id"]
    umbral = float(perfiles.config_efectiva(perfil, config_global).get("umbral") or 5.0)
    nueva = config_con(perfil, config_global, sugerencia)
    actual_p = _puntuador_base(perfil, config_global)
    nuevo_p = buscador.crear_puntuador(nueva)
    ofertas = list(almacen.ofertas_por_url(urls, chat_id=chat_id).values())
    fuera = []
    if not ofertas:
        return {"fuera": fuera, "de": 0}
    df = pd.DataFrame(ofertas)
    for columna in ("company", "title", "location", "description", "job_url", "site", "is_remote"):
        if columna not in df.columns:
            df[columna] = None
    if sugerencia["clave"] == "empresas_excluidas":
        quedan = set(buscador.filtrar_empresas_excluidas(df, nueva)["job_url"])
    elif sugerencia["clave"] == "max_distancia_km":
        quedan = set(buscador.filtrar_presenciales_lejos(df, nueva)["job_url"])
    else:
        quedan = set(df["job_url"])
        if nueva.get("experiencia_estricta"):
            quedan = set(buscador.filtrar_por_experiencia_exigida(df, nueva)["job_url"])
    for oferta in ofertas:
        url = oferta["job_url"]
        anos = buscador.extraer_anos_experiencia(oferta.get("description"))
        args = (oferta.get("title"), oferta.get("location"), anos, oferta.get("description"))
        antes = buscador.explicar_relevancia(*args, actual_p, oferta)[0]
        if antes < umbral:
            continue  # hoy ya no llegaría: la sugerencia no le quita nada
        despues = buscador.explicar_relevancia(*args, nuevo_p, oferta)[0]
        if url not in quedan or despues < umbral:
            fuera.append(url)
    return {"fuera": fuera, "de": len(ofertas)}


def segura(perfil: dict, config_global: dict, sugerencia: dict) -> bool:
    """Solo se ofrece si, aplicada, no deja fuera ninguna ✅ o ⭐ del perfil que hoy le llega."""
    return not efecto(perfil, config_global, sugerencia, perfiles.urls_positivas(perfil["chat_id"]))["fuera"]


def aplicar_sugerencia(perfil: dict, sugerencia: dict) -> None:
    """A su config, donde viva (config.json para el titular, su bloque, o su plan), y a su historial."""
    chat_id = perfil["chat_id"]
    clave, valor = sugerencia["clave"], sugerencia["valor"]
    donde = _clave_de_config(perfil)
    if donde == "global":
        actual = perfiles._leer_origen(perfiles.ruta().parent / "config.json", dict, {})
        nuevo = (list(actual.get(clave) or []) + [valor]) if clave == "empresas_excluidas" else valor
        perfiles.escribir_claves_de_config({clave: nuevo})

    def cambiar(datos):
        if donde == "bloque":
            bloque = datos.setdefault("config", {})
            bloque[clave] = (list(bloque.get(clave) or []) + [valor]) if clave == "empresas_excluidas" else valor
        elif donde == "plan":
            plan = datos.setdefault("plan", {})
            if clave == "anos_experiencia_max":
                plan["experiencia_max_exigida"] = valor
                if "experiencia" in datos:  # las respuestas de la encuesta (MP8), para que regenerar no lo deshaga
                    datos["experiencia_max_exigida"] = valor
            elif clave == "max_distancia_km":
                plan.setdefault("area", {})["radio_km"] = valor
                if isinstance(datos.get("ubicacion"), dict):
                    datos["ubicacion"]["radio_km"] = valor
            else:
                plan["empresas_excluidas"] = list(plan.get("empresas_excluidas") or []) + [valor]
                if "puestos" in datos:
                    datos["empresas_excluidas"] = list(datos.get("empresas_excluidas") or []) + [valor]
        aj = datos.setdefault("ajustes_aprendidos", {})
        aj["cambios"] = (aj.get("cambios") or []) + [{
            "fecha": perfiles._ahora(), "que": clave, "antes": sugerencia.get("antes"), "despues": valor}]
    perfiles.actualizar_datos(chat_id, cambiar)
