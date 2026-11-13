"""
Normalización y etiquetado de ofertas: país, nivel de experiencia, tipo de contrato,
modalidad y salario legible. Lo comparten app.py (web) y ver_ofertas.py (export estático).
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

ETIQUETAS_CATEGORIA = {
    "ciberseguridad": "Ciberseguridad",
    "it_saas": "Técnico IT/SaaS",
}
ETIQUETAS_AMBITO = {
    "barcelona_local": "Barcelona (radio limitado)",
    "remoto_es_ue": "Remoto España/UE",
    "remoto_mundial": "Remoto mundial",
}
ETIQUETAS_NIVEL = {
    "entry level": "Sin experiencia / Entry level",
    "associate": "Associate (junior-mid)",
    "mid-senior level": "Mid-Senior",
    "director": "Director",
    "executive": "Ejecutivo",
    "internship": "Prácticas",
    "not applicable": "No especificado",
}
ETIQUETAS_CONTRATO = {
    "fulltime": "Jornada completa",
    "parttime": "Media jornada",
    "contract": "Contrato/Freelance",
    "internship": "Prácticas",
    "temporary": "Temporal",
    "perdiem": "Por día",
}

# Códigos de país de 2 letras que pueden aparecer al final de "location" (p.ej. Indeed
# devuelve "Madrid, MD, ES").
CODIGOS_PAIS = {
    "ES": "España", "DE": "Alemania", "FR": "Francia", "IT": "Italia", "PT": "Portugal",
    "NL": "Países Bajos", "IE": "Irlanda", "BE": "Bélgica", "PL": "Polonia", "RO": "Rumanía",
    "BG": "Bulgaria", "GR": "Grecia", "AT": "Austria", "CH": "Suiza", "SE": "Suecia",
    "DK": "Dinamarca", "FI": "Finlandia", "EE": "Estonia", "LV": "Letonia", "LT": "Lituania",
    "LU": "Luxemburgo", "HU": "Hungría", "SK": "Eslovaquia", "SI": "Eslovenia", "HR": "Croacia",
    "CY": "Chipre", "MT": "Malta", "CZ": "Chequia", "GB": "Reino Unido", "UK": "Reino Unido",
}
ESTADOS_EEUU = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
ESTADOS_EEUU_NOMBRE = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
    "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
}
# Nombres de país en inglés (tal como los devuelve LinkedIn/Google) -> español, para no
# tener "Spain" y "España" como dos países distintos en el filtro.
NOMBRES_PAIS_EN_ES = {
    "spain": "España", "germany": "Alemania", "france": "Francia", "italy": "Italia",
    "portugal": "Portugal", "netherlands": "Países Bajos", "ireland": "Irlanda",
    "belgium": "Bélgica", "poland": "Polonia", "romania": "Rumanía", "bulgaria": "Bulgaria",
    "greece": "Grecia", "austria": "Austria", "switzerland": "Suiza", "sweden": "Suecia",
    "denmark": "Dinamarca", "finland": "Finlandia", "estonia": "Estonia", "latvia": "Letonia",
    "lithuania": "Lituania", "luxembourg": "Luxemburgo", "hungary": "Hungría",
    "slovakia": "Eslovaquia", "slovenia": "Eslovenia", "croatia": "Croacia", "cyprus": "Chipre",
    "malta": "Malta", "czechia": "Chequia", "czech republic": "Chequia",
    "united kingdom": "Reino Unido", "united states": "Estados Unidos", "canada": "Canadá",
    "mexico": "México", "india": "India", "brazil": "Brasil", "argentina": "Argentina",
    "australia": "Australia", "norway": "Noruega", "ukraine": "Ucrania",
}

# LinkedIn describe muchas ofertas por su área metropolitana en vez de por el país
# ("Greater Barcelona Metropolitan Area"). Estas son las ciudades que aparecen en las
# búsquedas de este perfil; sirven para deducir el país sin adivinar.
CIUDADES_CONOCIDAS = {
    "barcelona": "España", "madrid": "España", "valencia": "España",
    "sevilla": "España", "bilbao": "España", "m[áa]laga": "España",
    "zaragoza": "España", "murcia": "España", "palma": "España",
    "lisbon": "Portugal", "lisboa": "Portugal", "porto": "Portugal",
    "paris": "Francia", "lyon": "Francia", "berlin": "Alemania",
    "munich": "Alemania", "hamburg": "Alemania", "frankfurt": "Alemania",
    "amsterdam": "Países Bajos", "rotterdam": "Países Bajos",
    "dublin": "Irlanda", "milan": "Italia", "rome": "Italia", "roma": "Italia",
    "warsaw": "Polonia", "krakow": "Polonia", "cracow": "Polonia",
    "bucharest": "Rumanía", "athens": "Grecia", "vienna": "Austria",
    "brussels": "Bélgica", "copenhagen": "Dinamarca", "stockholm": "Suecia",
    "helsinki": "Finlandia", "prague": "Chequia", "budapest": "Hungría",
    "sofia": "Bulgaria", "london": "Reino Unido", "manchester": "Reino Unido",
    "new york": "Estados Unidos", "san francisco": "Estados Unidos",
    "chicago": "Estados Unidos", "boston": "Estados Unidos",
    "los angeles": "Estados Unidos", "seattle": "Estados Unidos",
    "atlanta": "Estados Unidos", "dallas": "Estados Unidos",
    "toronto": "Canadá", "vancouver": "Canadá", "bangalore": "India",
    "mumbai": "India", "hyderabad": "India", "delhi": "India",
}

COLUMNAS_UTILES = [
    "id", "site", "job_url", "job_url_direct", "title", "company", "location",
    "date_posted", "job_type", "interval", "min_amount", "max_amount", "currency",
    "is_remote", "job_level", "company_industry", "description", "categoria", "ambito",
    "busqueda", "distancia_km", "posible_restriccion_geografica", "relevancia",
    "motivos_relevancia",
    # Competencia y antigüedad real leídas de la página de LinkedIn en el momento de
    # descubrir la oferta (ver buscador.extraer_candidatos). Solo las trae LinkedIn: en
    # el resto de fuentes quedan a None, que es lo que la interfaz espera para no pintar
    # la etiqueta.
    "candidatos", "candidatos_tope", "publicada_en",
]


def extraer_pais(location) -> str | None:
    if not location or not isinstance(location, str):
        return None
    partes = [p.strip() for p in location.split(",") if p.strip()]
    if not partes:
        return None
    ultimo = partes[-1]

    if len(ultimo) == 2 and ultimo.isupper():
        if ultimo in CODIGOS_PAIS:
            return CODIGOS_PAIS[ultimo]
        if ultimo in ESTADOS_EEUU:
            return "Estados Unidos"
        return None  # código ambiguo (país/estado con las mismas siglas): mejor no adivinar

    ultimo_norm = ultimo.lower()
    # LinkedIn usa mucho "área metropolitana": "Springfield, Massachusetts Metropolitan
    # Area" pero también "Greater Barcelona Metropolitan Area". No se puede dar por
    # hecho que sean de EE. UU.: hay que mirar qué ciudad o estado la acompaña.
    if "metropolitan area" in ultimo_norm or "área metropolitana" in ultimo_norm:
        if any(estado in ultimo_norm for estado in ESTADOS_EEUU_NOMBRE):
            return "Estados Unidos"
        for ciudad, pais in CIUDADES_CONOCIDAS.items():
            if ciudad in ultimo_norm:
                return pais
        return None  # zona sin identificar: mejor no adivinar el país
    if ultimo_norm in ESTADOS_EEUU_NOMBRE:
        return "Estados Unidos"
    if ultimo_norm in NOMBRES_PAIS_EN_ES:
        return NOMBRES_PAIS_EN_ES[ultimo_norm]
    # Una zona suelta sin comas ("Greater Barcelona Area") también lleva su ciudad
    for ciudad, pais in CIUDADES_CONOCIDAS.items():
        if ciudad in ultimo_norm:
            return pais
    return ultimo


def _salario_legible(row) -> str | None:
    minimo, maximo = row.get("min_amount"), row.get("max_amount")
    if pd.isna(minimo) and pd.isna(maximo):
        return None
    moneda = row.get("currency") or ""
    intervalo = {
        "yearly": "/año", "monthly": "/mes", "hourly": "/h",
        "weekly": "/sem", "daily": "/día",
    }.get(row.get("interval"), "")
    if pd.notna(minimo) and pd.notna(maximo) and minimo != maximo:
        return f"{minimo:,.0f}–{maximo:,.0f} {moneda}{intervalo}".strip()
    valor = minimo if pd.notna(minimo) else maximo
    return f"{valor:,.0f} {moneda}{intervalo}".strip()


def enriquecer(df: pd.DataFrame) -> pd.DataFrame:
    """Añade las columnas derivadas que usan los filtros del visor."""
    if df.empty:
        return df

    df = df.copy()
    for columna in COLUMNAS_UTILES:
        if columna not in df.columns:
            df[columna] = None
    df = df[[c for c in COLUMNAS_UTILES if c in df.columns]]

    df["categoria_label"] = df["categoria"].map(ETIQUETAS_CATEGORIA).fillna(df["categoria"])
    df["ambito_label"] = df["ambito"].map(ETIQUETAS_AMBITO).fillna(df["ambito"])
    df["pais"] = df["location"].apply(extraer_pais).fillna("No indicado")
    df["experiencia_label"] = df["job_level"].map(ETIQUETAS_NIVEL).fillna("No especificado")
    df["contrato_label"] = (
        df["job_type"].map(ETIQUETAS_CONTRATO).fillna(df["job_type"]).fillna("No indicado")
    )
    # La modalidad la decide buscador.modalidad(), el mismo criterio que usa el filtro
    # geográfico, y distingue «Híbrido» de «Presencial». Antes esta etiqueta salía solo
    # de la bandera `is_remote` del portal, que falla en los dos sentidos: había
    # presenciales en Madrid etiquetadas «Remoto» porque el portal lo decía, y remotas
    # reales etiquetadas «Presencial/Híbrido». Import diferido para no cerrar el ciclo
    # con buscador, que importa extraer_pais de este módulo.
    from buscador import modalidad

    df["modalidad_label"] = df.apply(modalidad, axis=1)
    df["salario"] = df.apply(_salario_legible, axis=1)

    # .astype(object) es necesario: en una columna numérica, .where(..., None) sin esto
    # pandas vuelve a convertir None en NaN, y json.dumps serializa NaN como el literal
    # "NaN" (no "null"), que JavaScript interpreta como NaN en vez de ausencia de valor.
    return df.astype(object).where(df.notnull(), None)


def _serializable(valor):
    """
    jobspy devuelve date_posted como datetime.date, que json.dumps no sabe serializar
    (al pasar por CSV no se notaba porque se convertía a texto por el camino).
    """
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()[:10]
    if isinstance(valor, pd.Timestamp):
        return valor.date().isoformat()
    if isinstance(valor, (np.integer,)):
        return int(valor)
    if isinstance(valor, (np.floating,)):
        return float(valor)
    if isinstance(valor, (np.bool_,)):
        return bool(valor)
    return valor


def a_registros(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicts lista para serializar a JSON."""
    if df.empty:
        return []
    registros = enriquecer(df).to_dict(orient="records")
    return [{k: _serializable(v) for k, v in fila.items()} for fila in registros]
