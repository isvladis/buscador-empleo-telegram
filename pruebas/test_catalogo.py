# -*- coding: utf-8 -*-
"""
Pruebas del catálogo de familias profesionales (MP7): `perfilado/catalogo.json` y sus muestras.

    python pruebas/test_catalogo.py

Lo que se comprueba, y por qué:

1. El JSON es lo que sale de `perfilado/fuente.py`, sin un carácter de retroceso (el `"\\b"` sin `r`).
2. El formato: 14 familias de 4 a 8 puestos, ids únicos, todo puesto con términos y núcleo, EURES en
   todas y Tecnoempleo solo en IT, los `choca_con` y `tambien_en` apuntan a algo que existe, y un mismo
   nombre de motivo es siempre la misma regla (el puntuador cuenta cada nombre una vez).
3. Las regex: todas compilan y ninguna tarda más de 5 ms contra 5.000 caracteres (retroceso
   catastrófico: el puntuador las pasa por cada título de cada corrida).
4. Las muestras (`pruebas/catalogo_muestras.json`): los «si» casan con su núcleo, los «no» no, y las
   «rebajadas» casan pero un negativo del propio puesto las baja.
5. Los puestos de IT siguen llevando las cadenas de `reglas/`: si alguien cambia `titular-ciber`, esto
   lo dice antes de que el catálogo se quede con la versión vieja.
6. Ningún negativo ni descarte de un puesto castiga un título que otro puesto debe aceptar, salvo que
   ese otro puesto esté en su `choca_con` (el generador la quita entonces).

Cada bloque lleva su control negativo: la misma comprobación sobre algo roto a propósito tiene que
fallar. Nada sale a la red.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from perfilado import construir, fuente  # noqa: E402
from reglas import titular_2026_10 as base  # noqa: E402
from reglas import titular_ciber as ciber  # noqa: E402

fallos: list[str] = []
hechas = 0

CATALOGO = json.loads(construir.RUTA.read_text(encoding="utf-8"))
MUESTRAS = json.loads((AQUI / "catalogo_muestras.json").read_text(encoding="utf-8"))
FAMILIAS_DEL_PLAN = ["it", "admin", "atencion", "comercial", "marketing", "finanzas", "rrhh", "sanidad",
                     "educacion", "hosteleria", "comercio", "logistica", "industria", "oficios"]
FUENTES = {"linkedin", "indeed", "infojobs", "eures", "jooble", "tecnoempleo"}
# Los motivos que pone el propio puntuador (buscador.PuntuadorCatalogo). Un nombre del catálogo igual
# que uno de estos saldría dos veces en la explicación de una oferta.
MOTIVOS_FIJOS = {"junior o sin experiencia", "prácticas, que aceptas", "prácticas o becario",
                 "senior, lead o manager", "junior, por debajo de tu nivel", "idioma que no hablas",
                 "salario por debajo de tu mínimo", "jornada que no quieres", "sin experiencia o 1 año"}
TOPE_MS = 5.0
# Las regex que heredan una cadena de reglas/: no se pueden acelerar sin tocar el juego del titular, y
# `_PUESTO_CIBER` de titular-ciber ya tarda 5,4 ms por sí sola contra 5.000 caracteres (medido el 05-11
# en el PC). No es retroceso catastrófico, es una alternancia grande probada en cada posición: crece en
# línea recta. Para esas el tope es lo que ya tarda la cadena heredada, con un 30 % de margen.
MARGEN_HEREDADA = 1.3
# Retroceso catastrófico es que el tiempo crezca más que el texto: con el doble de texto, más del
# triple de tiempo.
CRECIMIENTO_MAXIMO = 3.0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def puestos(catalogo=None) -> dict:
    return {p["id"]: p for f in (catalogo or CATALOGO)["familias"] for p in f["puestos"]}


def cubre(regla: dict, destino: str) -> bool:
    return any(destino == d or destino.startswith(d + ".") for d in regla.get("choca_con") or ())


def titulos(puesto_id: str, lado: str, muestras=None) -> list[str]:
    return [t["titulo"] for t in (muestras or MUESTRAS).get(puesto_id, {}).get(lado, [])]


# ----------------------------------------------------------------------
def probar_al_dia() -> None:
    print("\n[1] El JSON es el de fuente.py")
    texto = construir.RUTA.read_text(encoding="utf-8")
    comprueba(texto == construir.contenido(), "catalogo.json = python -m perfilado.construir")
    crudo = construir.RUTA.read_bytes()
    # En las cadenas ya leídas, no en los bytes: json.dumps escribe el 0x08 como «\b» (dos caracteres
    # normales) y contar bytes([8]) en el fichero da 0 aunque esté roto. Pasó con esta misma prueba.
    comprueba(not construir.retrocesos(CATALOGO), "sin carácter de retroceso (0x08) en las cadenas del catálogo")
    comprueba(b"\r\n" not in crudo, "sin finales de línea de Windows")
    comprueba(not construir.retrocesos(MUESTRAS), "sin 0x08 en las muestras")
    comprueba(construir.retrocesos({"a": ["x", chr(8) + "fy"]}) == ["/a/1"],
              "   control: un 0x08 se encuentra y se dice dónde")
    comprueba(construir.main(["--comprobar"]) == 0, "construir --comprobar dice que está al día")
    # Control negativo: un cambio en la fuente que no se ha volcado tiene que notarse.
    viejo = fuente.FAMILIAS[0]["puestos"][0]["nucleo"]
    try:
        fuente.FAMILIAS[0]["puestos"][0]["nucleo"] = viejo + "|algo nuevo"
        comprueba(construir.contenido() != texto, "   control: cambiar la fuente deja el JSON atrás")
        comprueba(construir.main(["--comprobar"]) == 1, "   control: --comprobar lo detecta")
        # El fallo del 04-11, a propósito: "\bfy" sin r'' es un retroceso seguido de «fy».
        fuente.FAMILIAS[0]["puestos"][0]["nucleo"] = viejo + "|" + chr(8) + r"fy\d{2}"
        comprueba(construir.main([]) == 1 and construir.RUTA.read_text(encoding="utf-8") == texto,
                  "   control: un \\b sin r'' no llega a escribirse en el JSON")
    finally:
        fuente.FAMILIAS[0]["puestos"][0]["nucleo"] = viejo


# ----------------------------------------------------------------------
def errores_de_formato(catalogo: dict) -> list[str]:
    errores = []
    familias = catalogo["familias"]
    if [f["id"] for f in familias] != FAMILIAS_DEL_PLAN:
        errores.append("las familias no son las 14 del §4.2, en su orden")
    ids_familia = {f["id"] for f in familias}
    todos = []
    for f in familias:
        if not 4 <= len(f["puestos"]) <= 8:
            errores.append("%s tiene %d puestos" % (f["id"], len(f["puestos"])))
        todos.append(f["otro"]["id"])
        for p in f["puestos"]:
            todos.append(p["id"])
            if not p["id"].startswith(f["id"] + "."):
                errores.append("%s no empieza por su familia" % p["id"])
            t = p.get("terminos") or {}
            if set(t) != {"es", "en", "ca"}:
                errores.append("%s: terminos sin es/en/ca" % p["id"])
            if not (t.get("es") or t.get("en")):
                errores.append("%s: sin términos" % p["id"])
            if not (p.get("nucleo") or "").strip():
                errores.append("%s: sin núcleo" % p["id"])
            fuentes = set(p.get("fuentes") or [])
            if not fuentes or not fuentes <= FUENTES or "eures" not in fuentes:
                errores.append("%s: fuentes %s" % (p["id"], sorted(fuentes)))
            if ("tecnoempleo" in fuentes) != (f["id"] == "it"):
                errores.append("%s: Tecnoempleo solo en IT" % p["id"])
            for otra in p.get("tambien_en") or []:
                if otra not in ids_familia or otra == f["id"]:
                    errores.append("%s: tambien_en %r" % (p["id"], otra))
            for s in p["senales"]:
                if not s["peso"] > 0:
                    errores.append("%s: señal %r sin peso positivo" % (p["id"], s["nombre"]))
            for neg in p["negativos"]:
                if not neg["peso"] < 0:
                    errores.append("%s: negativo %r que no resta" % (p["id"], neg["nombre"]))
    if len(todos) != len(set(todos)):
        errores.append("ids repetidos")
    validos = set(todos) | ids_familia
    reglas_por_nombre = {}
    for p in puestos(catalogo).values():
        for tipo in ("senales", "negativos", "descartes"):
            for regla in p[tipo]:
                for destino in regla.get("choca_con") or ():
                    if destino not in validos:
                        errores.append("%s: choca_con %r no existe" % (p["id"], destino))
                if cubre(regla, p["id"]):
                    errores.append("%s: %r choca consigo mismo (no se aplicaría nunca)" % (p["id"], regla["nombre"]))
                if tipo == "descartes":
                    continue
                if regla["nombre"] in MOTIVOS_FIJOS:
                    errores.append("%s: %r es un motivo del puntuador" % (p["id"], regla["nombre"]))
                firma = (tipo, regla["patron"], regla["peso"])
                if reglas_por_nombre.setdefault(regla["nombre"], firma) != firma:
                    errores.append("%r: el mismo nombre con dos reglas distintas" % regla["nombre"])
    return errores


def probar_formato() -> None:
    print("\n[2] Formato del catálogo")
    errores = errores_de_formato(CATALOGO)
    for e in errores[:10]:
        print("      ", e)
    comprueba(not errores, "14 familias de 4–8 puestos, ids únicos, términos, núcleo, fuentes y referencias")
    n = len(puestos())
    comprueba(n == 94, "94 puestos, los aprobados por el titular el 04-11 (%d)" % n)
    comprueba(all(f["otro"]["id"] == f["id"] + ".otro" for f in CATALOGO["familias"]),
              "cada familia tiene su «otro»")
    for pid, fam in (("admin.recepcion", "sanidad"), ("educacion.ninera", "sanidad"),
                     ("educacion.civico", "oficios"), ("marketing.traduccion", "educacion"),
                     ("industria.civil", "oficios")):
        comprueba(fam in (puestos()[pid].get("tambien_en") or []), "%s también se ofrece en %s" % (pid, fam))
    # Controles negativos: cada tipo de error se detecta.
    roto = copy.deepcopy(CATALOGO)
    roto["familias"][1]["puestos"][1]["id"] = roto["familias"][1]["puestos"][0]["id"]
    comprueba(any("repetidos" in e for e in errores_de_formato(roto)), "   control: un id repetido")
    roto = copy.deepcopy(CATALOGO)
    roto["familias"][2]["puestos"][0]["fuentes"].append("tecnoempleo")
    comprueba(any("Tecnoempleo" in e for e in errores_de_formato(roto)), "   control: Tecnoempleo fuera de IT")
    roto = copy.deepcopy(CATALOGO)
    roto["familias"][3]["puestos"][0]["terminos"] = {"es": [], "en": [], "ca": []}
    comprueba(any("sin términos" in e for e in errores_de_formato(roto)), "   control: un puesto sin términos")
    roto = copy.deepcopy(CATALOGO)
    neg = roto["familias"][1]["puestos"][0]["negativos"][0]
    roto["familias"][2]["puestos"][0]["negativos"].append(dict(neg, patron="otra cosa"))
    comprueba(any("dos reglas distintas" in e for e in errores_de_formato(roto)),
              "   control: el mismo nombre con otro patrón")
    roto = copy.deepcopy(CATALOGO)
    roto["familias"][0]["puestos"][0]["negativos"][0]["choca_con"] = ["it"]
    comprueba(any("consigo mismo" in e for e in errores_de_formato(roto)), "   control: un negativo que se anula solo")


# ----------------------------------------------------------------------
def patrones(catalogo=None) -> list[tuple[str, str]]:
    salida = []
    for p in puestos(catalogo).values():
        salida.append((p["id"] + " núcleo", p["nucleo"]))
        for tipo in ("senales", "negativos", "descartes"):
            for regla in p[tipo]:
                salida.append(("%s %s" % (p["id"], regla["nombre"]), regla["patron"]))
                if regla.get("excepcion"):
                    salida.append(("%s %s (excepción)" % (p["id"], regla["nombre"]), regla["excepcion"]))
    return salida


def textos_largos() -> list[str]:
    """5.000 caracteres de lo que más hace retroceder: lo que casi casa, repetido."""
    todos = [t["titulo"] for p in MUESTRAS.values() for lado in p.values() for t in lado]
    mezcla = " ".join(todos)
    return [(x * (5000 // len(x) + 1))[:5000] for x in (
        "a", "técnico/a de ", "camarero/a ", "Técnico/a de Soporte N1 - Helpdesk ", "aux. ",
        "customer service english speaking ", "/-/ ", mezcla)]


def milisegundos(rx: re.Pattern, textos: list[str]) -> float:
    peor = 0.0
    for texto in textos:
        mejor = None
        for _ in range(3):  # el mínimo de tres: lo que tarda la regex, no lo que tarda Windows
            inicio = time.perf_counter()
            rx.search(texto)
            dura = (time.perf_counter() - inicio) * 1000
            mejor = dura if mejor is None else min(mejor, dura)
        peor = max(peor, mejor)
    return peor


def probar_regex() -> None:
    print("\n[3] Las regex: compilan y no se cuelgan")
    lista = patrones()
    malas = []
    for nombre, patron in lista:
        try:
            re.compile(patron, re.IGNORECASE)
        except re.error as error:
            malas.append("%s: %s" % (nombre, error))
    comprueba(not malas, "las %d regex compilan%s" % (len(lista), (": " + malas[0]) if malas else ""))
    textos = textos_largos()
    mitades = [x[:2500] for x in textos]
    heredadas = [base._PUESTO_SOPORTE_IT, ciber._PUESTO_CIBER]
    tope_heredada = {h: MARGEN_HEREDADA * milisegundos(re.compile(h, re.IGNORECASE), textos) for h in heredadas}
    lentas, crecen, excepciones = [], [], []
    peor = (0.0, "")
    for nombre, patron in lista:
        rx = re.compile(patron, re.IGNORECASE)
        ms = milisegundos(rx, textos)
        tope = max([TOPE_MS] + [tope_heredada[h] for h in heredadas if h in patron])
        if ms > TOPE_MS:
            excepciones.append("%s %.1f ms (tope %.1f)" % (nombre, ms, tope))
        peor = max(peor, (ms, nombre))
        if ms > tope:
            lentas.append("%s %.1f ms" % (nombre, ms))
        mitad = milisegundos(rx, mitades)
        if mitad > 0.2 and ms / mitad > CRECIMIENTO_MAXIMO:
            crecen.append("%s ×%.1f" % (nombre, ms / mitad))
    comprueba(not lentas, "ninguna pasa de su tope contra 5.000 caracteres (la peor: %s, %.2f ms)%s" % (
        peor[1], peor[0], (": " + ", ".join(lentas[:3])) if lentas else ""))
    print("       por encima de %g ms: %s" % (TOPE_MS, "; ".join(excepciones) or "ninguna"))
    comprueba(all(e.startswith("it.ciber núcleo") for e in excepciones),
              "   y por encima de %g ms solo it.ciber, que lleva entera la señal de titular-ciber" % TOPE_MS)
    comprueba(not crecen, "ninguna crece más que el texto (de 2.500 a 5.000 caracteres, ×%g como mucho)%s" % (
        CRECIMIENTO_MAXIMO, (": " + ", ".join(crecen[:3])) if crecen else ""))
    # Control negativo: una regex con retroceso catastrófico tiene que pasar del tope y crecer más que
    # el texto. Con 22 «x» ya son millones de intentos; con 5.000 no acabaría.
    catastrofica = re.compile(r"(x+x+)+y")
    comprueba(milisegundos(catastrofica, ["x" * 22]) > TOPE_MS, "   control: «(x+x+)+y» se detecta como lenta")
    comprueba(milisegundos(catastrofica, ["x" * 20]) / max(1e-6, milisegundos(catastrofica, ["x" * 10]))
              > CRECIMIENTO_MAXIMO, "   control: y su tiempo crece mucho más que el texto")


# ----------------------------------------------------------------------
def errores_de_muestras(catalogo: dict, muestras: dict) -> list[str]:
    errores = []
    for pid, p in puestos(catalogo).items():
        m = muestras.get(pid)
        if not m:
            errores.append("%s sin muestras" % pid)
            continue
        if len(m.get("si", [])) < 8 or len(m.get("no", [])) < 8:
            errores.append("%s: %d si y %d no (mínimo 8 y 8)" % (pid, len(m.get("si", [])), len(m.get("no", []))))
        nucleo = re.compile(p["nucleo"], re.IGNORECASE)
        propios = [re.compile(n["patron"], re.IGNORECASE) for n in p["negativos"]]
        for t in titulos(pid, "si", muestras):
            if not nucleo.search(t):
                errores.append("%s: «%s» debía casar" % (pid, t))
            elif any(rx.search(t) for rx in propios):
                errores.append("%s: «%s» lo baja un negativo suyo (¿es una rebajada?)" % (pid, t))
        for t in titulos(pid, "no", muestras):
            if nucleo.search(t):
                errores.append("%s: «%s» no debía casar" % (pid, t))
        for t in titulos(pid, "rebajadas", muestras):
            if not nucleo.search(t) or not any(rx.search(t) for rx in propios):
                errores.append("%s: «%s» debía casar y bajarla un negativo suyo" % (pid, t))
    return errores


def probar_muestras() -> None:
    print("\n[4] Las muestras")
    errores = errores_de_muestras(CATALOGO, MUESTRAS)
    for e in errores[:10]:
        print("      ", e)
    total = sum(len(v) for m in MUESTRAS.values() for v in m.values())
    reales = sum(1 for m in MUESTRAS.values() for v in m.values() for t in v if not t.get("sintetico"))
    comprueba(not errores, "los %d títulos aciertan (%d reales de la base, %d inventados)" % (
        total, reales, total - reales))
    comprueba(set(MUESTRAS) == set(puestos()), "un juego de muestras por puesto, ni más ni menos")
    comprueba(sum(1 for m in MUESTRAS.values() if m.get("rebajadas")) >= 15,
              "al menos 15 puestos prueban sus negativos con «rebajadas»")
    print("\n      Por familia: títulos reales / total")
    for f in CATALOGO["familias"]:
        ts = [t for p in f["puestos"] for v in MUESTRAS[p["id"]].values() for t in v]
        print("        %-11s %4d / %4d" % (f["id"], sum(1 for t in ts if not t.get("sintetico")), len(ts)))
    # Controles negativos: los dos fallos que ya pasaron una vez.
    roto = copy.deepcopy(CATALOGO)
    p = puestos(roto)["educacion.monitor"]
    p["nucleo"] = p["nucleo"].replace(r"\bmonitor(?:a|es|as|@)?\b", r"\bmonitor\w*")
    comprueba(any("Monitorización" in e for e in errores_de_muestras(roto, MUESTRAS)),
              "   control: «monitor\\w*» casa con «Técnico/a de Monitorización» y se ve")
    roto = copy.deepcopy(CATALOGO)
    p = puestos(roto)["hosteleria.camarero"]
    p["nucleo"] = p["nucleo"].replace(r"(?!(?:/as?|\(a\))? de (?:pisos|planta|habitaciones))(?:/as?|\(a\))?(?!\w)",
                                      r"(?:/as?|\(a\))?(?! de (?:pisos|planta|habitaciones))(?!\w)")
    comprueba(any("Camarero/a de pisos" in e for e in errores_de_muestras(roto, MUESTRAS)),
              "   control: la exclusión sin el «/a» deja pasar «Camarero/a de pisos»")
    roto = copy.deepcopy(CATALOGO)
    puestos(roto)["it.desarrollo"]["negativos"] = []
    comprueba(any("Desarrollador/a de negocio" in e for e in errores_de_muestras(roto, MUESTRAS)),
              "   control: sin sus negativos, las rebajadas de desarrollo no bajan")


# ----------------------------------------------------------------------
def errores_de_it(catalogo: dict) -> list[str]:
    p = puestos(catalogo)
    errores = []
    contiene = {
        "it.soporte": [base._PUESTO_SOPORTE_IT],
        "it.sistemas": [fuente._SYSADMIN],
        "it.ciber": [ciber._PUESTO_CIBER],
        "it.desarrollo": [next(r["patron"] for r in ciber.REGLAS_DESCARTE if r["nombre"] == "Desarrollo de software")],
        "it.datos": [next(r["patron"] for r in ciber.REGLAS_DESCARTE if r["nombre"] == "Datos, BI e IA")],
        "it.qa": [next(x for n, _, x in base.PENALIZACIONES_RELEVANCIA if n == "QA o testing")],
        "it.cloud": [next(x for n, _, x in base.PENALIZACIONES_RELEVANCIA if n == "DevOps o cloud engineer")],
    }
    for pid, cadenas in contiene.items():
        for cadena in cadenas:
            if cadena not in p[pid]["nucleo"]:
                errores.append("%s ya no lleva la cadena de reglas/" % pid)
    if p["it.soporte"]["nucleo"] != base._PUESTO_SOPORTE_IT:
        errores.append("it.soporte no es la señal de soporte de titular-2026-10")
    if fuente._SYSADMIN not in base._PUESTO_SOPORTE_IT:
        errores.append("el administrador de sistemas ya no está en titular-2026-10")
    de_ciber = {r["nombre"]: r for r in ciber.REGLAS_DESCARTE}
    for pid, puesto in p.items():
        if not pid.startswith("it."):
            continue
        for regla in puesto["descartes"]:
            original = de_ciber.get(regla["nombre"])
            if original is None or (regla["patron"], regla.get("excepcion")) != (original["patron"], original.get("excepcion")):
                errores.append("%s: el descarte %r no es el de titular-ciber" % (pid, regla["nombre"]))
        for s in puesto["senales"]:
            fila = [x for x in base.SENALES_RELEVANCIA if x[0] == s["nombre"]]
            if fila and fila[0][2] != s["patron"]:
                errores.append("%s: la señal %r no es la de titular-2026-10" % (pid, s["nombre"]))
    return errores


def probar_it() -> None:
    print("\n[5] Los puestos de IT y las cadenas de reglas/")
    comprueba(len(ciber.REGLAS_DESCARTE) == 26,
              "titular-ciber tiene 26 reglas de descarte, con la de prácticas del 04-11 (%d)" % len(ciber.REGLAS_DESCARTE))
    errores = errores_de_it(CATALOGO)
    for e in errores[:5]:
        print("      ", e)
    comprueba(not errores, "núcleos, señales y descartes copiados son los de titular-2026-10 y titular-ciber")
    copiados = sorted({r["nombre"] for pid, p in puestos().items() if pid.startswith("it.") for r in p["descartes"]})
    comprueba(copiados == ["Falsos amigos de IT (baja IT, preventa)", "Investigación académica",
                           "Oficios no técnicos (sanidad, obra, PRL…)"],
              "los descartes de IT son los tres del oficio, no los del gusto del titular")
    comprueba("Programas de prácticas o de recién titulado" not in copiados,
              "   ni la regla de prácticas del titular: el catálogo las decide con «practicas»")
    # Control negativo: si titular-ciber cambia y el catálogo no, se ve.
    roto = copy.deepcopy(CATALOGO)
    puestos(roto)["it.soporte"]["nucleo"] = base._PUESTO_SOPORTE_IT.replace("help ?desk", "helpdesk")
    comprueba(bool(errores_de_it(roto)), "   control: un núcleo de IT desincronizado se detecta")
    roto = copy.deepcopy(CATALOGO)
    puestos(roto)["it.soporte"]["descartes"][0]["patron"] += "|algo"
    comprueba(bool(errores_de_it(roto)), "   control: un descarte copiado que se aparta se detecta")


# ----------------------------------------------------------------------
def choques(catalogo: dict, muestras: dict) -> list[str]:
    """Reglas de A que castigan un «si» de B sin que B esté en su choca_con. Los negativos solo cuentan
    si el título casa también con el núcleo de A, que es cuando el puntuador los aplica; los descartes
    tiran la oferta siempre."""
    todos = puestos(catalogo)
    nucleos = {pid: re.compile(p["nucleo"], re.IGNORECASE) for pid, p in todos.items()}
    errores = []
    for a, pa in todos.items():
        for tipo in ("negativos", "descartes"):
            for regla in pa[tipo]:
                rx = re.compile(regla["patron"], re.IGNORECASE)
                ex = re.compile(regla["excepcion"], re.IGNORECASE) if regla.get("excepcion") else None
                for b in todos:
                    if b == a or cubre(regla, b):
                        continue
                    for t in titulos(b, "si", muestras):
                        if rx.search(t) and not (ex and ex.search(t)) and (
                                tipo == "descartes" or nucleos[a].search(t)):
                            errores.append("%s «%s» castiga «%s» de %s" % (a, regla["nombre"], t, b))
                            break
    return errores


def probar_choques() -> None:
    print("\n[6] Ninguna regla castiga a otro puesto que la persona haya elegido")
    errores = choques(CATALOGO, MUESTRAS)
    for e in errores[:10]:
        print("      ", e)
    comprueba(not errores, "negativos y descartes contra los «si» de los otros 93 puestos")
    # Control negativo: sin el choca_con de la preventa, el «Técnico/a Soporte Comercial B2B» de
    # Administración (base) queda castigado por los puestos de IT.
    roto = copy.deepcopy(CATALOGO)
    for p in puestos(roto).values():
        for regla in p["negativos"]:
            if regla["nombre"] == "preventa, comercial o fraude":
                regla.pop("choca_con", None)
    comprueba(any("preventa" in e for e in choques(roto, MUESTRAS)), "   control: quitar un choca_con se ve")
    roto = copy.deepcopy(CATALOGO)
    for regla in puestos(roto)["it.soporte"]["descartes"]:
        regla.pop("choca_con", None)
    comprueba(any("Oficios no técnicos" in e for e in choques(roto, MUESTRAS)),
              "   control: el descarte de oficios sin choca_con tumba a otras familias")


def main() -> int:
    probar_al_dia()
    probar_formato()
    probar_regex()
    probar_muestras()
    probar_it()
    probar_choques()
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
