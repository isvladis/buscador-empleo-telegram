# -*- coding: utf-8 -*-
"""
Juegos de reglas con nombre (MP2, EVOLUCION-MULTIPERFIL.md §5.1).

Un perfil dice qué juego usa en `datos.reglas`. Tiene que poder existir más de uno a la vez:
MP3 deja `titular-2026-10` congelado para el adicional y MP4 crea `titular-ciber` para el
titular, y los dos corren en el mismo proceso.

Las regex se compilan **una vez por juego**, no una por perfil: son 29 patrones, algunos muy
largos, y dos perfiles con el mismo juego no tienen por qué pagarlos dos veces en un contenedor
con la memoria justa.

Este paquete no importa `buscador` y solo depende de `re`: `perfiles.py` lo necesita para
resolver la config efectiva, y `python -m perfiles` no debe arrastrar jobspy para eso.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from reglas import titular_2026_10, titular_ciber

# Umbral de «Encaje alto» tal y como lo pinta templates/index.html (tag rel-alta) y lo usa la
# notificación a Telegram tras cada búsqueda automática: una sola constante para no repetir el
# número mágico en JS, Python y el mensaje de Telegram por separado.
UMBRAL_ENCAJE_ALTO = 5.0

# Lo que un perfil con bloque `config` propio decide por sí mismo. Es una lista explícita y no
# «todo menos lo del sistema» a propósito: una clave nueva de config.json se queda en el sistema
# hasta que alguien decida lo contrario, en vez de pasar a ser editable por cada perfil.
# Sale de INVENTARIO-MULTIPERFIL.md §5, con dos cambios que manda el prompt de MP2 o D1:
# `idioma_min_caracteres` e `idioma_confianza_minima` son del sistema (el prompt), y
# `dias_descripcion_ocultas` también, porque la poda ya es «cuando todos la tengan oculta».
CLAVES_DE_LA_PERSONA = (
    "categorias", "ambitos", "centro_lat", "centro_lon", "centro_nombre", "max_distancia_km",
    "distancia_portal_millas", "exigir_remoto_fuera_de_barcelona", "anos_experiencia_max",
    "filtrar_senior", "niveles_excluidos", "descartar_otros_idiomas", "idiomas_permitidos",
    "descartar_fuera_de_perfil", "reglas_descarte", "empresas_excluidas", "pesos_relevancia",
    "categorias_siempre_visibles", "usar_modelo_entrenado", "dias_sin_repetir_aviso",
    "telegram_max_avisos_individuales", "jooble_terminos", "jooble_ubicacion",
)


@dataclass(frozen=True)
class Juego:
    """Unas reglas escritas a mano, con su nombre. Nadie las modifica una vez creadas."""

    nombre: str
    senales: list
    penalizaciones: list
    reglas_descripcion: list
    patron_senior: re.Pattern
    # Claves de CLAVES_DE_LA_PERSONA: de lo que parte un perfil con bloque propio.
    config: dict
    # [(nombre, peso, regex compilada)] de señales y penalizaciones, en ese orden: el orden es
    # el de los motivos que se enseñan, así que forma parte de la equivalencia.
    reglas_titulo: list = field(init=False)

    def __post_init__(self):
        compiladas = [(n, p, re.compile(rx, re.IGNORECASE))
                      for n, p, rx in list(self.senales) + list(self.penalizaciones)]
        object.__setattr__(self, "reglas_titulo", compiladas)
        # Los nombres son la clave con la que se sobrescriben los pesos desde la config
        # (`pesos_relevancia`) y el texto con el que se explica cada puntuación: no se pueden
        # repetir, o un peso ajustado afectaría a dos reglas a la vez.
        nombres = [n for n, _, _ in compiladas] + [r["nombre"] for r in self.reglas_descripcion]
        if len(set(nombres)) != len(nombres):
            raise ValueError(f"nombres de reglas repetidos en {self.nombre}")


TITULAR_2026_10 = Juego(
    nombre="titular-2026-10",
    senales=titular_2026_10.SENALES_RELEVANCIA,
    penalizaciones=titular_2026_10.PENALIZACIONES_RELEVANCIA,
    reglas_descripcion=titular_2026_10.REGLAS_DE_LA_DESCRIPCION,
    patron_senior=titular_2026_10.SENIOR_EXCLUDE,
    config=titular_2026_10.CONFIG_PERSONA,
)

TITULAR_CIBER = Juego(
    nombre="titular-ciber",
    senales=titular_ciber.SENALES_RELEVANCIA,
    penalizaciones=titular_ciber.PENALIZACIONES_RELEVANCIA,
    reglas_descripcion=titular_ciber.REGLAS_DE_LA_DESCRIPCION,
    patron_senior=titular_ciber.SENIOR_EXCLUDE,
    config=titular_ciber.CONFIG_PERSONA,
)

JUEGOS = {juego.nombre: juego for juego in (TITULAR_2026_10, TITULAR_CIBER)}


def obtener(nombre: str) -> Juego:
    """El juego de ese nombre. Un nombre desconocido es un error, no un juego por defecto:
    puntuar a alguien con las reglas de otro sería peor que no puntuarle."""
    try:
        return JUEGOS[nombre]
    except KeyError:
        raise ValueError(f"juego de reglas desconocido: {nombre!r}") from None
