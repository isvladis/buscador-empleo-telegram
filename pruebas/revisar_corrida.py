# -*- coding: utf-8 -*-
"""
Revisión de las últimas corridas, para lanzarla a mano en el Toshiba (como vc, sin sudo).

SOLO LEE data/: no escribe nada, no llama a ninguna API y nunca muestra config.json ni el
.env (pueden llevar secretos). Solo usa la biblioteca estándar, porque corre con el Python
del servidor, fuera del contenedor.

    python3 ~/proyectos/jobspy/pruebas/revisar_corrida.py          # últimas 24 h
    python3 ~/proyectos/jobspy/pruebas/revisar_corrida.py 48       # últimas 48 h

Las horas de data/ están en UTC (el contenedor va en UTC): en invierno, una hora menos
que la peninsular; en verano, dos.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comprobar_datos import abrir_sin_escribir  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
FUENTES = ("tecnoempleo", "jooble", "infojobs", "eures")
UMBRAL_AVISO = 5.0


def leer(nombre, por_defecto):
    try:
        return json.loads((DATA / nombre).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return por_defecto
    except (OSError, ValueError) as e:
        print("  (no se puede leer %s: %s)" % (nombre, e))
        return por_defecto


def titulo(texto):
    print("\n" + "=" * 70 + "\n" + texto + "\n" + "=" * 70)


def main() -> int:
    horas = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 24
    desde = (datetime.utcnow() - timedelta(hours=horas)).isoformat(timespec="seconds")
    print("Revisión de las últimas %d h (desde %s UTC)" % (horas, desde.replace("T", " ")))

    try:
        base = abrir_sin_escribir(DATA / "perfiles.db")
    except sqlite3.Error as e:
        print("  (no se puede leer perfiles.db: %s)" % e)
        return 1
    # Solo lo del titular: esto lo mira él, y lo de otro perfil no se enseña a nadie.
    fila = base.execute("SELECT valor FROM meta WHERE clave = 'titular'").fetchone()
    chat = int(fila[0]) if fila else 0
    fila = base.execute("SELECT valor FROM meta WHERE clave = ?", ("ultima_busqueda:%d" % chat,)).fetchone()
    print("Última búsqueda: %s UTC" % str(fila[0] if fila else None).replace("T", " "))

    titulo("1. Salud de las fuentes")
    salud = leer("salud_fuentes.json", {"fuentes": {}, "incidencias_pendientes": []})
    for f in FUENTES:
        info = salud.get("fuentes", {}).get(f)
        if not info:
            print("  %-12s sin incidencias registradas  <- lo normal" % f)
            continue
        if info.get("desactivada"):
            motivo = " ".join(str(info.get("motivo") or "").split()).split("<")[0]  # sin el HTML de la página que bloqueó
            print("  %-12s DESACTIVADA desde %s: %s" % (f, info.get("desactivada_en"), motivo[:110]))
        else:
            print("  %-12s activa · bloqueos seguidos: %s · corridas vacías seguidas: %s"
                  % (f, info.get("bloqueos_consecutivos", 0), info.get("corridas_vacias_consecutivas", 0)))
    pendientes = salud.get("incidencias_pendientes") or []
    print("  Avisos de salud pendientes de enviar a Telegram: %d%s"
          % (len(pendientes), "  <- deberían haberse enviado; mira el log" if pendientes else ""))

    titulo("2. Cupo de Jooble")
    uso = leer("jooble_uso.json", None)
    if uso is None:
        print("  No hay jooble_uso.json: Jooble todavía no ha hecho ninguna petición.")
    else:
        print("  Peticiones gastadas: %s de 500 (se para sola en 480)" % uso.get("peticiones"))
        print("  Última tanda: %s UTC (la siguiente, 12 h después)" % str(uso.get("ultima_descarga")).replace("T", " "))
        for dia, n in sorted((uso.get("por_dia") or {}).items())[-7:]:
            print("    %s: %d" % (dia, n))
        print("  Lo esperado: 4 por tanda y 8 al día como mucho.")

    titulo("3. Ofertas NUEVAS de Tecnoempleo y Jooble")
    ofertas = []
    for datos, relevancia, visto in base.execute(
            "SELECT o.datos, op.relevancia, op.visto_primera FROM ofertas_perfil op"
            " JOIN ofertas o ON o.url = op.url WHERE op.chat_id = ?", (chat,)):
        ofertas.append(dict(json.loads(datos), relevancia=relevancia, visto_por_primera_vez=visto))
    for f in ("tecnoempleo", "jooble"):
        nuevas = [o for o in ofertas if o.get("site") == f and str(o.get("visto_por_primera_vez") or "") >= desde]
        total = sum(1 for o in ofertas if o.get("site") == f)
        print("\n  %s: %d nuevas en %d h (%d en total en la base)" % (f, len(nuevas), horas, total))
        for o in sorted(nuevas, key=lambda o: -(o.get("relevancia") or 0)):
            r = o.get("relevancia") or 0
            print("    %4.1f %s %-50s | %-22s | %s" % (
                r, "AVISA" if r >= UMBRAL_AVISO else "     ", (o.get("title") or "")[:50],
                (o.get("company") or "")[:22], o.get("modalidad_label") or ""))
    print("\n  Nota: las ofertas que llegan por más de una fuente se quedan con la de LinkedIn,")
    print("  Indeed o InfoJobs, así que aquí solo salen las que nadie más tenía.")

    titulo("4. Avisos de Telegram y lo que respondiste con los botones")
    columnas = ("titulo", "relevancia", "fuente", "avisada", "respuesta", "motivo")
    avisos = {clave: dict(zip(columnas, resto)) for clave, *resto in base.execute(
        "SELECT clave, %s FROM avisos WHERE chat_id = ?" % ", ".join(columnas), (chat,))}
    if not avisos:
        print("  No hay avisos: no ha salido ninguno desde que hay botones.")
    else:
        recientes = {k: v for k, v in avisos.items() if str(v.get("avisada") or "") >= desde}
        print("  Avisos con botones en %d h: %d" % (horas, len(recientes)))
        for v in sorted(recientes.values(), key=lambda v: str(v.get("avisada"))):
            resp = v.get("respuesta") or "sin responder"
            if v.get("motivo"):
                resp += " (%s)" % v["motivo"]
            print("    %s  %4.1f  %-45s | %-12s -> %s" % (
                str(v.get("avisada"))[5:16].replace("T", " "), v.get("relevancia") or 0,
                (v.get("titulo") or "")[:45], v.get("fuente") or "", resp))
    marcas = base.execute("SELECT accion, titulo, motivo FROM marcas WHERE chat_id = ? AND origen = 'telegram'"
                          " ORDER BY accion", (chat,)).fetchall()
    print("  Marcas puestas desde Telegram (lo que ve la web): %d" % len(marcas))
    for accion, titulo_marca, motivo in marcas:
        print("    %-9s %-50s %s" % (accion, (titulo_marca or "")[:50], motivo or ""))

    titulo("5. Rotación de consultas (MP3)")
    filas = base.execute("SELECT clave, ultima, resultados FROM consultas").fetchall()
    consultas = [f for f in filas if not f[0].startswith("jooble|")]
    hechas = sorted(f[1] for f in consultas if f[1])
    print("  Consultas únicas: %d · hechas alguna vez: %d" % (len(consultas), len(hechas)))
    if hechas:
        print("  La más antigua: %s UTC · la última: %s UTC" % (hechas[0].replace("T", " "), hechas[-1].replace("T", " ")))
        por_corrida = {}
        for marca in hechas:
            por_corrida[marca] = por_corrida.get(marca, 0) + 1
        print("  Tandas con su marca: %d, de hasta %d consultas" % (len(por_corrida), max(por_corrida.values())))
        print("  Si las hay más antiguas que la mitad de la ventana (19 h), llega un aviso de operación.")
    jooble = sorted((f for f in filas if f[0].startswith("jooble|")), key=lambda f: f[1] or "")
    print("  Términos de Jooble: %d" % len(jooble))
    for clave, ultima, _ in jooble:
        print("    %-45s última vez: %s" % (clave[len("jooble|"):][:45], str(ultima).replace("T", " ")))

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
