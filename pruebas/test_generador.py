# -*- coding: utf-8 -*-
"""
Pruebas del generador del plan de perfil (MP7): de las respuestas de la encuesta a un plan que
`perfiles.config_efectiva` y `buscador.PuntuadorCatalogo` leen sin error.

    python pruebas/test_generador.py

Con las seis personas del prompt (pruebas/personas_mp7.py): para cada una, como mucho 6 términos, los
ámbitos y los filtros que tocan, y su plan pasa por la config efectiva, por `construir_busquedas` y por
el puntuador. Después, lo que el generador decide por su cuenta (choca_con, idiomas, palabras clave), el
cambio del puntuador de catálogo (los negativos de un puesto solo restan en títulos de ese puesto) y las
consultas únicas de los seis planes juntos, que son las de la tabla del §6.

Nada sale a la red: no se busca, no se geocodifica y no se escribe en ningún chat.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))
os.environ["JOBSPY_DATA_DIR"] = tempfile.mkdtemp(prefix="test-generador-")

import pandas as pd  # noqa: E402

import buscador  # noqa: E402
import config as config_mod  # noqa: E402
import perfiles  # noqa: E402
from perfilado import generador  # noqa: E402
from personas_mp7 import PERSONAS  # noqa: E402

fallos: list[str] = []
hechas = 0
CONFIG = dict(config_mod.CONFIG_POR_DEFECTO)
CATALOGO = generador.cargar_catalogo()


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def de_catalogo(chat_id: int, plan: dict) -> dict:
    return {"chat_id": chat_id, "version": 1, "datos": {"v": 1, "reglas": "catalogo", "plan": plan}}


def llega(perfil: dict, titulo: str, ubicacion: str, remota=None) -> tuple[bool, float, list]:
    """Lo mismo que hace la corrida con un título: descartes, senior y puntuación contra el umbral, con
    la fila entera, que es lo que recibe el puntuador de catálogo (salario, jornada y modalidad)."""
    efectiva = perfiles.config_efectiva(perfil, CONFIG)
    punt = perfiles.puntuador_de(perfil, CONFIG)
    fila = {"title": titulo, "location": ubicacion, "company": "ACME", "description": None,
            "job_level": None, "is_remote": remota, "ambito": "local", "job_url": "caso://" + titulo}
    df = pd.DataFrame([fila])
    fuera = bool(buscador.marcar_ruido(df, efectiva)["fuera_de_perfil"].iloc[0]) or (
        efectiva["filtrar_senior"] and bool(buscador._compilar(efectiva["patron_senior"]).search(titulo)))
    nota, motivos = buscador.explicar_relevancia(titulo, ubicacion, puntuador=punt, oferta=fila)
    return (not fuera and nota >= efectiva["umbral"]), nota, motivos


# (título, ubicación, ¿debe llegar a Encaje alto?[, ¿el portal la marca remota?])
CASOS = {
    "soporte_bcn": [("Técnico/a de Soporte N1 - Helpdesk", "Barcelona, Spain", True),
                    ("Técnico de sistemas y redes Junior", "Barcelona, Spain", True),
                    ("Senior IT Support Engineer", "Barcelona, Spain", False),
                    ("Técnico/a de soporte preventa", "Barcelona, Spain", False),
                    ("Camarero/a", "Barcelona, Spain", False),
                    ("Técnico/a de PRL", "Barcelona, Spain", False)],
    "administrativa_valencia": [("Administrativo/a contable", "Valencia, Spain", True),
                                ("Auxiliar administrativo/a", "Valencia, Spain", True),
                                ("Responsable de administración", "Valencia, Spain", False),
                                ("Comercial junior", "Valencia, Spain", False),
                                ("Administrativo/a comercial ETT", "Valencia, Spain", False),
                                ("Enfermero/a", "Valencia, Spain", False)],
    "camarero_malaga": [("Camarero/a de sala", "Málaga, Spain", True), ("Barista", "Málaga, Spain", True),
                        ("Camarera de pisos", "Málaga, Spain", False),
                        ("Waiter (English speaking)", "Málaga, Spain", False),
                        ("Jefe de sala", "Málaga, Spain", False)],
    "enfermera_madrid": [("Enfermero/a de quirófano", "Madrid, Spain", True),
                         ("Jefe/a de enfermería", "Madrid, Spain", True),
                         ("Enfermera junior", "Madrid, Spain", False),
                         ("Auxiliar de enfermería", "Madrid, Spain", False)],
    "mozo_zaragoza": [("Mozo/a de almacén", "Zaragoza, Spain", True),
                      ("Carretillero/a frontal", "Zaragoza, Spain", True),
                      ("Mozo de almacén autónomo", "Zaragoza, Spain", False),
                      ("Jefe de almacén", "Zaragoza, Spain", False),
                      ("Administrador/a de Almacenamiento SAN/NAS (Nivel 2)", "Zaragoza, Spain", False)],
    "desarrollador_remoto_ue": [("Senior Backend Developer (Python)", "Spain", True, True),
                                # El núcleo y el +1,0 de «en remoto» (decidido por el titular el 05-11):
                                # sin él se quedaba en 4,0 y un título exacto no llegaba nunca.
                                ("DevOps Engineer AWS", "European Union", True, True),
                                # Sin saber si es remota no suma: es lo que la corrida deja pasar sin
                                # ubicación conocida.
                                ("DevOps Engineer AWS (oficina)", "European Union", False, False),
                                ("Business Developer", "Spain", False, True),
                                ("Técnico/a de Soporte IT", "Spain", False, True)],
}


def probar_personas() -> dict:
    planes = {}
    for i, (nombre, respuestas) in enumerate(PERSONAS.items()):
        print("\n[1] %s" % nombre)
        plan = generador.generar_plan(respuestas)
        planes[nombre] = plan
        terminos = plan["terminos"]
        normales = [generador._normal(t) for t in terminos]
        comprueba(1 <= len(terminos) <= 6 and len(set(normales)) == len(normales),
                  "%d términos, sin repetidos: %s" % (len(terminos), ", ".join(terminos)))
        perfil = de_catalogo(700 + i, plan)
        try:
            efectiva = perfiles.config_efectiva(perfil, CONFIG)
            punt = perfiles.puntuador_de(perfil, CONFIG)
            busquedas = buscador.construir_busquedas(efectiva)
            ok = isinstance(punt, buscador.PuntuadorCatalogo)
        except Exception as error:  # noqa: BLE001 — lo que se prueba es que no rompa
            ok, efectiva, busquedas = False, {}, []
            print("      ", repr(error))
        comprueba(ok, "pasa por config_efectiva, puntuador_de y construir_busquedas")
        comprueba(len(busquedas) == plan["explicacion"]["consultas"],
                  "la ficha dice %d consultas y son %d" % (plan["explicacion"]["consultas"], len(busquedas)))
        ficha = plan["explicacion"]["ficha"]
        comprueba(ficha.startswith("🗂️ ") and "🔎 Buscaré: " in ficha and "(%d)" % len(terminos) in ficha,
                  "la ficha empieza por los puestos y dice qué buscará")
        for titulo, ubicacion, debe, *remota in CASOS[nombre]:
            si, nota, motivos = llega(perfil, titulo, ubicacion, *remota)
            unicos = len({n for n, _ in motivos}) == len(motivos)
            comprueba(si == debe and unicos and round(sum(p for _, p in motivos), 1) == nota,
                      "%-46s %4.1f %s" % (titulo, nota, "llega" if si else "no"))
        planes[nombre + "/efectiva"] = efectiva
    return planes


def probar_filtros(planes: dict) -> None:
    print("\n[2] Ámbitos y filtros de cada persona")
    p = planes["soporte_bcn"]
    e = planes["soporte_bcn/efectiva"]
    comprueba(list(p["ambitos"]) == ["local", "remoto_es"], "soporte BCN: local y remoto España")
    comprueba(e["max_distancia_km"] == 10 and e["distancia_portal_millas"] == 6, "   10 km = 6 millas en el portal")
    comprueba("tecnoempleo" in p["ambitos"]["local"]["sitios"], "   Tecnoempleo, porque es IT y en Barcelona")
    comprueba(p["experiencia_max_exigida"] == 1 and p["experiencia_estricta"] and p["practicas"],
              "   sin experiencia: tope 1 año, estricto y prácticas que acepta")
    comprueba(e["filtrar_senior"] and e["niveles_excluidos"] == ["mid-senior level", "director", "executive"],
              "   senior fuera y los tres niveles de LinkedIn")
    comprueba("junior it support" in p["terminos"], "   con ≤ 2 años, la variante junior")
    comprueba(any("desarrollo de software" == n["nombre"] for q in p["puestos"] for n in q["negativos"]),
              "   soporte sin desarrollo: el negativo de desarrollo se queda")

    p, e = planes["administrativa_valencia"], planes["administrativa_valencia/efectiva"]
    comprueba(list(p["ambitos"]) == ["local"] and p["ambitos"]["local"]["location"] == "Valencia, Spain",
              "administrativa Valencia: solo local")
    comprueba("tecnoempleo" not in p["ambitos"]["local"]["sitios"] and "eures" in p["ambitos"]["local"]["sitios"],
              "   sin Tecnoempleo y con EURES")
    comprueba(e["anos_experiencia_max"] == 5 and e["experiencia_estricta"], "   3–5 años: tope 5, estricto")
    comprueba(not any("junior" in t for t in p["terminos"]), "   con 4 años, sin variantes junior")
    comprueba("contabilidad" in p["terminos"] and "a3" not in p["terminos"] and "a3" in p["claves_mas"],
              "   «contabilidad» se busca; «a3» puntúa pero no se busca (menos de 4 letras)")
    comprueba(e["empresas_excluidas"] == ["Asesoría Ejemplo"] and p["salario_min"] == 18000
              and p["jornada"] == ["completa"], "   empresas excluidas, salario y jornada pasan al plan")
    comprueba(e["idiomas_permitidos"] == ["es", "ca", "en"], "   idiomas es, ca, en")

    p, e = planes["camarero_malaga"], planes["camarero_malaga/efectiva"]
    comprueba(e["max_distancia_km"] == 60 and e["distancia_portal_millas"] == 37,
              "camarero Málaga: toda la provincia, 60 km = 37 millas")
    comprueba(e["idiomas_permitidos"] == ["es"] and p["ingles"] is None,
              "   inglés básico: ni idioma permitido ni alerta quitada")
    comprueba("waiter" not in p["terminos"], "   y no se busca en inglés")
    comprueba(not p["practicas"], "   no acepta prácticas")

    p, e = planes["enfermera_madrid"], planes["enfermera_madrid/efectiva"]
    comprueba(not e["filtrar_senior"] and e["niveles_excluidos"] == ["director", "executive"],
              "enfermera 6–10 años: el senior no se filtra")
    comprueba(e["anos_experiencia_max"] == 10 and not e["experiencia_estricta"], "   tope 10, solo resta")
    comprueba(p["ingles"] == "alto" and "en" in e["idiomas_permitidos"], "   inglés alto")
    comprueba("UCI" in p["claves_mas"] and "UCI" not in p["terminos"], "   la certificación puntúa, no se busca")

    p, e = planes["mozo_zaragoza"], planes["mozo_zaragoza/efectiva"]
    comprueba(list(p["ambitos"]) == ["local"] and e["idiomas_permitidos"] == ["es"], "mozo Zaragoza: local, español")
    comprueba(p["experiencia_max_exigida"] == 1 and "mozo de almacén sin experiencia" in p["terminos"],
              "   menos de 1 año: tope 1 y «sin experiencia» en la búsqueda")

    p, e = planes["desarrollador_remoto_ue"], planes["desarrollador_remoto_ue/efectiva"]
    comprueba(list(p["ambitos"]) == ["remoto_es", "remoto_ue"] and p["area"]["radio_km"] == 0,
              "desarrollador solo remoto UE: remoto España y UE, sin local, radio 0")
    comprueba(p["ambitos"]["remoto_ue"]["sitios"] == ["linkedin"], "   fuera de España, solo LinkedIn")
    comprueba(all("tecnoempleo" not in a["sitios"] for a in p["ambitos"].values()),
              "   sin Tecnoempleo: Sevilla queda lejos de su feed")
    comprueba(p["experiencia_max_exigida"] is None and e["anos_experiencia_max"] is None, "   +10 años: sin tope")
    comprueba(not e["filtrar_senior"], "   y el senior no se filtra")
    comprueba("python" in p["terminos"], "   su palabra clave «python» se busca")


def probar_reglas_del_generador() -> None:
    print("\n[3] Lo que decide el generador")
    base = {"experiencia": "1_2", "ubicacion": {"nombre": "Barcelona", "lat": 41.39, "lon": 2.17, "radio_km": 10},
            "modalidad": ["presencial"], "idiomas": ["es"]}
    solo = generador.generar_plan(dict(base, puestos=["it.soporte"]))
    dos = generador.generar_plan(dict(base, puestos=["it.soporte", "it.desarrollo"]))
    negs = lambda plan: {n["nombre"] for q in plan["puestos"] for n in q["negativos"]}  # noqa: E731
    comprueba("desarrollo de software" in negs(solo), "soporte solo: «desarrollo de software» resta")
    comprueba("desarrollo de software" not in negs(dos), "soporte + desarrollo: el generador lo quita (choca_con)")
    comprueba(not any("choca_con" in n for q in dos["puestos"] for n in q["negativos"])
              and not any("choca_con" in d for d in dos["descartes"]), "   y el plan no lleva choca_con")
    mixto = generador.generar_plan(dict(base, puestos=["it.soporte", "hosteleria.camarero"]))
    comprueba(not any(d["nombre"].startswith("Oficios no técnicos") for d in mixto["descartes"]),
              "soporte + camarero: el descarte de oficios de IT se va, o tiraría los de camarero")
    comprueba(any(d["nombre"].startswith("Oficios no técnicos") for d in solo["descartes"]),
              "   y con soporte solo, se queda")
    # Control negativo: sin la comprobación de choque, el negativo se queda.
    original = generador._choca
    try:
        generador._choca = lambda regla, elegidos: False
        roto = generador.generar_plan(dict(base, puestos=["it.soporte", "it.desarrollo"]))
        comprueba("desarrollo de software" in negs(roto), "   control: sin _choca, el negativo se queda")
    finally:
        generador._choca = original

    ca = generador.generar_plan(dict(base, experiencia="6_10", puestos=["sanidad.enfermeria"], idiomas=["es", "ca"]))
    es = generador.generar_plan(dict(base, experiencia="6_10", puestos=["sanidad.enfermeria"]))
    comprueba(ca["terminos"] == ["enfermero", "enfermera", "infermer", "infermera"] and es["terminos"] == ["enfermero", "enfermera"],
              "en catalán solo si lo habla, y detrás del castellano")
    en = generador.generar_plan(dict(base, puestos=["comercial.inside"], idiomas=["es"]))
    comprueba(en["terminos"] == [] and en["explicacion"]["avisos"],
              "inside sales sin inglés: nada que buscar, y la ficha lo dice")
    otro = generador.generar_plan(dict(base, puestos=["hosteleria.otro"], otros=["sumiller"]))
    # Hasta el 11-11 esto pedía `otro["puestos"] == []`: lo escrito era solo palabra clave, y no llegaba
    # nunca al listón. Ahora es un puesto con su núcleo (ver probar_otro).
    comprueba(otro["terminos"] == ["sumiller"] and otro["claves_mas"] == ["sumiller"]
              and [p["id"] for p in otro["puestos"]] == ["otro:sumiller"]
              and "eures" in otro["ambitos"]["local"]["sitios"],
              "«Otro» de hostelería: se busca lo escrito, es un puesto más, fuentes de su familia")
    suelto = generador.generar_plan(dict(base, puestos=[], otros=["guía de montaña"]))
    comprueba(suelto["ambitos"]["local"]["sitios"] == list(generador.FUENTES_SIN_FAMILIA),
              "solo lo escrito, sin familia: las fuentes generales, sin Tecnoempleo")
    cinco = generador.generar_plan(dict(base, puestos=["it.soporte", "it.sistemas", "it.ciber", "it.datos",
                                                        "it.qa", "it.cloud"], claves_mas=["fortinet", "linux"]))
    comprueba(len(cinco["puestos"]) == 5 and len(cinco["terminos"]) == 6,
              "seis puestos se quedan en cinco (§4.2) y los términos en seis")
    primeros = [generador._indice(CATALOGO)[0][i]["terminos"]["es"][0]
                for i in ("it.soporte", "it.sistemas", "it.ciber", "it.datos", "it.qa")]
    comprueba(cinco["terminos"][:5] == primeros and cinco["terminos"][5] == "fortinet",
              "   el primero de cada puesto y después la palabra clave")
    antes = json.dumps(CATALOGO, sort_keys=True)
    generador.generar_plan(dict(base, puestos=["it.soporte", "it.desarrollo"]), catalogo=CATALOGO)
    comprueba(json.dumps(CATALOGO, sort_keys=True) == antes, "generar un plan no toca el catálogo")
    for malas, que in (({"puestos": ["no.existe"]}, "un puesto que no existe"),
                       ({"puestos": []}, "sin puestos ni «otro»"),
                       ({"puestos": ["it.soporte"], "ubicacion": {}}, "sin ciudad"),
                       ({"puestos": ["it.soporte"], "experiencia": "mucha"}, "una experiencia que no existe")):
        try:
            generador.generar_plan(dict(base, **malas))
            comprueba(False, "%s es un error" % que)
        except ValueError:
            comprueba(True, "%s es un error" % que)


def probar_negativos_del_puntuador() -> None:
    print("\n[4] Los negativos de un puesto solo restan en títulos de ese puesto")
    base = {"experiencia": "1_2", "ubicacion": {"nombre": "Bilbao", "lat": 43.26, "lon": -2.93, "radio_km": 25},
            "modalidad": ["presencial"], "idiomas": ["es"]}
    plan = generador.generar_plan(dict(base, puestos=["marketing.diseno", "oficios.mecanico"]))
    punt = buscador.PuntuadorCatalogo(plan)
    motivos = dict(punt.reglas_del_titulo("Mecánico/a de automoción", "Bilbao, Spain"))
    comprueba("diseño industrial o técnico" not in motivos and motivos.get("puesto: mecánico/a de automoción") == 4.0,
              "diseño + mecánico: «Mecánico/a de automoción» sin el −3,0 de diseño industrial")
    motivos = dict(punt.reglas_del_titulo("Diseñador/a gráfico industrial", "Bilbao, Spain"))
    comprueba(motivos.get("diseño industrial o técnico") == -3.0,
              "   y el negativo sigue restando a lo que sí casa con diseño gráfico")
    # Control negativo: si los negativos fueran de todos los puestos, el mecánico volvería a restar.
    roto = buscador.PuntuadorCatalogo(plan)
    todos = {d for _, _, d in roto.nucleos}
    roto.negativos = [(n, p, rx, todos) for n, p, rx, _ in roto.negativos]
    comprueba("diseño industrial o técnico" in dict(roto.reglas_del_titulo("Mecánico/a de automoción", "Bilbao, Spain")),
              "   control: sin la condición, el mecánico pierde 3 puntos")
    # Los planes de MP2 (sin `id` en los puestos, con nombre) siguen funcionando.
    viejo = json.loads((AQUI / "fixtures" / "plan_administrativo_valencia.json").read_text(encoding="utf-8"))
    sin_id = copy.deepcopy(viejo)
    for q in sin_id["puestos"]:
        q.pop("id")
    m = dict(buscador.PuntuadorCatalogo(sin_id).reglas_del_titulo("Administrativo comercial", "Valencia"))
    comprueba(m.get("comercial encubierto") == -2.0, "un plan sin ids usa el nombre del puesto")


def probar_consultas(planes: dict) -> None:
    print("\n[5] Consultas únicas: la tabla del §6")
    activos = [de_catalogo(800 + i, planes[n]) for i, n in enumerate(PERSONAS)]
    titular = {"chat_id": 799, "version": 1, "datos": {"v": 1, "reglas": "titular-ciber", "config": {}}}
    solas = {n: len(buscador.planificar_consultas([a], CONFIG)) for n, a in zip(PERSONAS, activos)}
    seis = buscador.planificar_consultas(activos, CONFIG)
    con_titular = buscador.planificar_consultas([titular] + activos, CONFIG)
    del_titular = len(buscador.planificar_consultas([titular], CONFIG))
    tope = int(CONFIG.get("max_busquedas_por_corrida") or buscador.PRESUPUESTO_POR_DEFECTO)
    for nombre, n in solas.items():
        print("       %-26s %2d consultas" % (nombre, n))
    print("       %-26s %2d únicas (suma %d)" % ("las seis juntas", len(seis), sum(solas.values())))
    # Con el ritmo del servidor (data/config.json del 04-11: cada hora y hours_old de 38 h), no el de
    # fábrica (cada 6 h), que es lo que trae CONFIG_POR_DEFECTO.
    servidor = dict(CONFIG, busqueda_automatica_horas=1, hours_old=38)
    rotacion = buscador.periodo_de_rotacion(len(con_titular), servidor)
    print("       %-26s %2d únicas; con presupuesto %d y una corrida por hora, cada una se repite cada %g h" % (
        "titular + las seis", len(con_titular), tope, rotacion))
    comprueba(all(n <= 6 * len(planes[k]["ambitos"]) for k, n in solas.items()),
              "cada persona, como mucho 6 términos por ámbito")
    comprueba(len(seis) == sum(solas.values()), "las seis no comparten ninguna consulta (%d)" % len(seis))
    comprueba(del_titular == 33, "el titular con titular-ciber: sus 33 de siempre (%d)" % del_titular)
    comprueba(len(con_titular) == del_titular + len(seis), "titular + seis = %d" % len(con_titular))
    comprueba(len(buscador.elegir_consultas(con_titular, {}, tope)) == tope, "el presupuesto sigue en %d" % tope)
    comprueba(rotacion <= servidor["hours_old"] / 2,
              "cada consulta se repite en %g h, dentro de la mitad de la ventana de %d h (D5)" % (
                  rotacion, servidor["hours_old"]))


def probar_senior() -> None:
    print("\n[6] El senior de los perfiles de catálogo")
    filtro = buscador._compilar(perfiles._SENIOR_GENERICO)
    comprueba(perfiles._SENIOR_GENERICO == buscador._CAT_SENIOR.pattern,
              "el filtro (perfiles) y la resta (buscador) son el mismo patrón")
    no_senior = ["Community Manager", "Key Account Manager", "Account Manager (SaaS)", "Social Media Manager",
                 "Content Manager", "Traffic manager", "Lead Generation Specialist", "Tour Leader",
                 "Jefe/a de partida", "Jefe de rango"]
    senior = ["Store Manager", "Responsable de tienda", "Jefe/a de recepción", "Team Leader BDR", "Product Manager",
              "Ecommerce Manager", "Senior Accountant", "Director/a de tiempo libre", "Head of Sales",
              "Account Director", "Lead Developer"]
    comprueba(not any(filtro.search(t) for t in no_senior), "no son nivel: %s" % ", ".join(no_senior))
    comprueba(all(filtro.search(t) for t in senior), "sí lo son: %s" % ", ".join(senior))
    # Control negativo: con el patrón de MP2, el puesto propio de marketing y de ventas era «senior».
    viejo = buscador._compilar(r"\bsenior\b|\bsr\.?\b|\blead(?:er)?\b|\bmanager\b|\bdirector\b"
                               r"|\bhead of\b|\bjefe\b|\bresponsable\b")
    comprueba(sum(1 for t in no_senior if viejo.search(t)) == len(no_senior),
              "   control: con el patrón de MP2, los %d eran senior" % len(no_senior))
    plan = generador.generar_plan({"puestos": ["marketing.social"], "experiencia": "1_2", "modalidad": ["presencial"],
                                   "ubicacion": {"nombre": "Sevilla", "lat": 37.39, "lon": -5.98, "radio_km": 10},
                                   "idiomas": ["es"]})
    si, nota, _ = llega(de_catalogo(950, plan), "Community Manager", "Sevilla, Spain")
    comprueba(si, "un community manager con 1–2 años recibe «Community Manager» (%.1f)" % nota)


def probar_remoto() -> None:
    print("\n[7] El +1,0 de «en remoto» para quien busca solo en remoto")
    remoto = generador.generar_plan(PERSONAS["desarrollador_remoto_ue"])
    local = generador.generar_plan(dict(PERSONAS["desarrollador_remoto_ue"], modalidad=["presencial", "remoto"],
                                        ubicacion={"nombre": "Sevilla", "lat": 37.3891, "lon": -5.9845, "radio_km": 25}))
    _, nota, motivos = llega(de_catalogo(960, remoto), "DevOps Engineer AWS", "Spain", True)
    comprueba(dict(motivos).get("en remoto") == 1.0 and nota == 5.0, "solo remoto y oferta remota: +1,0 (%.1f)" % nota)
    _, nota, motivos = llega(de_catalogo(961, remoto), "DevOps Engineer AWS", "Spain", None)
    comprueba("en remoto" not in dict(motivos), "sin saber si es remota, nada (%.1f)" % nota)
    _, nota, motivos = llega(de_catalogo(962, remoto), "DevOps Engineer AWS", "Sevilla, Spain", True)
    comprueba(dict(motivos).get("en Sevilla") == 1.0 and "en remoto" not in dict(motivos),
              "en su ciudad y en remoto: el +1,0 una sola vez")
    _, nota, motivos = llega(de_catalogo(963, local), "DevOps Engineer AWS", "Spain", True)
    comprueba("en remoto" not in dict(motivos), "con ámbito local no hay +1,0 de remoto: tiene su ciudad")
    # Control negativo: sin el radio 0, el mismo perfil no lo suma.
    roto = buscador.PuntuadorCatalogo(remoto)
    roto.solo_remoto = False
    fila = {"title": "DevOps Engineer AWS", "location": "Spain", "is_remote": True, "description": None}
    comprueba("en remoto" not in dict(roto.motivos("DevOps Engineer AWS", "Spain", None, None, fila)),
              "   control: sin solo_remoto, no hay +1,0")


def descartada(perfil: dict, titulo: str) -> bool:
    """¿Lo tira un descarte del plan? (sin mirar la nota ni el senior)"""
    efectiva = perfiles.config_efectiva(perfil, CONFIG)
    df = pd.DataFrame([{"title": titulo, "location": "Barcelona, Spain", "company": "ACME", "description": None,
                        "job_level": None, "is_remote": None, "ambito": "local", "job_url": "caso://" + titulo}])
    return bool(buscador.marcar_ruido(df, efectiva)["fuera_de_perfil"].iloc[0])


def probar_practicas() -> None:
    print("\n[8] Quien no acepta prácticas no recibe programas de prácticas ni de recién titulado (MP8)")
    # Tres programas de prácticas reales que se descartaron, de los mejor puntuados, y
    # dos más del mismo tipo. Con `practicas` en falso el plan de catálogo los dejaba pasar: «FY27 | Junior»
    # daba 7,0 y «Graduate Program» 5,0, y «Prácticas…» solo perdía 1,5.
    programas = ["Prácticas IT: Ciberseguridad 26-27", "FY27 | Junior | T&T | Ciberseguridad",
                 "Prácticas en Smart Factory y Ciberseguridad STP27", "Graduate Program Cybersecurity",
                 "Programa de recién titulados - Ciberseguridad", "Beca de ciberseguridad", "Cybersecurity Intern"]
    barcelona = {"nombre": "Barcelona", "lat": 41.39, "lon": 2.17, "radio_km": 25}
    base = {"puestos": ["it.ciber"], "experiencia": "ninguna", "ubicacion": barcelona,
            "modalidad": ["presencial", "hibrido", "remoto"], "remoto_alcance": "es", "idiomas": ["es", "en"], "ingles": "intermedio"}
    no = de_catalogo(970, generador.generar_plan(dict(base, practicas=False)))
    for titulo in programas:
        si, nota, _ = llega(no, titulo, "Barcelona, Spain")
        comprueba(not si and descartada(no, titulo), "practicas = No: «%s» se descarta (no llega, aunque puntúe %.1f)" % (titulo, nota))
    for titulo in ("Analista SOC Junior", "Técnico/a de ciberseguridad junior", "Analista de seguridad N1"):
        si, nota, _ = llega(no, titulo, "Barcelona, Spain")
        comprueba(si and not descartada(no, titulo), "   y los puestos de verdad siguen llegando: «%s» (%.1f)" % (titulo, nota))
    comprueba(not descartada(no, "Analista SOC - buenas prácticas de seguridad") and not descartada(no, "Best practices Security Analyst"),
              "   «buenas prácticas» no es un programa de prácticas")
    comprueba(not descartada(no, "Instructor de prácticas de conducción") and not descartada(no, "Tutor de prácticas en empresa"),
              "   ni el instructor o el tutor de prácticas, que en su oficio es el puesto")
    comprueba(not descartada(no, "Internal Audit Analyst"), "   «intern» no casa con «internal»")
    # Quien las acepta sí las recibe
    si_acepta = de_catalogo(971, generador.generar_plan(dict(base, practicas=True)))
    comprueba(DESCARTE := generador.DESCARTE_DE_PRACTICAS["nombre"] not in [d["nombre"] for d in si_acepta["datos"]["plan"]["descartes"]],
              "practicas = Sí: el plan no lleva la regla")
    for titulo in programas[:3]:
        si, nota, _ = llega(si_acepta, titulo, "Barcelona, Spain")
        comprueba(si, "   y le llegan: «%s» (%.1f)" % (titulo, nota))
    # Quien lleva años no las quiere (no se le pregunta): la regla va por defecto
    for experiencia in ("1_2", "3_5", "6_10", "mas_10"):
        plan = generador.generar_plan(dict(base, experiencia=experiencia, practicas=True))
        comprueba(plan["practicas"] is False and generador.DESCARTE_DE_PRACTICAS["nombre"] in [d["nombre"] for d in plan["descartes"]],
                  "con experiencia «%s» no se preguntan y se descartan (aunque venga practicas = Sí)" % experiencia)
    # Una sola vez, aunque el puesto ya trajera la suya
    nombres = [d["nombre"] for d in no["datos"]["plan"]["descartes"]]
    comprueba(len(nombres) == len(set(nombres)), "ninguna regla de descarte repetida")
    # Control negativo: sin la regla, el mismo plan deja pasar el programa que se descartó
    sin_regla = copy.deepcopy(no)
    sin_regla["chat_id"] = 972  # otro chat_id: la config efectiva se cachea por chat y versión
    sin_regla["datos"]["plan"]["descartes"] = [d for d in sin_regla["datos"]["plan"]["descartes"]
                                               if d["nombre"] != generador.DESCARTE_DE_PRACTICAS["nombre"]]
    si, nota, _ = llega(sin_regla, "FY27 | Junior | T&T | Ciberseguridad", "Barcelona, Spain")
    comprueba(si and nota >= 5.0, "   control: sin la regla, «FY27 | Junior…» llega con %.1f (el fallo que se corrigió)" % nota)


def nota_de(perfil: dict, titulo: str, ubicacion: str, descripcion=None) -> tuple[float, list]:
    punt = perfiles.puntuador_de(perfil, CONFIG)
    fila = {"title": titulo, "location": ubicacion, "company": "ACME", "description": descripcion,
            "job_level": None, "is_remote": None, "ambito": "local", "job_url": "caso://" + titulo}
    return buscador.explicar_relevancia(titulo, ubicacion, None, descripcion, punt, fila)


def probar_otro() -> None:
    print("\n[9] Lo escrito en «Otro» es un puesto: llega al listón, y no cuenta dos veces (11-11)")
    # Un puesto que el catálogo no tiene, escrito en «Otro», no pasaba de 2,5 en el título: como palabra
    # clave son +1,5, y con la ciudad, 2,5. Los ejemplos son inventados.
    barcelona = {"nombre": "Barcelona", "lat": 41.39, "lon": 2.17, "radio_km": 5}
    base = {"puestos": ["admin.recepcion"], "otros": ["Logopeda"], "experiencia": "mas_10", "ubicacion": barcelona,
            "modalidad": ["presencial"], "idiomas": ["es"]}
    plan = generador.generar_plan(base)
    comprueba([p["id"] for p in plan["puestos"]] == ["admin.recepcion", "otro:logopeda"],
              "el plan lleva lo escrito como un puesto más, detrás de los del catálogo")
    comprueba(plan["puestos"][-1] == {"id": "otro:logopeda", "nombre": "Logopeda", "senales": [], "negativos": [],
                                     "nucleo": generador.nucleo_de_otro("Logopeda")},
              "   con su núcleo y sin señales ni negativos (no hay de dónde sacarlos)")
    comprueba("Logopeda" in plan["terminos"] and plan["claves_mas"] == ["Logopeda"],
              "   se sigue buscando, y sigue entre las palabras clave (por su +0,5 en la descripción)")
    comprueba("«Logopeda»" in plan["explicacion"]["ficha"], "   y la ficha lo sigue enseñando")
    con = de_catalogo(980, plan)
    for titulo in ("Logopeda", "Logopedas (H/M)", "LOGOPEDA para clínica"):
        llega_, nota, motivos = llega(con, titulo, "Barcelona, Spain")
        comprueba(llega_ and nota == 5.0 and ("puesto: Logopeda", 4.0) in motivos,
                  "«%s» en su ciudad llega con 5,0: el puesto (+4,0) y la ciudad, ni más ni menos" % titulo)
    nota, motivos = nota_de(con, "Logopeda", "Barcelona, Spain")
    comprueba(not any("en el título" in m for m, _ in motivos),
              "   sin sumar además el +1,5 de palabra clave: lo escrito a mano no vale más que un puesto del catálogo")
    nota, motivos = nota_de(con, "Recepcionista", "Barcelona, Spain", "Clínica con logopeda y fisioterapeuta.")
    comprueba(nota == 5.5 and ("«Logopeda» en la descripción", 0.5) in motivos,
              "en la descripción sigue dando su +0,5, que es lo que distingue una recepción de otra")
    nota, motivos = nota_de(con, "Recepcionista y logopeda", "Barcelona, Spain")
    comprueba(nota == 5.0 and [m for m, _ in motivos if m.startswith("puesto:")] == ["puesto: recepcionista (oficina, clínica, gimnasio…)"],
              "un título que es de los dos cuenta una vez, y el motivo es el del catálogo")
    comprueba(not llega(con, "Técnico de logopedia", "Barcelona, Spain")[0],
              "y lo que no es el puesto sigue sin llegar («logopedia» no es «logopeda»)")

    # CONTROL NEGATIVO: el plan que hacía el generador de antes (lo escrito solo como palabra clave) deja
    # el mismo título en 2,5. Y sirve también para lo contrario: el puntuador no ha cambiado para él.
    de_antes = copy.deepcopy(plan)
    de_antes["puestos"] = [p for p in plan["puestos"] if not p["id"].startswith("otro:")]
    antes = de_catalogo(981, de_antes)
    llega_, nota, motivos = llega(antes, "Logopeda", "Barcelona, Spain")
    comprueba(not llega_ and nota == 2.5 and ("«Logopeda» en el título", 1.5) in motivos,
              "control: con el plan de antes, «Logopeda» en su ciudad se queda en 2,5 y no llega (el fallo)")

    # El núcleo: plural, género, acentos, varias palabras en cualquier orden; y lo que no debe casar.
    casos = {
        "Interculturales": (["Mediador/a intercultural", "Técnico/a de actividades interculturales", "EDUCADOR INTERCULTURAL"],
                            ["Educador social", "Intercambio cultural"]),
        "Mediador": (["Mediador/a intercultural", "Mediadora comunitaria", "Mediadores"], ["Intermediador financiero", "Mediación"]),
        "auxiliar de vivero": (["Auxiliar de vivero", "Vivero - Auxiliar técnico", "Auxiliar viveros"],
                               ["Auxiliar administrativo", "Viverista"]),
        "camarero/a": (["Camarera de sala", "Camareros/as", "Camarer@s extra"], ["Recamarero"]),
        "Camareros": (["Camarera de sala", "Camarero/a"], ["Cámara frigorífica"]),
        "guía de montaña": (["Guía de montaña", "Guias de Montana"], ["Guía turístico", "Bicicleta de montaña"]),
        "niñera": (["Niñera interna", "Ninera fines de semana"], ["Ingeniera"]),
        "dependiente": (["Dependienta de tienda", "Dependiente/a", "Dependientes"], ["Independiente"]),
        "chef": (["Sous chef", "Chefs"], ["Chófer"]),
        "redes": (["Técnico de redes"], ["Paredes"]),
    }
    mal = []
    for escrito, (si, no) in casos.items():
        rx = buscador._compilar(generador.nucleo_de_otro(escrito))
        mal += ["«%s» no casa con «%s»" % (escrito, t) for t in si if not rx.search(t)]
        mal += ["«%s» casa con «%s»" % (escrito, t) for t in no if rx.search(t)]
    comprueba(not mal, "el núcleo aguanta plural, género, acentos y el orden de las palabras (%d títulos)%s" % (
        sum(len(a) + len(b) for a, b in casos.values()), ": " + "; ".join(mal) if mal else ""))
    comprueba(all(chr(8) not in (generador.nucleo_de_otro(e) or "") and chr(92) + "b" in generador.nucleo_de_otro(e) for e in casos),
              "   y todos llevan su límite de palabra de verdad, no un carácter de retroceso")
    comprueba(all(generador.nucleo_de_otro(e) is None for e in ("c++", "a", "ti", "  ", "de la")),
              "lo que no da para un núcleo (una letra, «c++», solo preposiciones) no lo tiene")
    corto = generador.generar_plan(dict(base, otros=["c++"]))
    comprueba([p["id"] for p in corto["puestos"]] == ["admin.recepcion"] and corto["claves_mas"] == ["c++"],
              "   y se queda de palabra clave, como antes")

    # El sector escrito a mano es un tema, no un puesto: sigue siendo palabra clave.
    sector = generador.generar_plan(dict(base, puestos=[], otros=["auxiliar vivero"], sector_otro="Paisajismo"))
    comprueba([p["id"] for p in sector["puestos"]] == ["otro:auxiliar vivero"]
              and sector["claves_mas"] == ["Paisajismo", "auxiliar vivero"]
              and sector["terminos"] == ["Paisajismo", "auxiliar vivero"],
              "el «Otro» del sector sigue siendo palabra clave (y se busca); el del puesto, puesto")
    comprueba("«Paisajismo», «auxiliar vivero»" in sector["explicacion"]["ficha"], "   y la ficha enseña los dos")
    solo_sector = generador.generar_plan(dict(base, puestos=[], otros=[], sector_otro="Paisajismo"))
    comprueba(solo_sector["puestos"] == [] and solo_sector["terminos"] == ["Paisajismo"],
              "   con solo el sector escrito hay plan, como antes")
    dos = generador.generar_plan(dict(base, otros=["Logopeda", "Logopeda ", "Dietista"]))
    comprueba([p["id"] for p in dos["puestos"]] == ["admin.recepcion", "otro:logopeda", "otro:dietista"],
              "dos veces lo mismo es un solo puesto")


def main() -> int:
    planes = probar_personas()
    probar_filtros(planes)
    probar_reglas_del_generador()
    probar_negativos_del_puntuador()
    probar_consultas(planes)
    probar_senior()
    probar_remoto()
    probar_practicas()
    probar_otro()
    print()
    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for fallo in fallos:
            print("  -", fallo)
        return 1
    print("TODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
