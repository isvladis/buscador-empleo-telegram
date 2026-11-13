"""
Búsqueda de ofertas por línea de comandos (alternativa a la interfaz web de app.py).

Usa la misma configuración (config.json), el mismo estado (favoritas / ocultas /
aplicadas) y la misma base de ofertas que la web, así que ambos se complementan:
lo que ocultes o apliques en la web tampoco reaparecerá aquí.

Uso:
    python test.py              busca y añade a la base existente
    python test.py --reemplazar descarta las ofertas actuales y empieza de cero
    python test.py --csv        además exporta los CSV por ámbito

Interfaz web:  python app.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd

import almacen
import buscador
import config as config_mod
import etiquetas

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

pd.set_option("display.max_columns", None)
pd.set_option("display.width", None)

DIR_BASE = Path(__file__).parent
COLUMNAS_RESUMEN = ["title", "company", "location", "date_posted", "job_url"]

ARCHIVOS_POR_AMBITO = {
    "barcelona_local": "ofertas_barcelona_local.csv",
    "remoto_es_ue": "ofertas_remoto_es_ue.csv",
    "remoto_mundial": "ofertas_remoto_mundial.csv",
}


def mostrar_progreso(mensaje: str, hecho: int, total: int) -> None:
    print(f"[{hecho}/{total}] {mensaje}")


def mostrar_resumen(titulo: str, df: pd.DataFrame) -> None:
    print(f"\n=== {titulo} ({len(df)} ofertas) ===")
    if df.empty:
        print("  (sin resultados)")
        return
    columnas = [c for c in COLUMNAS_RESUMEN if c in df.columns]
    print(df[columnas].to_string(index=False))


def guardar_csv(df: pd.DataFrame, nombre: str) -> None:
    ruta = DIR_BASE / nombre
    df.to_csv(
        ruta, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\",
        index=False, encoding="utf-8-sig",
    )
    print(f"Guardado: {ruta} ({len(df)} ofertas)")


def main() -> None:
    reemplazar = "--reemplazar" in sys.argv
    exportar_csv = "--csv" in sys.argv

    config = config_mod.cargar_config()
    excluidas = almacen.urls_excluidas()
    if excluidas:
        print(f"{len(excluidas)} ofertas ocultadas/aplicadas no volverán a aparecer.\n")

    # Mismo caché de descripciones que usa la web (ver almacen.recordar_descripciones y
    # app._ejecutar_busqueda): la base más lo que se descargó en corridas anteriores y los
    # filtros descartaron, para no volver a pedírselo a LinkedIn.
    # Sin cargar el caché entero, como en app.py (ver almacen.cargar_cache_vistas).
    en_cache, competencia_en_cache = almacen.cargar_cache_vistas()
    en_base = almacen.descripciones_en_base()
    conocidas = almacen.Conocidas(en_base, en_cache)

    df = buscador.buscar(
        config=config,
        urls_excluidas=excluidas,
        progreso=mostrar_progreso,
        descripciones_conocidas=conocidas,
        # Se excluye solo lo que ya está en la base; lo que ya estaba en el caché vuelve
        # a pasar para que se le refresque la fecha de visto (mismo criterio que app.py).
        al_descargar_descripciones=lambda d, c: almacen.recordar_descripciones(
            {u: t for u, t in d.items() if u not in en_base}, c
        ),
        competencia_conocida=competencia_en_cache,
    )

    if df.empty:
        print("\nNo se ha encontrado ninguna oferta.")
        return

    registros = etiquetas.a_registros(df)
    resumen = almacen.fusionar_ofertas(registros, reemplazar=reemplazar)
    print(f"\n{resumen['nuevas']} ofertas nuevas · {resumen['total']} en la base.")

    enriquecido = etiquetas.enriquecer(df)
    for ambito, nombre_archivo in ARCHIVOS_POR_AMBITO.items():
        subconjunto = enriquecido[enriquecido["ambito"] == ambito]
        mostrar_resumen(etiquetas.ETIQUETAS_AMBITO.get(ambito, ambito), subconjunto)
        if exportar_csv:
            guardar_csv(subconjunto, nombre_archivo)

    if exportar_csv:
        guardar_csv(enriquecido, "ofertas_todas.csv")

    print("\nPara verlas y gestionarlas en el navegador:  python app.py")


if __name__ == "__main__":
    main()
