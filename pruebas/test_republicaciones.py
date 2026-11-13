# -*- coding: utf-8 -*-
"""
Pruebas de que una vacante republicada no vuelve a avisar.

El caso real que lo motivó: «Cloud Security Operations Engineer | Barcelona (Híbrido)»
de Cobser Consulting entró el 22 y el 23 de octubre de 2026, las dos veces con 6,5, así
que habría avisado dos veces de la misma vacante en días consecutivos.

Importante sobre el alcance: esto NO es el dedupe. El dedupe difuso no fusiona dos
anuncios con fechas de publicación distintas, y hace bien, porque son dos publicaciones
reales. Lo que se arregla aquí es solo el AVISO.

    python pruebas/test_republicaciones.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
DATOS = tempfile.mkdtemp(prefix="test-republi-")
os.environ["JOBSPY_DATA_DIR"] = DATOS

import app  # noqa: E402
import config as config_mod  # noqa: E402

fallos: list[str] = []
hechas = 0


def comprueba(ok: bool, que: str) -> None:
    global hechas
    hechas += 1
    print("  %s %s" % ("OK   " if ok else "FALLA", que))
    if not ok:
        fallos.append(que)


def hace(dias: int) -> str:
    return (datetime.now() - timedelta(days=dias)).isoformat(timespec="seconds")


COBSER = {
    "title": "Cloud Security Operations Engineer | Barcelona (Híbrido)",
    "company": "Cobser Consulting",
    "location": "Barcelona, Catalonia, Spain",
}


def main() -> int:
    config = dict(config_mod.CONFIG_POR_DEFECTO)

    print("\n[1] La misma vacante republicada hace un día: no se avisa")
    en_base = [
        {**COBSER, "job_url": "https://linkedin.test/1", "visto_por_primera_vez": hace(1)},
        {**COBSER, "job_url": "https://linkedin.test/2", "visto_por_primera_vez": hace(0)},
    ]
    nuevas = [{**COBSER, "job_url": "https://linkedin.test/2"}]
    indice = app._indice_de_avisos_previos(en_base, nuevas, config)
    clave = app._clave_de_vacante(nuevas[0])
    comprueba(clave in indice, "la vacante aparece como ya vista")
    comprueba(indice[clave] == hace(1)[:10], "y con la fecha del anuncio anterior")

    print("\n[2] La misma vacante dos meses después: SÍ se avisa (se ha vuelto a abrir)")
    en_base = [
        {**COBSER, "job_url": "https://linkedin.test/1", "visto_por_primera_vez": hace(60)},
        {**COBSER, "job_url": "https://linkedin.test/2", "visto_por_primera_vez": hace(0)},
    ]
    indice = app._indice_de_avisos_previos(en_base, nuevas, config)
    comprueba(app._clave_de_vacante(nuevas[0]) not in indice,
              "fuera de la ventana de %s días: no cuenta como repetida"
              % config["dias_sin_repetir_aviso"])

    print("\n[3] Una vacante distinta de la misma empresa sí avisa")
    en_base = [
        {"title": "Técnico/a Helpdesk N1", "company": "Cobser Consulting",
         "job_url": "https://linkedin.test/1", "visto_por_primera_vez": hace(1)},
        {**COBSER, "job_url": "https://linkedin.test/2", "visto_por_primera_vez": hace(0)},
    ]
    indice = app._indice_de_avisos_previos(en_base, nuevas, config)
    comprueba(app._clave_de_vacante(nuevas[0]) not in indice,
              "otro puesto en la misma empresa no se confunde con una repetición")

    print("\n[4] Sin empresa NI ubicación no se afirma que sea la misma vacante")
    sin_empresa = {"title": "Técnico/a Helpdesk", "company": "", "job_url": "https://x.test/2"}
    en_base = [
        {"title": "Técnico/a Helpdesk", "company": "", "job_url": "https://x.test/1",
         "visto_por_primera_vez": hace(1)},
    ]
    indice = app._indice_de_avisos_previos(en_base, [sin_empresa], config)
    comprueba(indice == {},
              "dos ofertas sin empresa ni ubicación y con el mismo título no se dan por la misma")

    print("\n[4b] Sin empresa pero con la misma ubicación: no se repite el AVISO (caso real del 27-10)")
    workplace = {"title": "Técnico de Soporte Workplace Onsite (H/M/X) - Barcelona", "company": None,
                 "location": "Barcelona, Spain"}
    en_base = [{**workplace, "job_url": "https://es.jooble.org/jdp/-8756090583537501084",
                "visto_por_primera_vez": hace(0)}]
    nueva = {**workplace, "job_url": "https://es.jooble.org/jdp/-6387778586515724407"}
    indice = app._indice_de_avisos_previos(en_base, [nueva], config)
    comprueba(app._clave_de_vacante(nueva) in indice,
              "la misma oferta de Jooble con otro id y sin empresa ya no avisa dos veces")
    otra_ciudad = {**nueva, "location": "Madrid, Spain"}
    comprueba(app._clave_de_vacante(otra_ciudad) not in indice, "   con otra ubicación, sí avisa")

    print("\n[4c] La empresa con el país pegado es la misma empresa (caso real del 27-10)")
    irium_jooble = {"title": "Onsite IT Support Technician", "company": "IRIUM - Spain",
                    "location": "Barcelona, Spain"}
    irium_linkedin = {"title": "Onsite IT Support Technician", "company": "IRIUM",
                      "location": "Barcelona, Catalonia, Spain"}
    comprueba(app._clave_de_vacante(irium_jooble) == app._clave_de_vacante(irium_linkedin),
              "«IRIUM - Spain» (Jooble) e «IRIUM» (LinkedIn) dan la misma vacante")
    import dedupe_difuso as dd
    comprueba([dd.normalizar_empresa(e) for e in ("Adecco Spain", "Deloitte España", "Veolia ? Hungary")]
              == ["adecco", "deloitte", "veolia"], "Adecco Spain, Deloitte España, Veolia ? Hungary")
    comprueba(dd.normalizar_empresa("Concentrix Spain Business Process Outsourcing S.L.U.")
              == "concentrix spain business process outsourcing s l u",
              "el país solo se quita al final: «Concentrix Spain Business…» se queda")
    comprueba(dd.normalizar_empresa("Spain") == "", "   (una «empresa» que es solo un país queda vacía)")
    hoy = datetime.now().date().isoformat()
    a = {**irium_jooble, "job_url": "https://es.jooble.org/jdp/3134852004543925322", "site": "jooble",
         "date_posted": hoy, "description": "Onsite IT support for end users in Barcelona."}
    b = {**irium_linkedin, "job_url": "https://www.linkedin.com/jobs/view/4484086690", "site": "linkedin",
         "date_posted": hoy, "description": "Onsite IT support for end users in Barcelona."}
    comprueba(dd.son_duplicados(a, b)[0], "y el dedupe difuso las junta en una sola oferta")

    print("\n[5] El título normaliza mayúsculas y espacios")
    en_base = [
        {"title": "  CLOUD SECURITY OPERATIONS ENGINEER | Barcelona (Híbrido)  ",
         "company": "COBSER  CONSULTING", "job_url": "https://x.test/1",
         "visto_por_primera_vez": hace(2)},
    ]
    indice = app._indice_de_avisos_previos(en_base, nuevas, config)
    comprueba(app._clave_de_vacante(nuevas[0]) in indice,
              "mayúsculas y espacios de más no impiden reconocerla")

    print("\n[6] Se puede desactivar (dias_sin_repetir_aviso = 0)")
    en_base = [
        {**COBSER, "job_url": "https://linkedin.test/1", "visto_por_primera_vez": hace(1)},
    ]
    indice = app._indice_de_avisos_previos(en_base, nuevas, dict(config, dias_sin_repetir_aviso=0))
    comprueba(indice == {}, "con 0 no se filtra nada: vuelve al comportamiento de antes")

    print("\n[7] La excepción del SOC junior sigue avisando aunque se repita")
    soc = {"title": "Analista SOC Junior N1", "company": "Una Consultora",
           "job_url": "https://x.test/soc2"}
    # categorias_siempre_visibles trae por defecto el patrón de SOC junior
    comprueba(any(r.get("activa") for r in config["categorias_siempre_visibles"]),
              "la categoría «siempre visible» está activa por defecto")
    import almacen
    comprueba(almacen.es_categoria_siempre_visible(soc, config) is True,
              "«Analista SOC Junior N1» cae en la categoría siempre visible")
    comprueba(almacen.es_categoria_siempre_visible(dict(COBSER), config) is False,
              "y la de Cobser no, así que a esa sí se le aplica el filtro")

    if fallos:
        print("\nFALLOS (%d de %d):" % (len(fallos), hechas))
        for f in fallos:
            print("  - %s" % f)
        return 1
    print("\nTODO BIEN: %d comprobaciones" % hechas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
