# -*- coding: utf-8 -*-
"""
Las seis personas de ejemplo de MP7 (prompts/multiperfil/MP7-catalogo-y-generador.md): sus respuestas a
la encuesta, como las guardará MP8. Las usan `test_generador.py` y la tabla del §6 del plan.

Ninguna es una persona real: son los seis casos que pide el prompt, con coordenadas del centro de cada
ciudad. La primera es «alguien como el adicional» (soporte IT, sin experiencia, Barcelona), que hoy entra
por la plantilla `titular-2026-10` y que con la encuesta entraría así.
"""

PERSONAS = {
    "soporte_bcn": {
        "puestos": ["it.soporte", "it.sistemas"],
        "experiencia": "ninguna", "experiencia_estricta": True, "practicas": True,
        "ubicacion": {"nombre": "Barcelona", "lat": 41.3874, "lon": 2.1686, "radio_km": 10},
        "modalidad": ["presencial", "hibrido", "remoto"], "remoto_alcance": "es",
        "idiomas": ["es", "ca", "en"], "ingles": "intermedio",
        "jornada": ["completa"], "claves_mas": [], "claves_menos": ["comercial"],
    },
    "administrativa_valencia": {
        "puestos": ["admin.administrativo", "admin.auxiliar", "finanzas.contable"],
        "experiencia": "3_5", "experiencia_estricta": True,
        "ubicacion": {"nombre": "Valencia", "lat": 39.4699, "lon": -0.3763, "radio_km": 25},
        "modalidad": ["presencial", "hibrido"],
        "idiomas": ["es", "ca", "en"], "ingles": "intermedio",
        "jornada": ["completa"], "salario_min": 18000,
        "claves_mas": ["contabilidad", "a3"], "claves_menos": ["comercial", "ett"],
        "empresas_excluidas": ["Asesoría Ejemplo"],
    },
    "camarero_malaga": {
        "puestos": ["hosteleria.camarero", "hosteleria.cafeteria"],
        "experiencia": "ninguna", "practicas": False,
        "ubicacion": {"nombre": "Málaga", "lat": 36.7213, "lon": -4.4214, "radio_km": 60},
        "modalidad": ["presencial"],
        "idiomas": ["es", "en"], "ingles": "basico",
        "jornada": ["completa", "parcial"], "turnos_ok": True,
    },
    "enfermera_madrid": {
        "puestos": ["sanidad.enfermeria"],
        "experiencia": "6_10", "experiencia_estricta": False,
        "ubicacion": {"nombre": "Madrid", "lat": 40.4168, "lon": -3.7038, "radio_km": 25},
        "modalidad": ["presencial"],
        "idiomas": ["es", "en"], "ingles": "alto",
        "jornada": ["completa", "indefinido"], "salario_min": 30000,
        "certificaciones": ["UCI"],
    },
    "mozo_zaragoza": {
        "puestos": ["logistica.mozo", "logistica.carretillero"],
        "experiencia": "menos_1", "practicas": False,
        "ubicacion": {"nombre": "Zaragoza", "lat": 41.6488, "lon": -0.8891, "radio_km": 25},
        "modalidad": ["presencial"],
        "idiomas": ["es"],
        "claves_mas": ["carretilla"],
    },
    "desarrollador_remoto_ue": {
        "puestos": ["it.desarrollo", "it.cloud"],
        "experiencia": "mas_10", "experiencia_max_exigida": None, "experiencia_estricta": False,
        "ubicacion": {"nombre": "Sevilla", "lat": 37.3891, "lon": -5.9845, "radio_km": 0},
        "modalidad": ["remoto"], "remoto_alcance": "ue",
        "idiomas": ["es", "en"], "ingles": "alto",
        "salario_min": 40000, "claves_mas": ["python"],
    },
}
