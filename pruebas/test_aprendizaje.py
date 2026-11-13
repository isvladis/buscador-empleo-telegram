# -*- coding: utf-8 -*-
"""
Pruebas de la capa aprendida de MP6 (aprendizaje.py): etiquetas, pesos y sus topes, la protección de
las ✅ y ⭐, el aislamiento entre perfiles, la vigilancia con su mínimo de muestra, olvidar y empezar de
cero, las ofertas de descubrimiento, las sugerencias de fondo y lo que se ve en el bot.

Ninguna prueba llama a Telegram ni a ningún portal: requests.post es un espía. Los chat_id son
inventados. Las de lo importante llevan su control negativo: se quita el arreglo y tiene que fallar.

    python pruebas/test_aprendizaje.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
DATOS = Path(tempfile.mkdtemp(prefix="test-aprendizaje-"))
TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"
TITULAR, A, B = 1111111, 2222222, 3333333
os.environ.update(JOBSPY_DATA_DIR=str(DATOS), JOBSPY_TELEGRAM_TOKEN=TOKEN, JOBSPY_TELEGRAM_CHAT_ID=str(TITULAR))
os.environ.pop("JOBSPY_TELEGRAM_CHAT_IDS_EXTRA", None)
(DATOS / "config.json").write_text(json.dumps({
    "_config_version": 2, "max_perfiles": 3, "busqueda_automatica_activa": False, "hours_old": 38,
    "telegram_bot_token": "", "telegram_chat_id": ""}, indent=2), encoding="utf-8")

import requests  # noqa: E402

import aprendizaje  # noqa: E402
import app  # noqa: E402
import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import notificaciones  # noqa: E402
import perfiles  # noqa: E402
import telegram_bot as bot  # noqa: E402
import telegram_botones as tb  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


class Resp:
    def __init__(self, datos):
        self._datos, self.status_code, self.ok = datos, 200, True
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


class Espia:
    def __init__(self):
        self.llamadas: list[dict] = []
        self.n = 100

    def post(self, url, json=None, timeout=None, data=None, files=None):  # noqa: A002
        metodo = url.rsplit("/", 1)[-1]
        self.llamadas.append({"metodo": metodo, "cuerpo": json if json is not None else dict(data or {})})
        if metodo == "sendMessage":
            self.n += 1
            return Resp({"ok": True, "result": {"message_id": self.n}})
        if metodo == "getMe":
            return Resp({"ok": True, "result": {"username": "bot_de_prueba"}})
        return Resp({"ok": True, "result": True})

    def textos(self, chat):
        return [c["cuerpo"].get("text", "") for c in self.llamadas
                if c["metodo"] in ("sendMessage", "editMessageText") and str(c["cuerpo"].get("chat_id")) == str(chat)]

    def teclados(self, chat):
        return [c["cuerpo"].get("reply_markup") for c in self.llamadas
                if c["metodo"] in ("sendMessage", "editMessageText") and str(c["cuerpo"].get("chat_id")) == str(chat)]


espia = Espia()
requests.post = espia.post
notificaciones.time.sleep = lambda *_: None


def config() -> dict:
    return config_mod.cargar_config()


def botones(teclado) -> list[str]:
    return [b["callback_data"] for fila in (teclado or {}).get("inline_keyboard", []) for b in fila]


def mensaje(texto, chat, n=[0]):
    n[0] += 1
    return {"update_id": n[0], "message": {"message_id": n[0], "text": texto,
                                           "chat": {"id": chat, "type": "private"}, "from": {"id": chat}}}


def pulsacion(datos, chat, n=[5000]):
    n[0] += 1
    return {"update_id": n[0], "callback_query": {"id": "cb%d" % n[0], "data": datos, "from": {"id": chat},
                                                  "message": {"message_id": 77, "chat": {"id": chat, "type": "private"}}}}


def escribe(texto, chat):
    return bot.procesar(mensaje(texto, chat), config())


def pulsa(datos, chat):
    return bot.procesar(pulsacion(datos, chat), config())


# ----------------------------------------------------------------------
# Ofertas y etiquetas sembradas
# ----------------------------------------------------------------------
_N = [0]


def oferta(titulo, empresa, sitio="linkedin", anos=None, horas=1):
    _N[0] += 1
    cuando = (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")
    texto = "Puesto en Barcelona, trabajo en equipo y formación."
    if anos:
        texto += f" Se requieren {anos} años de experiencia."
    return {"job_url": f"https://www.{sitio}.com/jobs/view/{_N[0]}", "title": titulo, "company": empresa,
            "location": "Barcelona, Catalonia, Spain", "site": sitio, "is_remote": False, "description": texto,
            "relevancia": 6.0, "motivos_relevancia": [["motivo", 6.0]],
            "visto_por_primera_vez": cuando, "visto_por_ultima_vez": cuando}


def enlazar(chat, ofertas_):
    perfiles.aplicar_cambios(chat, escribir=[(o, 0, i) for i, o in enumerate(ofertas_)])


def etiqueta(chat, titulo, empresa, accion, sitio="linkedin", motivo=None, fecha=None, anos=None):
    """Una marca puesta con un botón de Telegram, como la deja telegram_botones (con sus rasgos)."""
    o = oferta(titulo, empresa, sitio, anos)
    enlazar(chat, [o])
    extra = {"origen": "telegram", "rasgos": aprendizaje.datos_de_rasgos(o)}
    if motivo:
        extra["motivo"] = motivo
    perfiles.fijar_marca(chat, o["job_url"], accion, {"title": titulo, "company": empresa}, extra)
    if fecha:
        perfiles.conexion().execute("UPDATE marcas SET fecha = ? WHERE chat_id = ? AND url = ?", (fecha, chat, o["job_url"]))
    return o


def puntua(perfil, o, puntuador="perfil"):
    if puntuador == "perfil":
        puntuador = buscador.puntuador_para(perfil, config())
    anos = buscador.extraer_anos_experiencia(o.get("description"))
    return buscador.explicar_relevancia(o["title"], o["location"], anos, o["description"], puntuador, o)


def fresco(chat):
    aprendizaje.olvidar_cache()
    return perfiles.obtener(chat)


# ----------------------------------------------------------------------
def caso_umbral_y_topes():
    print("\n[1] Menos de %d etiquetas no cambia nada; los topes por rasgo y por oferta"
          % aprendizaje.MIN_ETIQUETAS)
    perfiles.guardar(A, perfiles.datos_de_plantilla(), "activo")
    perfil = fresco(A)
    prueba = oferta("Técnico de Soporte", "Mala SL", "jooble")
    enlazar(A, [prueba])
    antes = puntua(perfil, prueba, perfiles.puntuador_de(perfil, config()))
    # Se queda a UNA del umbral, sea el que sea: así la prueba sigue valiendo si el umbral cambia
    # (subió de 15 a 30 el 04-11) y lo que comprueba es el comportamiento, no el número.
    faltan = aprendizaje.MIN_ETIQUETAS - 1
    for i in range(faltan // 2):
        etiqueta(A, "Analista SOC Junior", "Buena SA", "favorito")
        etiqueta(A, "Técnico de Soporte", "Mala SL", "oculta", sitio="jooble")
    if faltan % 2:
        etiqueta(A, "Técnico de Soporte", "Mala SL", "oculta", sitio="jooble")
    # Las marcas sin origen son las de la web (barridos en lote): no son etiquetas.
    for i in range(3):
        o = oferta("Analista SOC Junior", "Buena SA")
        enlazar(A, [o])
        perfiles.fijar_marca(A, o["job_url"], "oculta", {"title": o["title"], "company": o["company"]})
    perfil = fresco(A)
    comprueba(len(perfiles.etiquetas(A)) == faltan, "las 3 marcas sin origen (la web) no son etiquetas")
    comprueba(aprendizaje.capa_de(perfil, config()) is None, "con %d etiquetas no hay capa" % faltan)
    comprueba(buscador.puntuador_para(perfil, config()) is perfiles.puntuador_de(perfil, config()),
              "   y el puntuador es el de siempre, el mismo objeto")
    comprueba(puntua(perfil, prueba) == antes, "   y la nota y los motivos, idénticos (%s)" % (antes[0],))
    comprueba(all("rasgos" in e for e in perfiles.etiquetas(A)), "   las etiquetas llevan sus rasgos al pulsar")
    etiqueta(A, "Analista SOC Junior", "Buena SA", "favorito")
    etiqueta(A, "Técnico Rara", "Rara SL", "oculta")
    etiqueta(A, "Técnico Rara", "Rara SL", "oculta")
    perfil = fresco(A)
    capa = aprendizaje.capa_de(perfil, config())
    comprueba(capa is not None and capa.n_etiquetas == faltan + 3,
              "con %d, la capa se pone (%s)" % (faltan + 3, capa and capa.n_etiquetas))
    comprueba("e:rara sl" not in capa.pesos, "un rasgo visto 2 veces no pesa")
    comprueba("e:mala sl" in capa.pesos and capa.pesos["e:mala sl"] < 0 and capa.pesos["e:buena sa"] > 0,
              "   y uno visto 7 veces sí, con su signo")
    comprueba(all(abs(p) <= aprendizaje.TOPE_RASGO for p in capa.pesos.values()), "ningún rasgo pasa de ±1,5")
    crudo = sum(capa.pesos.get(r, 0) for r in capa.rasgos_de(prueba["title"], prueba["location"],
                                                              prueba["description"], prueba, None))
    nota, motivos = puntua(perfil, prueba)
    aprendido = [p for n, p in motivos if n.startswith("lo aprendido")]
    comprueba(crudo < -aprendizaje.TOPE_OFERTA and aprendido == [-3.0],
              "una oferta con todo en contra se queda en −3,0 (sin tope sería %.1f)" % crudo)
    comprueba(nota == round(antes[0] - 3.0, 1), "   y entra como un motivo más: %s → %s" % (antes[0], nota))
    nombre = [n for n, _ in motivos if n.startswith("lo aprendido")][0]
    comprueba(nombre.startswith("lo aprendido (") and nombre.count(",") == 1, "   con sus dos rasgos que más pesan: «%s»" % nombre)


def caso_proteccion():
    print("\n[2] Protección: una ✅ nunca queda bajo el umbral por la capa")
    protegida = etiqueta(A, "Técnico de Soporte", "Mala SL", "aplicada", sitio="jooble")
    perfil = fresco(A)
    base = puntua(perfil, protegida, perfiles.puntuador_de(perfil, config()))[0]
    comprueba(5.0 <= base < 8.0, "la ✅ llega sin capa (%.1f) y la capa podría hundirla" % base)
    con = puntua(perfil, protegida)[0]
    comprueba(con >= 5.0, "con la capa sigue llegando (%.1f)" % con)
    capa = aprendizaje.capa_de(perfil, config())
    comprueba(bool(capa.protegidos), "   porque se han rebajado sus rasgos: %s" % capa.protegidos[:4])
    original = aprendizaje.proteger
    aprendizaje.proteger = lambda pesos, positivas, umbral: dict(pesos)
    try:
        sin_proteccion = puntua(fresco(A), protegida)[0]
    finally:
        aprendizaje.proteger = original
    comprueba(sin_proteccion < 5.0, "control: sin la protección caería (%.1f)" % sin_proteccion)
    prueba = oferta("Técnico de Soporte", "Mala SL", "jooble")
    enlazar(A, [prueba])
    comprueba(puntua(fresco(A), prueba)[0] < base, "   y lo que no es una ✅ sigue bajando")


def caso_aislamiento():
    print("\n[3] Un perfil no aprende de las etiquetas de otro")
    perfil_a = fresco(A)
    pesos_a = dict(aprendizaje.capa_de(perfil_a, config()).pesos)
    perfiles.guardar(B, perfiles.datos_de_plantilla(), "activo")
    # Pasa del umbral sea el que sea (subió de 15 a 30 el 04-11): a B le hacen falta sus propias
    # etiquetas para tener capa, y de aquí las heredan los casos [5] y [6].
    for _ in range(aprendizaje.MIN_ETIQUETAS // 2 + 1):
        etiqueta(B, "Técnico de Soporte", "Mala SL", "favorito", sitio="jooble")
        etiqueta(B, "Analista SOC Junior", "Buena SA", "oculta")
    pesos_b = aprendizaje.capa_de(fresco(B), config()).pesos
    comprueba(pesos_b["e:mala sl"] > 0 and pesos_b["e:buena sa"] < 0, "B aprende lo contrario que A")
    comprueba(aprendizaje.capa_de(fresco(A), config()).pesos == pesos_a, "   y la capa de A no se mueve")
    original = perfiles.etiquetas

    def sin_chat_id(chat_id):
        return [{"url": u, "accion": a, **perfiles._columnas_a_info(*c)} for u, a, *c in perfiles.conexion().execute(
            "SELECT url, accion, fecha, origen, motivo, titulo, empresa, extra FROM marcas WHERE origen = 'telegram'")]
    perfiles.etiquetas = sin_chat_id
    try:
        mezclada = aprendizaje.capa_de(fresco(A), config()).pesos
    finally:
        perfiles.etiquetas = original
    comprueba(mezclada != pesos_a, "control: si se leyeran las etiquetas sin chat_id, A cambiaría")
    perfil_b = fresco(B)
    aprendizaje.cambiar_ajustes(B, lambda a: a.update(olvidados=["e:mala sl"]))
    comprueba("e:mala sl" in aprendizaje.capa_de(fresco(A), config()).pesos, "lo que olvida B no lo olvida A")
    aprendizaje.cambiar_ajustes(B, lambda a: a.update(olvidados=[]))
    comprueba(perfil_b["chat_id"] == B, "   (y B sigue siendo B)")


def caso_olvidar_y_cero():
    print("\n[4] Olvidar un rasgo y empezar de cero")
    prueba = oferta("Técnico de Soporte", "Mala SL", "jooble")
    enlazar(A, [prueba])
    perfil = fresco(A)
    sin_capa = puntua(perfil, prueba, perfiles.puntuador_de(perfil, config()))
    espia.llamadas.clear()
    comprueba(escribe("/ajustes", A) == "comando:ajustes", "/ajustes contesta")
    comprueba({"aj1:av", "aj1:la"} <= set(botones(espia.teclados(A)[-1])), "   con Avisos y Lo aprendido")
    comprueba(pulsa("aj1:la", A) == "boton:aj1:lo_aprendido", "Lo aprendido se abre")
    texto, teclado = espia.textos(A)[-1], espia.teclados(A)[-1]
    comprueba("empresa mala sl" in texto and "Lo que más baja" in texto, "   enseña lo que más pesa")
    olvidar = [d for d in botones(teclado) if d.startswith("aj1:ol:")]
    comprueba(0 < len(olvidar) <= 2 * aprendizaje.EN_LO_APRENDIDO, "   con un ➖ Olvidar por rasgo (%d)" % len(olvidar))
    comprueba(all(len(d.encode()) <= 64 for d in botones(teclado)), "   y ningún botón pasa de 64 bytes")
    pulsa("aj1:ol:" + aprendizaje.huella("e:mala sl"), A)
    capa = aprendizaje.capa_de(fresco(A), config())
    comprueba("e:mala sl" not in capa.pesos, "olvidar quita ese rasgo")
    comprueba("e:mala sl" in aprendizaje.ajustes(fresco(A))["olvidados"], "   y queda apuntado")
    caducado = pulsa("aj1:c2:%d" % (bot._minuto() - 60), A)
    comprueba(caducado == "boton:aj1:caducado", "empezar de cero con una confirmación vieja no hace nada")
    pulsa("aj1:c1:%d" % bot._minuto(), A)
    comprueba(pulsa("aj1:c2:%d" % bot._minuto(), A) == "boton:aj1:de_cero", "empezar de cero, confirmado")
    perfil = fresco(A)
    comprueba(aprendizaje.capa_de(perfil, config()) is None, "   ya no hay capa")
    comprueba(puntua(perfil, prueba) == sin_capa, "   y la puntuación es la de antes, exacta (%s)" % (sin_capa[0],))
    comprueba(len(perfiles.etiquetas(A)) > 0, "   sin borrar ninguna marca")


def caso_cambio_de_sector():
    print("\n[5] Las etiquetas de antes de un cambio de sector no cuentan")
    perfiles.guardar(B, {**perfiles.datos_de_plantilla(), "ajustes_aprendidos": {}}, "activo")
    comprueba(aprendizaje.capa_de(fresco(B), config()) is not None, "sin corte, B tiene capa")
    corte = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="seconds")
    aprendizaje.cambiar_ajustes(B, lambda a: a.update(desde=corte))
    perfil = fresco(B)
    comprueba(aprendizaje.etiquetas_de(B, aprendizaje.ajustes(perfil)) == [], "con el corte, ninguna etiqueta cuenta")
    comprueba(aprendizaje.capa_de(perfil, config()) is None, "   ni hay capa")
    comprueba(len(perfiles.etiquetas(B)) >= aprendizaje.MIN_ETIQUETAS,
              "   y las marcas siguen ahí")
    comprueba(aprendizaje.ajustes({"datos": {"reglas": "titular-ciber"}})["desde"] == "2026-11-02T20:17:27",
              "el titular-ciber trae por defecto la fecha del reenfoque")
    comprueba(aprendizaje.ajustes({"datos": {"reglas": "titular-ciber", "ajustes_aprendidos": {"desde": None}}})["desde"]
              == "2026-11-02T20:17:27", "   y un «desde» vacío no la quita")
    comprueba(aprendizaje.ajustes({"datos": {"reglas": "titular-2026-10"}})["desde"] is None,
              "   otro juego no tiene corte")


def caso_vigilancia():
    print("\n[6] Vigilancia: se apaga si empeora dos semanas seguidas, y no por poca muestra")
    perfiles.guardar(B, perfiles.datos_de_plantilla(), "activo")
    perfil = fresco(B)
    hoy = datetime.now()
    lunes = (hoy - timedelta(days=hoy.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)

    def semana(inicio, respondidos, cambiados):
        """`respondidos` avisos de esa semana: los `cambiados` los subió la capa y fueron 🙈; el resto, ⭐."""
        for i in range(respondidos):
            o = oferta("Analista %d" % i, "Empresa %d" % i)
            subida = i < cambiados
            o["motivos_relevancia"] = [["reglas", 4.5 if subida else 6.0]] + ([["lo aprendido (x)", 1.0]] if subida else [])
            enlazar(B, [o])
            clave = tb.clave_de(o["job_url"])
            perfiles.registrar_aviso(B, clave, o["job_url"], relevancia=5.5 if subida else 6.0,
                                     avisada=(inicio + timedelta(days=1, hours=i % 20)).isoformat(timespec="seconds"))
            perfiles.anotar_respuesta(B, clave, "oculta" if subida else "favorito")

    s1, s2, s3 = lunes - timedelta(days=21), lunes - timedelta(days=14), lunes - timedelta(days=7)
    semana(s1, 11, 5)
    m = aprendizaje.vigilar(perfil, config(), ahora=s2 + timedelta(hours=3))
    comprueba(m and not m["valida"] and m["peor"] is False, "11 respondidos no cuentan, aunque sea peor (%s)" % m)
    semana(s2, 14, 2)
    m = aprendizaje.vigilar(fresco(B), config(), ahora=s3 + timedelta(hours=3))
    comprueba(m and not m["valida"], "   ni 2 cambiados por la capa (%s de %s)" % (m and m["cambiados"], m and m["respondidos"]))
    comprueba(not aprendizaje.ajustes(fresco(B)).get("semanas"), "   y las que no cuentan no se escriben")
    semana(s3, 14, 4)
    m = aprendizaje.vigilar(fresco(B), config(), ahora=lunes + timedelta(hours=3))
    comprueba(m and m["valida"] and m["peor"] and not m["apagada"], "una semana peor que cuenta: aún no se apaga")
    comprueba(aprendizaje.vigilar(fresco(B), config(), ahora=lunes + timedelta(hours=5)) is None,
              "   y no se mide dos veces la misma semana")
    semana(lunes, 14, 4)
    m = aprendizaje.vigilar(fresco(B), config(), ahora=lunes + timedelta(days=7, hours=3))
    comprueba(m and m["apagada"], "dos semanas seguidas peores: se apaga (%s %% con, %s %% sin)" % (m["con"], m["sin"]))
    comprueba(aprendizaje.capa_de(fresco(B), config()) is None, "   y deja de puntuar")
    pulsa("aj1:la", B)
    comprueba("apagado" in espia.textos(B)[-1] and "aj1:en" in botones(espia.teclados(B)[-1]),
              "   «Lo aprendido» lo dice y ofrece volver a encenderlo")
    pulsa("aj1:en", B)
    comprueba(aprendizaje.capa_de(fresco(B), config()) is not None, "   y encenderlo la vuelve a poner")
    # Control negativo: sin el mínimo de muestra, la primera semana (11 respondidos) habría contado.
    original = aprendizaje.MIN_RESPONDIDOS_SEMANA, aprendizaje.MIN_CAMBIADOS_SEMANA
    aprendizaje.MIN_RESPONDIDOS_SEMANA = aprendizaje.MIN_CAMBIADOS_SEMANA = 0
    try:
        comprueba(aprendizaje.medir_semana(B, 5.0, s1)["valida"], "control: sin el mínimo, 11 respondidos contarían")
    finally:
        aprendizaje.MIN_RESPONDIDOS_SEMANA, aprendizaje.MIN_CAMBIADOS_SEMANA = original


def caso_descubrimiento():
    print("\n[7] Descubrimiento: como mucho 2 al día, nunca repetidas ni ya avisadas")
    # Con el umbral en 7 el borde es [6, 7): así hay de sobra para elegir con títulos corrientes.
    perfiles.guardar(B, {**perfiles.datos_de_plantilla(), "umbral": 7.0}, "activo")
    perfil = fresco(B)
    titulos = ["Técnico de Sistemas", "Técnico Informático", "Técnico IT", "Administrativo IT",
               "Técnico de Microinformática", "Operador de Sistemas", "Soporte Técnico", "Técnico Helpdesk",
               "Técnico de Redes", "Analista de Sistemas", "Técnico Junior", "Técnico de Campo IT"]
    nuevas = [oferta(t, "Empresa Borde %d" % i) for i, t in enumerate(titulos)]
    enlazar(B, nuevas)
    notas = {o["job_url"]: puntua(perfil, o)[0] for o in nuevas}
    borde = [u for u, n in notas.items() if 6.0 <= n < 7.0]
    comprueba(len(borde) >= 3, "hay al menos 3 en el borde para elegir (%d: %s)" % (len(borde), sorted(notas.values())))
    ya = next(u for u in borde)
    perfiles.registrar_aviso(B, tb.clave_de(ya), ya, tipo="top")
    avisos_antes = perfiles.cifras_de_avisos(B)["semana"]
    espia.llamadas.clear()
    app._notificar_si_alto_encaje(nuevas, perfil, posicion="2/2")
    enviados = [t for t in espia.textos(B) if "¿Te encaja?" in t]
    comprueba(len(enviados) == 2, "una corrida manda 2 (%d)" % len(enviados))
    claves = [d for t in espia.teclados(B) for d in botones(t) if d.startswith("s:")]
    urls = {perfiles.aviso(B, c.split(":")[1])["url"] for c in claves}
    comprueba(urls <= set(borde) and ya not in urls, "   del borde, y no la que ya se le enseñó")
    comprueba(all(perfiles.aviso(B, c.split(":")[1]) for c in claves), "   apuntadas en su índice")
    comprueba(perfiles.contar_avisos(B, "descubrimiento", "2000") == 2, "   como «descubrimiento»")
    altas = len([t for t in espia.teclados(B) if any(d.startswith("f:") for d in botones(t))])
    comprueba(perfiles.cifras_de_avisos(B)["semana"] == avisos_antes + altas,
              "   que no cuentan como avisos (solo cuentan los %d de Encaje alto)" % altas)
    espia.llamadas.clear()
    app._notificar_si_alto_encaje(nuevas, fresco(B), posicion="2/2")
    comprueba(not [t for t in espia.textos(B) if "¿Te encaja?" in t], "la segunda corrida del día no manda más")
    original = aprendizaje.DESCUBRIMIENTO_AL_DIA
    aprendizaje.DESCUBRIMIENTO_AL_DIA = 10
    try:
        espia.llamadas.clear()
        app._notificar_si_alto_encaje(nuevas, fresco(B), posicion="2/2")
        otras = {perfiles.aviso(B, d.split(":")[1])["url"] for t in espia.teclados(B) for d in botones(t) if d.startswith("s:")}
        comprueba(otras and not (otras & urls), "   y con cupo, nunca repite las de antes")
        # Con cupo de sobra se ve que el borde manda: quedan por debajo (2,0, 2,5…) y no se eligen.
        comprueba(otras <= set(borde) and len(urls | otras) <= len(borde),
                  "   ni con cupo sale nada de fuera del borde (%d de %d)" % (len(urls | otras), len(borde)))
        pulsa("aj1:ad:0", B)
        comprueba(not aprendizaje.descubrimiento_activo(fresco(B)), "se apagan en /ajustes → Avisos")
        espia.llamadas.clear()
        app._notificar_si_alto_encaje([oferta("Técnico de Sistemas", "Otra")], fresco(B), posicion="2/2")
        comprueba(not [t for t in espia.textos(B) if "¿Te encaja?" in t], "   y apagadas no llega ninguna")
    finally:
        aprendizaje.DESCUBRIMIENTO_AL_DIA = original
    clave = claves[0].split(":")[1]
    comprueba(pulsa("s:" + clave, B) == "encaja", "👍 se atiende")
    url = perfiles.aviso(B, clave)["url"]
    marca = perfiles.marcas(B)["favoritas"].get(url) or {}
    comprueba(marca.get("origen") == "descubrimiento" and perfiles.aviso(B, clave)["respuesta"] == "encaja",
              "   deja una ⭐ de origen «descubrimiento» y la respuesta «encaja»")
    comprueba(any(e["url"] == url for e in perfiles.etiquetas(B)), "   y es una etiqueta")
    comprueba(pulsa("n:" + claves[1].split(":")[1], B) == "no_encaja", "👎 también")
    comprueba(pulsa("s:" + clave, A) == "desconocida", "un 👍 de B pulsado desde A no encuentra nada")


def caso_sugerencias():
    print("\n[8] Sugerencias: una por semana, y no la que quitaría una ⭐")
    perfiles.guardar(B, perfiles.datos_de_plantilla(), "activo")
    perfiles.conexion().execute("DELETE FROM marcas WHERE chat_id = ?", (B,))
    for _ in range(3):
        etiqueta(B, "Técnico de Sistemas", "Senior SA", "oculta", motivo=aprendizaje.MOTIVO_EXPERIENCIA)
    favorita = etiqueta(B, "Técnico de Soporte", "Buena SA", "favorito", anos=1)
    perfil = fresco(B)
    sug = aprendizaje.candidata(perfil, config())
    comprueba(sug and sug["clave"] == "anos_experiencia_max" and sug["antes"] == 1 and sug["valor"] == 0,
              "3 «pide experiencia» en 14 días proponen bajar el tope (%s)" % (sug and sug["texto"]))
    comprueba(not aprendizaje.segura(perfil, config(), sug), "   pero quitaría la ⭐ que pide 1 año: no es segura")
    espia.llamadas.clear()
    comprueba(bot.ofrecer_sugerencia(perfil, config(), sug, B) is False and not espia.textos(B),
              "   y no se ofrece")
    comprueba(aprendizaje.candidata(fresco(B), config()) is None, "   ni se vuelve a mirar en una semana")
    perfiles.quitar_marcas(B, favorita["job_url"])
    aprendizaje.cambiar_ajustes(B, lambda a: a.update(sugerencias=[]))
    perfil = fresco(B)
    sug = aprendizaje.candidata(perfil, config())
    comprueba(sug and aprendizaje.segura(perfil, config(), sug), "sin esa ⭐, sí es segura")
    espia.llamadas.clear()
    comprueba(bot.ofrecer_sugerencia(perfil, config(), sug, B) is True, "   y se ofrece")
    teclado = espia.teclados(B)[-1]
    comprueba(botones(teclado) == [f"aj1:sg1:{sug['id']}", f"aj1:sg0:{sug['id']}"], "   con Sí y No")
    comprueba(aprendizaje.candidata(fresco(B), config()) is None, "   y no hay otra en una semana")
    comprueba(pulsa("aj1:sg1:zzzzzz", B) == "boton:aj1:sugerencia_vieja", "un id que no es el pendiente no aplica nada")
    comprueba(pulsa(f"aj1:sg1:{sug['id']}", A) == "boton:aj1:sugerencia_vieja", "   ni la de B pulsada desde A")
    comprueba(pulsa(f"aj1:sg1:{sug['id']}", B) == "boton:aj1:sugerencia_aceptada", "aceptarla")
    datos = perfiles.obtener(B)["datos"]
    comprueba(datos["config"]["anos_experiencia_max"] == 0, "   la aplica a su config")
    comprueba(datos["ajustes_aprendidos"]["cambios"][-1]["que"] == "anos_experiencia_max", "   y queda en su historial")
    comprueba(perfiles.config_efectiva(fresco(B), config())["anos_experiencia_max"] == 0, "   y la config efectiva lo ve")
    # Ya no hay ninguna pendiente: lo que impide otra esta semana es solo el enfriamiento.
    for _ in range(3):
        etiqueta(B, "Técnico de Sistemas", "Lejos SA", "oculta", motivo=aprendizaje.MOTIVO_UBICACION)
    comprueba(aprendizaje.candidata(fresco(B), config()) is None,
              "con 3 «ubicación» nuevas, tampoco hay otra hasta que pase la semana")
    dentro_de_8_dias = datetime.now() + timedelta(days=8)
    otra = aprendizaje.candidata(fresco(B), config(), ahora=dentro_de_8_dias)
    comprueba(otra is not None and otra["clave"] == "max_distancia_km",
              "   y pasada la semana, sí (%s)" % (otra and otra["texto"]))
    # Control negativo: sin la guarda, la que quitaba una ⭐ se habría ofrecido.
    original = aprendizaje.segura
    aprendizaje.segura = lambda *a: True
    try:
        favorita = etiqueta(B, "Técnico de Soporte", "Buena SA", "favorito", anos=1)
        aprendizaje.cambiar_ajustes(B, lambda a: a.update(ultima_sugerencia=None, sugerencias=[]))
        perfiles.guardar(B, {**perfiles.obtener(B)["datos"], "config": {"anos_experiencia_max": 1}})
        perfil = fresco(B)
        sug = aprendizaje.candidata(perfil, config())
        comprueba(bot.ofrecer_sugerencia(perfil, config(), sug, B) is True,
                  "control: sin la guarda, se ofrecería la que quita una ⭐")
    finally:
        aprendizaje.segura = original


def caso_resumen_y_ficha():
    print("\n[9] /resumen y la ficha que ve el titular")
    espia.llamadas.clear()
    comprueba(escribe("/resumen", B) == "comando:resumen", "/resumen contesta")
    texto = espia.textos(B)[-1]
    comprueba("Tu semana" in texto and "Precisión" in texto and "👍" in texto, "   con su semana, botones y precisión")
    espia.llamadas.clear()
    pulsa("adm1:fi:" + bot._ref(A), TITULAR)
    ficha = espia.textos(TITULAR)[-1]
    comprueba("Etiquetas (botones):" in ficha, "la ficha de A dice cuántas etiquetas tiene")
    comprueba(not any(x in ficha.lower() for x in ("mala sl", "buena sa", "lo aprendido (", "lo que más")),
              "   y nada de lo que ha aprendido")
    comprueba(pulsa("aj1:la", TITULAR) == "boton:aj1:lo_aprendido" and "mala sl" not in espia.textos(TITULAR)[-1].lower(),
              "«Lo aprendido» del titular no enseña lo de A")
    cmds = [c for c, _ in bot.COMANDOS_DE_TODOS]
    comprueba("resumen" in cmds and "ajustes" in cmds, "los dos comandos están en el menú de todos")


def caso_medir_no_se_mueve():
    print("\n[10] medir.py da su línea base con una base llena de etiquetas")
    entorno = {**os.environ, "JOBSPY_DATA_DIR": str(DATOS), "PYTHONUTF8": "1"}
    salida = subprocess.run([sys.executable, str(AQUI / "medir.py"), "--json"], capture_output=True, text=True,
                            encoding="utf-8", env=entorno)
    base = json.loads((AQUI / "linea_base_mp4.json").read_text(encoding="utf-8"))
    try:
        comprueba(json.loads(salida.stdout) == base, "idéntico a linea_base_mp4.json")
    except json.JSONDecodeError:
        comprueba(False, "medir.py no ha dado JSON: %s" % salida.stderr[-300:])


def main() -> int:
    for caso in (caso_umbral_y_topes, caso_proteccion, caso_aislamiento, caso_olvidar_y_cero, caso_cambio_de_sector,
                 caso_vigilancia, caso_descubrimiento, caso_sugerencias, caso_resumen_y_ficha, caso_medir_no_se_mueve):
        try:
            caso()
        except Exception as e:
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
