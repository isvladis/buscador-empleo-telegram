# -*- coding: utf-8 -*-
"""
Pruebas del evaluador por perfil (MP2): que «qué pasa» y «cuánto puntúa» dependan del perfil.

La equivalencia con el código de antes, sobre los datos reales, es de
pruebas/equivalencia_titular.py. Aquí está lo que no se puede ver con un solo perfil: el futuro
adicional, los dos perfiles de catálogo, el área por distancia, y sobre todo que lo de uno no
llegue a otro (regla 5). Nada sale a la red: el geocodificador es un espía.

    python pruebas/test_evaluador.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-evaluador-")

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import perfiles  # noqa: E402
import reglas  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def plan(nombre: str) -> dict:
    return json.loads((AQUI / "fixtures" / nombre).read_text(encoding="utf-8"))


PLAN_ADMIN = plan("plan_administrativo_valencia.json")
PLAN_MOZO = plan("plan_mozo_zaragoza.json")
CONFIG = dict(config_mod.CONFIG_POR_DEFECTO)
TITULAR = {"chat_id": 1, "version": 1,
           "datos": {"v": 1, "reglas": "titular-2026-10", "config": "global"}}
ADICIONAL = {"chat_id": 2, "version": 1, "datos": {
    "v": 1, "reglas": "titular-2026-10",
    "config": {"anos_experiencia_max": 1, "experiencia_estricta": True}}}


def de_catalogo(chat_id: int, el_plan: dict, version: int = 1) -> dict:
    return {"chat_id": chat_id, "version": version,
            "datos": {"v": 1, "reglas": "catalogo", "plan": el_plan}}


def oferta(titulo, ubicacion="Barcelona, Spain", descripcion=None, **resto) -> dict:
    return {"title": titulo, "location": ubicacion, "description": descripcion, "company": "ACME",
            "job_level": None, "is_remote": None, "ambito": "remoto_es_ue",
            "job_url": "caso://" + titulo, **resto}


def df_de(ofertas: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(ofertas)


class Sitio:
    def __init__(self, lat, lon, tipo="city"):
        self.latitude, self.longitude = lat, lon
        self.raw = {"addresstype": tipo}


class Espia:
    """El geocodificador de verdad, con Nominatim y el reloj cambiados por apuntes."""

    def __init__(self, sitios: dict):
        self.sitios = sitios
        self.preguntas: list[str] = []
        self.esperas = 0

    def consultar(self, direccion):
        self.preguntas.append(direccion)
        resultado = self.sitios.get(direccion)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    def dormir(self, segundos):
        self.esperas += segundos

    def geo(self, tope=None):
        return buscador.Geocodificador(tope=tope, consultar=self.consultar, dormir=self.dormir)


def probar_adicional() -> None:
    print("\n[1] El futuro adicional: su tope y, en lo demás, lo mismo que el titular")
    del_titular = perfiles.config_efectiva(TITULAR, CONFIG)
    del_adicional = perfiles.config_efectiva(ADICIONAL, CONFIG)
    punt_titular = perfiles.puntuador_de(TITULAR, CONFIG)
    punt_adicional = perfiles.puntuador_de(ADICIONAL, CONFIG)
    casos = [
        ("pide 5", "Se requieren 5 años de experiencia en soporte.", False),
        ("pide 2", "Se requieren 2 años de experiencia en soporte.", False),
        ("pide 1", "Se requiere 1 año de experiencia en soporte.", True),
        ("sin experiencia", "No se requiere experiencia.", True),
        ("no lo dice", "Buscamos a alguien con ganas de aprender.", True),
        ("sin descripción", None, True),
    ]
    df = df_de([oferta("Técnico/a Helpdesk N1 " + n, descripcion=d) for n, d, _ in casos])
    salen = buscador.filtrar_por_experiencia_exigida(df, del_adicional)
    for (nombre, _, pasa), titulo in zip(casos, df["title"]):
        comprueba((titulo in set(salen["title"])) == pasa,
                  "%s: %s" % (nombre, "pasa" if pasa else "cae"))
    comprueba(buscador.filtrar_por_experiencia_exigida(df, del_titular) is df,
              "sin estricta devuelve el mismo DataFrame, sin leer nada")

    por_defecto = {"anos_experiencia_max": 1, "experiencia_estricta": True}
    comprueba(del_adicional["reglas_descarte"] == reglas.TITULAR_2026_10.config["reglas_descarte"],
              "lleva las 25 reglas del juego, no las de config.json")
    comprueba(all(del_adicional[k] == v for k, v in por_defecto.items()), "su tope y estricta")

    for titulo, ubicacion in (("Analista SOC Junior", "Barcelona, Spain"),
                              ("Administrador de sistemas", "Barcelona"),
                              ("Técnico/a Soporte Remote User Support", "Madrid")):
        a = buscador.explicar_relevancia(titulo, ubicacion, puntuador=punt_titular)
        b = buscador.explicar_relevancia(titulo, ubicacion, puntuador=punt_adicional)
        # El peso 4,0 del juego es lo único que puede separarlos, y es a propósito.
        sin_sau = [m for m in b[1] if not m[0].startswith("atención al usuario")]
        comprueba([m for m in a[1] if not m[0].startswith("atención al usuario")] == sin_sau,
                  "%s: los mismos motivos" % titulo)
    a = perfiles.config_efectiva(TITULAR, {**CONFIG, "pesos_relevancia": dict(
        reglas.TITULAR_2026_10.config["pesos_relevancia"])})
    comprueba(a["pesos_relevancia"] == del_adicional["pesos_relevancia"],
              "con la config viva, los pesos del juego son los del titular")


def probar_config_efectiva() -> None:
    print("\n[2] config_efectiva: de dónde sale cada clave")
    global_ = perfiles.config_efectiva(TITULAR, CONFIG)
    comprueba({k: global_[k] for k in CONFIG} == CONFIG, "titular: una copia de config.json")
    comprueba(global_["area"] == {"modo": "regex", "nombre": "barcelona"}, "   área regex")
    comprueba(global_["umbral"] == buscador.UMBRAL_ENCAJE_ALTO, "   umbral de hoy")
    comprueba(global_["experiencia_estricta"] is False, "   sin experiencia estricta")
    comprueba(global_["puntuador"] == {"tipo": "reglas", "juego": "titular-2026-10"},
              "   y su juego")

    tocado = {**CONFIG, "hours_old": 7, "busquedas_en_paralelo": 9,
              "categorias": {"otra": {"etiqueta": "x", "activa": True, "terminos": ["cambiado"]}},
              "pesos_relevancia": {"junior o nivel 1": -9.0}, "reglas_descarte": []}
    bloque = {**ADICIONAL, "version": 2, "datos": {**ADICIONAL["datos"], "config": {
        **ADICIONAL["datos"]["config"], "hours_old": 999, "max_distancia_km": 12}}}
    e = perfiles.config_efectiva(bloque, tocado)
    comprueba(e["hours_old"] == 7, "lo del sistema sale de config.json")
    comprueba(e["busquedas_en_paralelo"] == 9, "   aunque el bloque diga otra cosa, se ignora")
    comprueba(e["max_distancia_km"] == 12, "lo personal del bloque manda")
    # Lo que MP4 hará con config.json: si esto fallara, el adicional cambiaría con el titular.
    juego = reglas.TITULAR_2026_10.config
    comprueba(e["categorias"] == juego["categorias"], "cambiar config.json no mueve sus términos")
    comprueba(e["pesos_relevancia"] == juego["pesos_relevancia"], "   ni sus pesos")
    comprueba(len(e["reglas_descarte"]) == 25, "   ni sus reglas de descarte")

    print("\n[2b] El modelo entrenado salió de las marcas del titular: no se reparte")
    con_modelo = {**CONFIG, "usar_modelo_entrenado": True}
    for quien, perfil in (("adicional", {**ADICIONAL, "version": 3}),
                          ("catálogo", de_catalogo(3, PLAN_ADMIN))):
        comprueba(perfiles.config_efectiva(perfil, con_modelo)["usar_modelo_entrenado"] is False,
                  "%s: apagado aunque config.json lo encienda" % quien)
    pedido = {**ADICIONAL, "version": 4, "datos": {**ADICIONAL["datos"], "config": {
        "usar_modelo_entrenado": True}}}
    comprueba(perfiles.config_efectiva(pedido, CONFIG)["usar_modelo_entrenado"] is False,
              "un bloque tampoco puede pedirlo")
    comprueba(perfiles.config_efectiva(TITULAR, con_modelo)["usar_modelo_entrenado"] is True,
              "control: el titular sí lo tiene")

    print("\n[2c] Lo que no tiene sentido es un error, no un perfil a medias")
    for nombre, datos in (("juego desconocido", {"reglas": "no-existe", "config": "global"}),
                          ("sin config", {"reglas": "titular-2026-10"}),
                          ("catálogo sin plan", {"reglas": "catalogo"})):
        try:
            perfiles.config_efectiva({"chat_id": None, "version": 0, "datos": datos}, CONFIG)
            comprueba(False, nombre)
        except ValueError:
            comprueba(True, nombre)


def probar_cache() -> None:
    print("\n[3] La misma oferta, dos perfiles: nada se contamina")
    a = de_catalogo(10, PLAN_ADMIN)
    b = de_catalogo(11, PLAN_MOZO)
    titulo = "Mozo de almacén con carretilla"
    punt_a, punt_b = perfiles.puntuador_de(a, CONFIG), perfiles.puntuador_de(b, CONFIG)
    nota_a = buscador.puntuar_relevancia(titulo, "Zaragoza, Spain", puntuador=punt_a)
    nota_b = buscador.puntuar_relevancia(titulo, "Zaragoza, Spain", puntuador=punt_b)
    comprueba(nota_b >= 5.0 > nota_a, "puntúa distinto (%.1f y %.1f)" % (nota_a, nota_b))
    comprueba(buscador.puntuar_relevancia(titulo, "Zaragoza, Spain", puntuador=punt_a) == nota_a,
              "y repetir no cambia nada")
    comprueba(perfiles.config_efectiva(a, CONFIG)["area"]["nombre"] == "Valencia",
              "cada uno ve su área")
    comprueba(perfiles.config_efectiva(b, CONFIG)["area"]["nombre"] == "Zaragoza", "   y el otro la suya")
    comprueba(perfiles.puntuador_de(a, CONFIG) is punt_a, "el puntuador se reutiliza")

    copia = perfiles.config_efectiva(a, CONFIG)
    copia["empresas_excluidas"].append("ensuciada")
    comprueba("ensuciada" not in perfiles.config_efectiva(a, CONFIG)["empresas_excluidas"],
              "tocar lo devuelto no llega a la caché")

    nuevo = {**a, "version": 2, "datos": {**a["datos"], "plan": {**PLAN_ADMIN, "umbral": 6.5}}}
    comprueba(perfiles.config_efectiva(nuevo, CONFIG)["umbral"] == 6.5, "otra versión, otro resultado")
    comprueba(perfiles.config_efectiva(a, {**CONFIG, "hours_old": 3})["hours_old"] == 3,
              "otra config, otro resultado")
    antes = len(perfiles._CACHE_EFECTIVA)
    for version in range(3, 20):
        perfiles.config_efectiva({**a, "version": version}, CONFIG)
    comprueba(len(perfiles._CACHE_EFECTIVA) == antes, "la caché no crece con cada versión")

    print("\n[3b] Control negativo: sin el chat_id en la clave, la prueba tiene que fallar")
    original = perfiles._entrada_de_cache

    def rota(perfil, config_global):
        # Una sola entrada para todos: la clave rota de verdad, no un perfil cambiado.
        clave = ("todos",)
        entrada = perfiles._CACHE_EFECTIVA.get("todos")
        if entrada is None:
            entrada = [clave, perfiles._construir_efectiva(perfil["datos"], config_global), None]
            perfiles._CACHE_EFECTIVA["todos"] = entrada
        return entrada

    perfiles._entrada_de_cache = rota
    try:
        punt_a2, punt_b2 = perfiles.puntuador_de(a, CONFIG), perfiles.puntuador_de(b, CONFIG)
        contaminado = (buscador.puntuar_relevancia(titulo, "Zaragoza, Spain", puntuador=punt_a2)
                       == buscador.puntuar_relevancia(titulo, "Zaragoza, Spain", puntuador=punt_b2))
        comprueba(contaminado and perfiles.config_efectiva(b, CONFIG)["area"]["nombre"] == "Valencia",
                  "con la clave rota, B puntúa y filtra con lo de A: la prueba de arriba lo cazaría")
    finally:
        perfiles._entrada_de_cache = original
        perfiles._CACHE_EFECTIVA.pop("todos", None)


def probar_distancia() -> None:
    print("\n[4] Área por distancia")
    area = PLAN_ADMIN["area"]
    espia = Espia({"Paterna, España": Sitio(39.50, -0.44), "Alicante, España": Sitio(38.35, -0.48),
                   "Comunitat Valenciana, España": Sitio(39.4, -0.5, "state")})
    geo = espia.geo(tope=60)
    en = buscador.esta_en_el_area
    comprueba(en("Paterna, VC, ES", area, geo) is True, "dentro del radio")
    comprueba(en("Alicante, Spain", area, geo) is False, "fuera del radio")
    comprueba(en("Pueblo Inventado, Spain", area, geo) is None, "desconocida: no se sabe")
    comprueba(en("Comunitat Valenciana, Spain", area, geo) is None,
              "una región da un centroide, y eso es «no se sabe»")
    for ubicacion in ("", None, "Spain", "ES", "CT, ES", "En remoto, ES"):
        comprueba(en(ubicacion, area, geo) is None, "sin municipio (%r): no se sabe" % (ubicacion,))
    comprueba(espia.preguntas == ["Paterna, España", "Alicante, España", "Pueblo Inventado, España",
                                  "Comunitat Valenciana, España"],
              "solo se pregunta lo que nombra un sitio")
    comprueba(en("Paterna, Valencian Community, Spain", area, geo) is True
              and len(espia.preguntas) == 4, "el mismo municipio con otro formato no gasta consulta")
    comprueba(espia.esperas == 4, "un segundo por consulta nueva")
    comprueba(en("Paterna, VC, ES", {**area, "radio_km": 0}, geo) is False,
              "radio 0: solo remoto, sin consultar")
    comprueba(buscador.Geocodificador.clave_de("Greater Valencia Metropolitan Area") == "Valencia, España",
              "el área metropolitana es la ciudad")

    print("\n[4b] Con area=None o en regex, lo de siempre: ni una consulta")
    mudo = Espia({})
    for ubicacion, esperado in (("Barcelona, Spain", True), ("Mataró, CT, ES", False),
                                ("Greater Barcelona Metropolitan Area", True), ("CT, ES", None)):
        comprueba(en(ubicacion) is esperado, "%s sin área" % ubicacion)
        comprueba(en(ubicacion, {"modo": "regex", "nombre": "barcelona"}, mudo.geo()) is esperado,
                  "   y en modo regex")
    comprueba(mudo.preguntas == [], "sin geocodificar")

    print("\n[4c] Tope de consultas nuevas por corrida")
    sitios = {f"Pueblo{i}, España": Sitio(39.5, -0.4) for i in range(8)}
    espia = Espia(sitios)
    geo = espia.geo(tope=3)
    respuestas = [en(f"Pueblo{i}, Spain", area, geo) for i in range(8)]
    comprueba(len(espia.preguntas) == 3, "con tope 3, 3 preguntas de 8 ubicaciones")
    comprueba(respuestas[:3] == [True] * 3 and respuestas[3:] == [None] * 5,
              "las que no entran son «no se sabe» y se conservan")
    comprueba("Pueblo5, España" not in geo.cache, "y no se apuntan: otra corrida las preguntará")
    comprueba(en("Pueblo1, Spain", area, geo) is True, "lo que ya está en el caché no gasta tope")
    otra = Espia(sitios).geo(tope=3)
    comprueba(en("Pueblo5, Spain", area, otra) is True, "la corrida siguiente tiene su propio tope")

    print("\n[4d] Un fallo de red no es «ese sitio no existe»")
    espia = Espia({"Paterna, España": TimeoutError("red")})
    geo = espia.geo()
    comprueba(en("Paterna, Spain", area, geo) is None, "no se sabe")
    comprueba("Paterna, España" not in geo.cache, "y no se apunta para siempre")

    print("\n[4e] Al titular no se le pone tope: cambiaría decisiones tras un atasco")
    espia = Espia({f"Sitio{i}, Spain": Sitio(41.39, 2.17) for i in range(5)})
    geo = espia.geo(tope=2)
    distancias = buscador.calcular_distancias(
        df_de([oferta("x", f"Sitio{i}, Spain") for i in range(5)]), dict(CONFIG), geo)
    comprueba(distancias["distancia_km"].notna().all() and len(espia.preguntas) == 5,
              "calcular_distancias sigue preguntándolo todo")
    comprueba(geo.nuevas == 5, "pero cuenta para el tope de los demás: la cuota es la misma")

    print("\n[4f] Cada escritura cuenta: sin nada nuevo no se toca el fichero")
    ruta = buscador.GEOCODE_CACHE_PATH
    ruta.write_text(json.dumps({"Barcelona, Spain": [41.38, 2.17]}), encoding="utf-8")
    os.utime(ruta, (1000, 1000))
    buscador.calcular_distancias(df_de([oferta("x")]), dict(CONFIG), Espia({}).geo())
    comprueba(ruta.stat().st_mtime == 1000, "todo en el caché: no se reescribe")
    buscador.calcular_distancias(df_de([oferta("x", "Nuevo, Spain")]), dict(CONFIG), Espia({}).geo())
    comprueba(ruta.stat().st_mtime != 1000, "control: con una ubicación nueva, sí")

    print("\n[4g] filtrar_presenciales_lejos con el área del perfil")
    efectiva = perfiles.config_efectiva(de_catalogo(12, PLAN_ADMIN), CONFIG)
    df = df_de([oferta("Dentro", "Paterna, Spain"), oferta("Lejos", "Alicante, Spain"),
                oferta("Lejos remota", "Alicante, Spain", is_remote=True),
                oferta("Sin ubicación", ""), oferta("Barcelona", "Barcelona, Spain")])
    espia = Espia({"Paterna, España": Sitio(39.50, -0.44), "Alicante, España": Sitio(38.35, -0.48),
                   "Barcelona, España": Sitio(41.39, 2.17)})
    salen = set(buscador.filtrar_presenciales_lejos(df, efectiva, espia.geo())["title"])
    comprueba(salen == {"Dentro", "Lejos remota", "Sin ubicación"},
              "dentro, remota y desconocida se quedan; lejos y Barcelona, fuera: %s" % sorted(salen))


TITULOS_ADMIN = [
    # (título, debería llegar a Encaje alto)
    ("Administrativo/a", True), ("Auxiliar Administrativo", True), ("Administrativa contable", True),
    ("Administrativo/a de contabilidad", True), ("Auxiliar contable", True),
    ("Administrativo/a con A3", True), ("Back Office administrativo", True),
    ("Gestión administrativa y facturación", True), ("Office Administrator", True),
    ("Administrativo/a Junior", True), ("Administrativa de recepción", True),
    ("Auxiliar de oficina", True),
    ("Comercial de seguros", False), ("Administrativo comercial", False),
    ("Responsable de administración", False), ("Jefe de administración", False),
    ("Director financiero", False), ("Enfermera", False), ("Camarero/a", False),
    ("Técnico de sistemas", False), ("Desarrollador Java", False),
    ("Administrative assistant (French speaking)", False), ("Administrativo con francés", False),
    ("Dependienta de tienda", False), ("Administrativo ETT Valencia", False),
]
TITULOS_MOZO = [
    ("Mozo de almacén", True), ("Mozo/a de almacén", True), ("Operario de almacén", True),
    ("Operaria de almacén", True), ("Preparador de pedidos", True),
    ("Preparador/a de pedidos con carretilla", True), ("Carretillero", True),
    ("Carretillero/a frontal", True), ("Mozo de almacén sin experiencia", True),
    ("Picking en almacén", True), ("Mozo de almacén logística", True),
    ("Mozo de almacén becario", True),
    ("Repartidor autónomo", False), ("Mozo de almacén autónomo", False), ("Jefe de almacén", False),
    ("Responsable de logística", False), ("Administrativo de almacén", False),
    ("Camarero/a", False), ("Técnico de logística senior", False), ("Conductor de camión", False),
    ("Dependiente/a", False), ("Warehouse manager", False),
    ("Operario de almacén con inglés", False), ("Comercial", False), ("Limpieza", False),
]


def probar_catalogo() -> None:
    for nombre, el_plan, ciudad, titulos, chat_id in (
        ("administrativo en Valencia, 3-5 años", PLAN_ADMIN, "Valencia, Spain", TITULOS_ADMIN, 20),
        ("mozo de almacén en Zaragoza, sin experiencia", PLAN_MOZO, "Zaragoza, Spain", TITULOS_MOZO, 21),
    ):
        print("\n[5] Catálogo: %s" % nombre)
        perfil = de_catalogo(chat_id, el_plan)
        punt = perfiles.puntuador_de(perfil, CONFIG)
        efectiva = perfiles.config_efectiva(perfil, CONFIG)
        comprueba(punt is not None and 20 <= len(titulos) <= 30, "%d títulos" % len(titulos))
        for titulo, encaja in titulos:
            df = df_de([oferta(titulo, ciudad)])
            ruido = buscador.marcar_ruido(df, efectiva)
            tumbada = bool(ruido["fuera_de_perfil"].iloc[0]) or (
                efectiva["filtrar_senior"] and bool(
                    buscador._compilar(efectiva["patron_senior"]).search(titulo)))
            nota, motivos = buscador.explicar_relevancia(titulo, ciudad, puntuador=punt)
            llega = not tumbada and nota >= efectiva["umbral"]
            bien = (round(sum(p for _, p in motivos), 1) == nota
                    and len({n for n, _ in motivos}) == len(motivos))
            comprueba(llega == encaja and bien,
                      "%-46s %4.1f %s" % (titulo, nota, "llega" if llega else "no"))
    print("\n[5b] Cada peso es un motivo con nombre, sin repetidos, y la suma es el número")
    punt = perfiles.puntuador_de(de_catalogo(20, PLAN_ADMIN), CONFIG)
    desc = "Contabilidad, A3 y comercial. Se requieren 8 años de experiencia."
    fila = {"min_amount": 15000.0, "max_amount": 16000.0, "interval": "yearly", "currency": "EUR",
            "job_type": "parttime"}
    nota, motivos = buscador.explicar_relevancia(
        "Administrativo contable con contabilidad y A3 comercial Junior", "Valencia, Spain", 8, desc,
        puntuador=punt, oferta=fila)
    nombres = [n for n, _ in motivos]
    comprueba(len(set(nombres)) == len(nombres), "ningún motivo repetido")
    comprueba(round(sum(p for _, p in motivos), 1) == nota, "la suma es el número (%.1f)" % nota)
    comprueba(("salario por debajo de tu mínimo", -1.5) in motivos, "salario por debajo: −1,5")
    comprueba(("jornada que no quieres", -1.0) in motivos, "jornada que no quiere: −1,0")
    comprueba(sum(p for n, p in motivos if n in ("contabilidad", "recepción")) <= 2.0,
              "señales del puesto: máx. +2,0")
    comprueba(not [n for n in nombres if "años" in n and "pide" in n],
              "con estricta el ajuste por años no resta: ya lo ha quitado el filtro")
    no_estricto = perfiles.puntuador_de(
        de_catalogo(22, {**PLAN_ADMIN, "experiencia_estricta": False}), CONFIG)
    _, m = buscador.explicar_relevancia("Administrativo", "Valencia", 8, puntuador=no_estricto)
    comprueba(("pide 8 años (tu tope: 5)", -4.5) in m, "sin estricta, el ajuste de hoy")
    _, m = buscador.explicar_relevancia("Administrativo", "Valencia", puntuador=punt)
    comprueba(not [n for n, _ in m if "salario" in n or "jornada" in n],
              "sin el dato, ni salario ni jornada restan")
    comprueba(buscador.explicar_relevancia("Administrativo", "Valencia", puntuador=punt,
                                           oferta={"job_type": "temporary"})[1] == m,
              "elegir «completa» no dice nada de temporal")
    aviso = {"title": "Administrativo", "description": "Inglés avanzado"}
    comprueba("inglés alto" in buscador.alertas(aviso, puntuador=punt), "la alerta de inglés alto")
    alto = perfiles.puntuador_de(de_catalogo(23, {**PLAN_ADMIN, "ingles": "alto"}), CONFIG)
    comprueba("inglés alto" not in buscador.alertas(aviso, puntuador=alto),
              "   que no sale a quien tiene nivel alto")

    print("\n[5c] Claves palabra a palabra: «ett» no es «marketing»")
    _, m = buscador.explicar_relevancia("Administrativo de Marketing", "Valencia", puntuador=punt)
    comprueba(not [n for n, _ in m if "ett" in n], "no casa dentro de otra palabra")

    print("\n[5d] Un perfil de catálogo no hereda nada de IT ni del titular")
    mozo = perfiles.config_efectiva(de_catalogo(21, PLAN_MOZO), CONFIG)
    comprueba(mozo["reglas_descarte"] == [] and buscador.reglas_activas(mozo) == [],
              "sin descartes propios, sin descartes (no las reglas de fábrica)")
    comprueba(len(buscador.reglas_activas({"reglas_descarte": []})) == 20,
              "control: en una config de hoy, una lista vacía sigue cayendo a las de fábrica")
    df = buscador.marcar_ruido(df_de([oferta("Mozo de almacén"), oferta("Administrativo")]), mozo)
    comprueba(not df["fuera_de_perfil"].any(), "«Administrativo» no cae por la regla de IT")
    comprueba(mozo["categorias_siempre_visibles"] == [] and mozo["pesos_relevancia"] == {},
              "ni pesos ni categorías del titular")
    comprueba(mozo["ambito_local"] == "local" and mozo["categorias"]["plan"]["terminos"]
              == PLAN_MOZO["terminos"], "sus términos y su ámbito local")
    largo = perfiles.config_efectiva(
        de_catalogo(24, {**PLAN_MOZO, "terminos": [str(i) for i in range(9)]}), CONFIG)
    comprueba(len(largo["categorias"]["plan"]["terminos"]) == 6, "tope de 6 términos (D5)")
    comprueba(len(buscador.construir_busquedas({**mozo, "distancia_portal_millas": 16,
                                                "country_indeed": "spain"})) == 3,
              "construir_busquedas sirve tal cual")

    print("\n[5e] Evaluar de punta a punta con un perfil de catálogo")
    admin = perfiles.config_efectiva(de_catalogo(20, PLAN_ADMIN), CONFIG)
    ofertas = [oferta("Administrativo/a", "Paterna, Spain", ambito="local",
                      descripcion="Se requieren 2 años de experiencia. " * 20),
               oferta("Administrativo/a Senior", "Paterna, Spain", ambito="local"),
               # «principal» tumba a un técnico de IT (SENIOR_EXCLUDE) y aquí no significa nada.
               oferta("Administrativo/a sede principal", "Paterna, Spain", ambito="local"),
               oferta("Administrativo/a veterano", "Paterna, Spain", ambito="local",
                      descripcion="Se requieren 9 años de experiencia. " * 20),
               oferta("Administrativa lejana", "Alicante, Spain", ambito="local"),
               oferta("Enfermera", "Paterna, Spain", ambito="local")]
    # Vacío: las pruebas de distancia de arriba dejan ahí Paterna, y sin esto no se preguntaría
    # nada y la comprobación de las consultas pasaría sin probar nada.
    buscador.GEOCODE_CACHE_PATH.write_text("{}", encoding="utf-8")
    espia = Espia({"Paterna, España": Sitio(39.50, -0.44), "Alicante, España": Sitio(38.35, -0.48)})
    geo = espia.geo(tope=60)
    barato = buscador.evaluar_barato(df_de(ofertas), admin, geo=geo)
    comprueba(set(barato["title"]) == {"Administrativo/a", "Administrativo/a sede principal",
                                       "Administrativo/a veterano"},
              "barato: su senior, su ruido y la distancia a su centro")
    comprueba(espia.preguntas == ["Paterna, España", "Alicante, España"],
              "distancias y área comparten consulta: %s" % espia.preguntas)
    caro = buscador.evaluar_caro(barato, admin, perfiles.puntuador_de(de_catalogo(20, PLAN_ADMIN), CONFIG),
                                 geo=geo)
    comprueba(set(caro["title"]) == {"Administrativo/a", "Administrativo/a sede principal"},
              "caro: los 9 años caen, por estricta")
    comprueba(espia.preguntas == ["Paterna, España", "Alicante, España"],
              "y el filtro de lejanía no pregunta otra vez")
    comprueba(set(caro["relevancia"]) == {4.0}, "y puntúa con su tabla: %s" % list(caro["relevancia"]))


def probar_enganches() -> None:
    print("\n[6] Enganche de la capa aprendida (MP6)")

    class ConAprendido(buscador.PuntuadorReglas):
        # Desde MP6 el enganche recibe también los años que pide la oferta (uno de sus rasgos).
        def termino_aprendido(self, titulo, ubicacion, descripcion, oferta, anos_experiencia=None):
            return ("lo aprendido (SOC)", 1.2)

    base = buscador.PuntuadorReglas()
    nota, motivos = buscador.explicar_relevancia("SOC Analyst", "Barcelona", puntuador=base)
    comprueba(not [n for n, _ in motivos if "aprendido" in n], "hoy no añade ningún motivo")
    nota2, motivos2 = buscador.explicar_relevancia("SOC Analyst", "Barcelona",
                                                   puntuador=ConAprendido())
    comprueba(("lo aprendido (SOC)", 1.2) in motivos2 and nota2 == round(nota + 1.2, 1),
              "si devuelve algo, es un término más con su motivo")

    print("\n[7] puntuador=None no depende de perfiles.db")
    original = perfiles.conexion

    def caida():
        raise perfiles.BaseNoDisponible("base caída")

    perfiles.conexion = caida
    try:
        comprueba(buscador.puntuar_relevancia("SOC Analyst Junior", "Barcelona") > 0,
                  "con la base caída se sigue puntuando")
        df = buscador.anotar_relevancia(df_de([oferta("SOC Analyst Junior")]))
        comprueba(df["relevancia"].iloc[0] > 0, "   y anotando")
        comprueba(perfiles.config_efectiva(perfiles.PERFIL_GLOBAL, CONFIG)["umbral"] == 5.0,
                  "   y la config efectiva que usa buscar() no la toca")
    finally:
        perfiles.conexion = original

    print("\n[8] Dos juegos a la vez: lo que necesita MP4")
    otro = reglas.Juego(nombre="prueba", senales=[("solo esto", 9.0, r"\bsoc\b")], penalizaciones=[],
                        reglas_descripcion=[], patron_senior=reglas.TITULAR_2026_10.patron_senior,
                        config=reglas.TITULAR_2026_10.config)
    reglas.JUEGOS[otro.nombre] = otro
    try:
        perfil = {"chat_id": 30, "version": 1, "datos": {"reglas": "prueba", "config": {}}}
        punt = perfiles.puntuador_de(perfil, CONFIG)
        comprueba(buscador.explicar_relevancia("SOC Analyst", puntuador=punt)[1] == [("solo esto", 9.0)],
                  "un perfil usa el suyo")
        comprueba(buscador.puntuar_relevancia("SOC Analyst") != 9.0, "   y el titular, el de siempre")
        try:
            reglas.Juego(nombre="malo", senales=[("a", 1.0, "x"), ("a", 2.0, "y")], penalizaciones=[],
                         reglas_descripcion=[], patron_senior=otro.patron_senior, config={})
            comprueba(False, "nombres repetidos")
        except ValueError:
            comprueba(True, "un juego con nombres repetidos no se puede crear")
    finally:
        del reglas.JUEGOS[otro.nombre]
    comprueba(set(reglas.CLAVES_DE_LA_PERSONA) == set(reglas.TITULAR_2026_10.config),
              "el juego congela exactamente las claves de la persona")
    comprueba(not set(reglas.CLAVES_DE_LA_PERSONA) & {"hours_old", "results_wanted",
                                                      "idioma_min_caracteres", "telegram_bot_token",
                                                      "telegram_chat_id"},
              "ninguna del sistema ni ningún secreto")


def main() -> int:
    probar_adicional()
    probar_config_efectiva()
    probar_cache()
    probar_distancia()
    probar_catalogo()
    probar_enganches()
    print()
    if fallos:
        print("%d FALLOS de %d comprobaciones" % (len(fallos), hechas))
        for fallo in fallos:
            print("  -", fallo)
        return 1
    print("TODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
