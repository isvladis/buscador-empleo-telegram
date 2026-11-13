# -*- coding: utf-8 -*-
"""
Vuelve a generar el plan de los perfiles de catálogo con el generador de hoy.

    python -m perfilado.regenerar               # ensayo: dice qué cambiaría, no escribe nada
    python -m perfilado.regenerar --confirmar   # lo escribe

POR QUÉ EXISTE. El plan de un perfil se genera al terminar la encuesta o al guardar algo en /ajustes, y
desde ahí es autosuficiente (§5.5): la corrida no vuelve a mirar ni las respuestas ni el catálogo. Eso
quiere decir que **un arreglo del generador no le llega a nadie que ya tenga plan** hasta que esa persona
toque algo. El 11-11 hizo falta por primera vez: lo escrito en «✏️ Otro» pasó de palabra clave (+1,5, que
no llega nunca al listón) a puesto (+4,0), y los dos perfiles que lo habían escrito seguían con el plan de
antes (`REVISION-2026-11-11.md` §2). También sirve cuando se corrige un puesto en `perfilado/fuente.py`.

QUÉ HACE Y QUÉ NO.

- Solo perfiles **de catálogo y activos**. Los de reglas a mano (el titular, la plantilla) no tienen plan
  que generar. Los pausados se dejan: escribir en su fila movería `actualizado`, que es el reloj de su
  pausa (`avisos_perfil._vigilar_pausado`), y su plan de antes sigue funcionando como funcionaba.
- Regenera **desde sus respuestas**, que es exactamente lo que hace /ajustes al guardar: el listón, los
  avisos, lo aprendido y todo lo demás del perfil se quedan como están.
- **No avisa a nadie** (regla 1) y **no vuelve a mandar nada**: las ofertas que ya están en la lista de
  la persona no se reenvían aunque ahora valgan más; lo nuevo se nota en las que lleguen desde ahora (y en
  /top).
- El plan de antes queda en `datos.historial` con su fecha (regla 7). Volver atrás es ponerlo otra vez.
- Es idempotente: si el plan que sale es el que ya tiene, no escribe ni sube la versión.
- No enseña ningún chat_id: los perfiles van numerados por fecha de alta, como en `REVISION-PILOTO.md`.

Lo ejecuta el titular, dentro del contenedor y con el servicio en marcha (una transacción por perfil; la
corrida siguiente ya lee el plan nuevo):

    sudo docker exec jobspy-buscador python -m perfilado.regenerar
    sudo docker exec jobspy-buscador python -m perfilado.regenerar --confirmar
"""

from __future__ import annotations

import sys

SECCION = "plan regenerado"


def _resumen(plan: dict) -> dict:
    return {"puestos": [p.get("nombre") or p.get("id") for p in plan.get("puestos") or []],
            "terminos": list(plan.get("terminos") or []),
            "consultas": (plan.get("explicacion") or {}).get("consultas"),
            "descartes": [d.get("nombre") for d in plan.get("descartes") or []],
            "umbral": plan.get("umbral")}


def regenerar(confirmar: bool = False, decir=print) -> dict:
    """Devuelve {"mirados", "cambian", "escritos", "fallidos"}. Sin `confirmar` no escribe nada."""
    import perfiles
    import telegram_encuesta

    cuenta = {"mirados": 0, "cambian": 0, "escritos": 0, "fallidos": 0}
    todos = sorted(perfiles.listar(), key=lambda p: (str(p.get("creado") or ""), p["chat_id"]))
    for numero, perfil in enumerate(todos, 1):
        nombre = "Perfil %d de %d" % (numero, len(todos))
        datos = perfil.get("datos") or {}
        if datos.get("reglas") != "catalogo" or not datos.get("plan"):
            decir("%s: no es de catálogo (reglas a mano o encuesta a medias); no se toca." % nombre)
            continue
        if perfil.get("estado") != "activo":
            decir("%s: está %s; se deja como está (se regenerará cuando cambie algo en /ajustes)." % (
                nombre, perfil.get("estado")))
            continue
        cuenta["mirados"] += 1
        try:
            nuevo = telegram_encuesta.plan_de(telegram_encuesta.respuestas_de(datos))
        except Exception as e:  # noqa: BLE001 — unas respuestas que ya no generan no pueden parar a los demás
            cuenta["fallidos"] += 1
            decir("%s: sus respuestas ya no generan un plan (%s: %s); se queda con el que tiene." % (
                nombre, type(e).__name__, e))
            continue
        viejo = datos["plan"]
        if nuevo == viejo:
            decir("%s: su plan ya es el de hoy; nada que hacer." % nombre)
            continue
        cuenta["cambian"] += 1
        antes, despues = _resumen(viejo), _resumen(nuevo)
        decir("%s: su plan cambia." % nombre)
        for clave in ("puestos", "terminos", "consultas", "descartes", "umbral"):
            if antes[clave] != despues[clave]:
                decir("    %s: %s  ->  %s" % (clave, antes[clave], despues[clave]))
        if all(antes[c] == despues[c] for c in antes):
            decir("    (cambia por dentro: reglas de algún puesto; puestos, términos, consultas y listón, iguales)")
        if not confirmar:
            continue

        def cambiar(d, nuevo=nuevo):
            if d.get("plan") == nuevo:
                return
            d["historial"] = ((d.get("historial") or []) + [
                {"fecha": perfiles._ahora(), "seccion": SECCION, "plan_anterior": d.get("plan")}
            ])[-telegram_encuesta.HISTORIAL_MAXIMO:]
            d["plan"] = nuevo

        perfiles.actualizar_datos(perfil["chat_id"], cambiar)
        cuenta["escritos"] += 1
        decir("    guardado; el plan de antes queda en su historial.")
    if cuenta["cambian"] and not confirmar:
        decir("\nEnsayo: no se ha escrito nada. Para aplicarlo, repite con --confirmar.")
    return cuenta


def main(argumentos: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argumentos is None else argumentos
    desconocidos = [a for a in argumentos if a != "--confirmar"]
    if desconocidos:
        print(__doc__)
        return 2
    cuenta = regenerar("--confirmar" in argumentos)
    print("\n%d perfil(es) de catálogo mirados: %d cambian, %d escritos, %d con respuestas que ya no generan." % (
        cuenta["mirados"], cuenta["cambian"], cuenta["escritos"], cuenta["fallidos"]))
    return 1 if cuenta["fallidos"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
