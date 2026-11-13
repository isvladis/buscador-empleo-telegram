# -*- coding: utf-8 -*-
"""
Escribe `pruebas/catalogo_muestras.json`: por cada puesto del catálogo (MP7), títulos que DEBEN casar con
su núcleo («si») y títulos que NO («no»).

    python pruebas/generar_muestras_mp7.py <copia de perfiles.db>

La copia de la base solo sirve para marcar qué títulos son reales: los que están tal cual en la tabla
`ofertas` salen sin marca y los demás con `"sintetico": true`. No se guarda nada de la base salvo los
títulos, que son públicos (son los anuncios).

**Cómo se eligieron.** Los «si», reales cuando la base los tiene (casi solo en IT, que es lo que busca el
titular) y si no, inventados con la forma de los portales: «Camarero/a», «CAMARERA», «Cambrer/a», «(H/M)».
Los «no» son lo que más importa (lo dice el caso de las prácticas del 04-11: la muestra del titular no
tenía ni un programa de prácticas y el medidor no podía ver el fallo que los botones vieron tres veces).
Por eso son sobre todo **falsos amigos**: el puesto vecino que comparte palabra («Camarero/a de pisos»
contra camarero), el mismo término en otro oficio («Técnico/a de Monitorización 24x7» contra monitor de
ocio) y, de la base, los que casaban antes de corregir las regex.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "catalogo_muestras.json"

M = {
    # ------------------------------------------------------------------ IT
    "it.soporte": {
        "si": ["Service Desk Technician (Remote)", "IT Support Specialist (m/f/d)", "Técnico de Soporte N1 - Helpdesk",
               "Técnico/a de Soporte IT", "Técnico/a de Soporte N1 (Teletrabajo)", "Onsite Technician",
               "IT Desktop Support Technician 2nd shift", "IT Support Specialist L1", "Técnico/a de Soporte Digital N1",
               "plaça de Tècnic Helpdesk/Endpoint al Campus UPC Barcelona CIDO", "TÈCNIC/A INFORMÀTIC/A DE SUPORT AL LLOC DE TREBALL ? ÀMBIT SANITARI"],
        "no": ["Gerocultor/a - Jornada Completa. Mañanas y tardes (Barcelona, Barcelona)", "Vigilante de Seguridad (Barcelona)",
               "Customer Success Advisor", "Técnico/a de Calidad", "Comercial junior", "Desarrollador/a Java | Sector Salud | Sevilla Híbrido",
               "Delineante", "Técnico de nóminas", "Camarero/a de sala"],
    },
    "it.sistemas": {
        "si": ["Administrador de sistemas", "Técnico/a Sistemas", "Systems Administrator (VMware & Windows Server)",
               "Técnico de sistemas y redes Junior", "Network Engineer (F5)", "Técnico Redes", "Corporate IT Systems Administrator",
               "Administrador/a de Sistemas Junior 24x7 – Barcelona", "TÈCNIC/A DE SISTEMES, XARXES I CIBERSEGURETAT"],
        "no": ["Técnico/a de Soporte N1 (Teletrabajo)", "Desarrollador/a Python, Linux & Bash", "Data Engineer", "SOC Specialist II",
               "Técnico/a de Calidad", "Delineante", "Administrativo/a", "Técnico/a de mantenimiento", "Agente de viajes"],
    },
    "it.ciber": {
        "si": ["Security Operations Analyst", "Blue Team Analyst N1", "Cybersecurity Analyst (Hybrid in Barcelona)",
               "SIEM Analyst (Barcelona presencial)", "Operador/a Ciberseguridad", "Junior Penetration Tester",
               "Ciberseguridad. Analista respuesta a incidentes", "Ingeniero de Ciberseguridad", "Consultor/a Junior GRC + IA | Ciberseguridad",
               "Analista de Ciberseguridad N2 – SOC ( Hibrido) Madrid 02CC"],
        "no": ["Vigilante de Seguridad (Barcelona)", "Técnico/a Prevención de Riesgos Laborales", "Security Supervisor - Hotel Arts Barcelona",
               "Técnico/a de Soporte IT", "Administrador de sistemas", "Técnico/a de Seguridad Alimentaria",
               "Auxiliar de seguridad para centro comercial", "Técnico de sistemas de seguridad electrónica (CCTV)",
               "Técnico/a de Calidad y Seguridad (HSEQ)"],
    },
    "it.desarrollo": {
        "si": ["Fullstack Java developer", "Backend Developer [Python]", "Desarrollador/a SharePoint Online / Microsoft 365",
               "Analista programador/a", "Ingeniero/a Software", "Software Engineer - Backend Platform", "Odoo Developer (Python)",
               "Desarrollador/a Java | Sector Salud | Sevilla Híbrido", "Programador/a PEGA - 100% Remoto (Madrid, España)"],
        "no": ["Técnico/a de Soporte IT", "Data Engineer", "QA Engineer", "Junior Graphic Designer", "Técnico de desarrollo de negocio",
               "Business Development Representative", "Técnico/a de Formación Subvencionada", "Administrador de sistemas",
               "Desarrollo de producto textil (técnica)"],
    },
    "it.datos": {
        "si": ["Data Engineer", "Associate Data Analyst, Barcelona", "ANALISTA BI", "Junior BI Analyst", "Ingeniero de datos",
               "Tableau Specialist", "Analista de datos y automatización - Barcelona", "AI Engineer Junior",
               "Analista de dades i recerca social aplicada", "Marketing Data Analyst"],
        "no": ["Grabador/a de datos", "Técnico/a de protección de datos", "Backend Developer [Python]", "Administrador de sistemas",
               "Técnico/a de Soporte IT", "Controller", "Técnico/a de Calidad", "Administrativo/a", "Analista logístico"],
    },
    "it.qa": {
        "si": ["QA / Test Engineer", "QA Engineer (Manual)", "Junior QA Engineer", "Junior Test Engineer", "Manual QA Tester ES",
               "SWQA TESTER JUNIOR", "Quality Assurance Analyst ES", "Test Analyst - 100% Remote, Portugal", "QA Automation hybrid Barcelona. International Project."],
        "no": ["Pentester Junior", "Junior Penetration Tester", "Web-Penetration-Tester (m/w/d) - 100 % Home-Office möglich",
               "Técnico/a de Calidad", "Inspector/a de calidad", "Backend Developer [Python]", "Técnico/a de Soporte IT",
               "Técnico/a de Control de Calidad Visual", "Técnico/a de Seguridad Alimentaria"],
    },
    "it.cloud": {
        "si": ["DevOps Engineer AWS", "Site Reliability Engineer", "Platform Engineer", "Junior Cloud Engineer (gn)",
               "DevOps / SRE Engineer", "AWS Cloud Engineer (Teletrabajo 100%)", "Analista DevOps (Remoto)",
               "Técnico/a de Sistemas OpenShift | Kubernetes, Cloud y DevOps | Remoto", "Cloud Operations Graduate - Barcelona"],
        "no": ["Backend Developer [Python]", "Administrador de sistemas", "Técnico/a de Soporte IT", "Data Engineer",
               "Cloud Marketplace and Process Analyst", "Ingeniero/a de Telecomunicación Junior (Barcelona)", "Técnico/a de Calidad",
               "Comercial junior", "SOC Specialist II"],
    },
    "it.telecom": {
        "si": ["NOC Operator", "Técnico/a de Monitorización (24x7) Remoto", "Técnico de Redes (NOC)", "NOC L1 Engineer",
               "Ingeniero/a de Telecomunicación Junior (Barcelona)", "TECNICO/A DE MONITORIZACION (PRESENCIAL)",
               "Técnico de Observabilidad y Monitorización", "NOC Technician (CCNA Certified)", "Técnico/a de fibra óptica"],
        "no": ["AML Transaction Monitoring Analyst", "Associate Clinical Programmer - Clinical Study Data, Risk based Quality Management & Monitoring",
               "Instaladores/as de Telecomunicaciones", "Monitor de Natación y Aquagym en Pechina", "Monitor/a de comedor escolar",
               "Técnico/a de Soporte IT", "Comercial de telecomunicaciones", "Técnico de Comunicación", "Administrador de sistemas"],
    },
    # ------------------------------------------------------------------ ADMIN
    "admin.administrativo": {
        "si": ["Técnico/a Administrativo/a", "ADMINISTRATIU/A COMPTABLE", "Administratiu/va de personal. Barcelona",
               "Personal técnico de administrativo-contable", "Administrativo/a", "ADMINISTRATIVA", "Administrativo/a de compras (H/M)",
               "Office Administrator", "Administrative Assistant"],
        "no": ["Administrador de sistemas", "Administrador/a de fincas", "Técnico/a de administración de sistemas", "Recepcionista",
               "Comercial junior", "Contable", "Back office bancario", "Secretaria de dirección", "Gestor/a de pedidos"],
    },
    "admin.auxiliar": {
        "si": ["Auxiliar Administrativo Help Desk con Certificado de Discapacidad", "Auxiliar administrativo/a", "AUXILIAR ADMINISTRATIVA",
               "Aux. administrativo para clínica", "Auxiliar de oficina", "Administrative Clerk", "Office Assistant",
               "Auxiliar Administratiu/va", "Ayudante administrativo/a"],
        "no": ["Administrativo/a", "Auxiliar de enfermería", "Auxiliar de geriatría", "Auxiliar de cocina", "Auxiliar de almacén",
               "Auxiliar de farmacia", "Auxiliar contable", "Auxiliar de vuelo", "Auxiliar de servicios"],
    },
    "admin.recepcion": {
        "si": ["Ayudante Recepción", "Recepcionista", "Recepcionista clínica dental", "Recepcionista para gimnasio",
               "Receptionist (English)", "RECEPCIONISTA (H/M)", "Front desk (oficinas centrales)", "Recepcionista centro médico",
               "Personal de acogida y recepción"],
        "no": ["Técnico/a de Calidad", "Administrativo/a", "Teleoperador/a", "Conserje", "Azafata de congresos",
               "Mozo/a de almacén", "Auxiliar de oficina", "Secretaria de dirección", "Agente de viajes"],
    },
    "admin.direccion": {
        "si": ["Executive Assistant", "Asistente de dirección", "Secretaria de dirección (H/M)", "Secretario/a", "Personal Assistant to the CEO",
               "Asistente ejecutiva bilingüe", "Secretària de direcció", "Office Manager", "PA to Managing Director"],
        "no": ["Administrativo/a", "Recepcionista", "Director/a de tienda", "Auxiliar administrativo/a", "Técnico/a de RRHH",
               "Dirección de obra", "Asistente de ventas", "Contable", "Coordinador/a de eventos"],
    },
    "admin.backoffice": {
        "si": ["Back Office Representative", "Técnico/a de Gestión Documental", "Middle Office Analyst – International Operations (f/m/x)",
               "Grabador/a de datos", "Data Entry Clerk", "Gestor/a administrativo/a", "Back-office bancario", "Operador/a de datos",
               "Gestión documental y archivo"],
        "no": ["Front Office Sales Support", "Backend Developer [Python]", "Back-end developer (Node)", "Data Engineer",
               "Recepcionista", "Administrativo/a", "Teleoperador/a", "Gestor/a de pedidos", "Contable"],
    },
    "admin.comercial": {
        "si": ["Front Office Sales Support", "Inside Sales Support", "Técnico/a Soporte Comercial B2B", "Administrativo/a comercial",
               "Administrativo de ventas", "Gestor/a de pedidos", "Gestión de pedidos y atención a clientes", "Sales Administrator",
               "Administrativo/a de exportación"],
        "no": ["Comercial junior", "Técnico comercial", "Administrativo/a", "Account Executive", "Teleoperador/a de ventas",
               "Back Office Representative", "Asesor/a comercial de seguros", "Recepcionista", "Contable"],
    },
    # ------------------------------------------------------------------ ATENCIÓN
    "atencion.teleoperador": {
        "si": ["Teleoperadora", "Teleoperador/a sin experiencia",
               "Agente telefónico/a", "Atención telefónica para aseguradora", "Agente Call Center", "Contact Center Agent",
               "Teleoperador/a de emisión", "Teleoperador/a para aseguradora", "Agente de contact center (inbound)"],
        "no": ["Customer Service Representative", "Agente de atención al cliente", "Recepcionista", "Técnico/a de Soporte IT",
               "Comercial junior", "Televenta", "Técnico/a de telecomunicaciones", "Azafata de información", "Auxiliar administrativo/a"],
    },
    "atencion.agente": {
        "si": ["Customer Service Representative", "Customer Support Specialist", "AGENT D'ATENCIÓ AL CLIENT", "Customer Care Specialist - LATAM Market",
               "Asesor de Servicio al Cliente - Mercado Español AD03", "Agente de atención al cliente", "Atención al cliente (H/M)",
               "Customer Advisor German", "Gestor/a de clientes"],
        "no": ["Customer Success Advisor", "Teleoperador/a", "Account Executive", "Recepcionista", "Comercial junior", "Agente de viajes",
               "Agente de seguros", "Atención al público en museo", "Gestor/a de reclamaciones"],
    },
    "atencion.idiomas": {
        "si": ["Customer Service Representative (Spanish and English)", "Technical Customer Support Specialist | French | Remote",
               "Customer Service Representative", "Basque-speaking Customer Advisor (on-site)", "Customer Advisor Dutch",
               "Atención al cliente con inglés", "German speaking customer service agent", "Agente con francés para atención al cliente",
               "Customer care representative (Italian)"],
        "no": ["Multilingual Training Administrator", "Engineering roles abroad – Eaton is hiring multilingual talent",
               "Agente de atención al cliente", "Profesor/a de inglés", "Traductor/a inglés-español", "Teleoperador/a",
               "Atención al cliente", "Account Executive", "Recepcionista"],
    },
    "atencion.postventa": {
        "si": ["Gestor/a de incidencias Alquileres", "Gestor/a de reclamaciones", "Atención postventa e-commerce", "Tramitador/a de siniestros", "Claims Handler",
               "Gestión de devoluciones", "Departamento de reclamaciones", "Atención postventa (H/M)", "Gestor/a de incidencias de clientes"],
        "no": ["Teleoperador/a", "Customer Service Representative", "Comercial junior", "Técnico/a de Soporte IT", "Administrativo/a",
               "Agente de seguros", "Contable", "Recepcionista", "Mozo/a de almacén"],
    },
    "atencion.publico": {
        "si": ["Atención al público", "Azafata de congresos", "Informador/a", "Personal de información en museo", "Taquillero/a",
               "Auxiliar de información", "Atenció ciutadana", "Hostess para eventos", "Acomodador/a de teatro"],
        "no": ["Recepcionista", "Teleoperador/a", "Agente de atención al cliente", "Dependiente/a", "Customer Service Representative",
               "Camarero/a", "Vigilante de Seguridad (Barcelona)", "Agente de viajes", "Administrativo/a"],
    },
    # ------------------------------------------------------------------ COMERCIAL
    "comercial.campo": {
        "si": ["Comercial junior", "Técnico comercial", "TÉCNICO/A COMERCIAL - EFICIENCIA ENERGÉTICA", "Comercial técnico",
               "Técnico/a Comercial Acabados - Cerámicas", "Delegado/a comercial zona norte", "Sales Representative",
               "Agente comercial", "Comercial interno/a B2B – Soluciones tecnológicas"],
        "no": ["Vigilante de Seguridad Centros Comerciales (Barcelona)", "Comercial de Tienda", "Account Executive",
               "Televenta", "Dependiente/a", "Atención al cliente", "Administrativo/a", "Mozo/a de almacén", "Business Development Representative"],
    },
    "comercial.televenta": {
        "si": ["Sales Support - Televenta", "Televenta", "Teleoperador/a de ventas", "Televendedor/a", "Agente de televenta",
               "Telemarketing (H/M)", "Venta telefónica", "Comercial telefónico/a", "Captación telefónica de socios"],
        "no": ["Teleoperador/a", "Comercial junior", "Atención telefónica", "Customer Service Representative", "Dependiente/a",
               "Marketing digital", "Account Executive", "Recepcionista", "Agente de seguros"],
    },
    "comercial.inside": {
        "si": ["Sales Development Representative", "Business Development Representative", "Sales Development Representative - English-speaking markets",
               "Inside Sales Support", "SDR (Spanish market)", "BDR - SaaS", "Lead Generation Specialist", "Desarrollo de negocio junior",
               "Inside Sales Representative"],
        "no": ["Account Executive", "Comercial junior", "Televenta", "Backend Developer [Python]", "Customer Service Representative",
               "Marketing digital", "Recruiter", "Técnico comercial", "Customer Success Advisor"],
    },
    "comercial.cuentas": {
        "si": ["Account Executive", "Account Executive - Enterprise Sales", "Customer Success Advisor", "Account Management Intern",
               "KAM Servicios Gestionados y Ciberseguridad", "Key Account Manager", "Gestor/a de cuentas", "Ejecutivo/a de cuentas",
               "Account Manager (SaaS)"],
        "no": ["Accountant", "Accounts Payable Specialist", "Contable", "Comercial junior", "Sales Development Representative",
               "Customer Service Representative", "Accounting Business Analyst – Treasury Systems (Remoto)", "Televenta", "Técnico comercial"],
    },
    "comercial.inmobiliario": {
        "si": ["Agente inmobiliario/a", "Asesor/a inmobiliario", "Comercial inmobiliario", "Captador/a inmobiliario", "Real Estate Agent",
               "Agente de la propiedad", "Comercial de obra nueva", "Assessor/a immobiliari", "Asesor/a de vivienda"],
        "no": ["Administrador/a de fincas", "Comercial junior", "Arquitecto/a técnico", "Agente de seguros", "Técnico/a de mantenimiento",
               "Recepcionista", "Retail Facility Management", "Albañil", "Contable"],
    },
    "comercial.asesor": {
        "si": ["Asesor/a Comercial - Atención telefónica", "Asesor/a comercial", "Agente de seguros", "Asesor energético",
               "Comercial de energía", "Agente exclusivo de seguros", "Comercial de telecomunicaciones", "Gestor/a comercial banca",
               "Insurance Sales Advisor"],
        "no": ["Asesor/a fiscal", "Asesor de Ventas [ comfort zone ] – Madrid", "Teleoperador/a", "Account Executive", "Dependiente/a",
               "Tramitador/a de siniestros", "Contable", "Agente de viajes", "Técnico/a de climatización"],
    },
    # ------------------------------------------------------------------ MARKETING
    "marketing.digital": {
        "si": ["Marketing Data Analyst", "Tècnic/a SEO i SEM (Àrea de Màrqueting)", "Técnico/a de marketing digital", "Marketing junior",
               "Digital Marketing Specialist", "Especialista en marketing", "Trade marketing", "Asistente de marketing", "Growth Marketer"],
        "no": ["Telemarketing (H/M)", "Comercial junior", "Community manager", "Diseñador/a gráfico/a", "Redactor/a", "SEO/GEO Specialist Junior - Presencial en Barcelona",
               "Account Executive", "Técnico de Comunicación", "Junior E-commerce Specialist"],
    },
    "marketing.social": {
        "si": ["SOCIAL MEDIA JUNIOR", "Content & Social Media Specialist", "Junior Content Creator", "Community Manager",
               "Social Media & Online Reputation Analyst | MeMe Barcelona", "Gestor/a de redes sociales", "CREADOR/A DE CONTENIDO JUNIOR",
               "Especialista en TikTok", "Social Ads Specialist"],
        "no": ["Trabajador/a social", "Educador/a social", "Social Insights Analyst", "Marketing digital", "Redactor/a",
               "Content Quality Analyst", "Técnico de Comunicación", "Diseñador/a gráfico/a", "Telemarketing (H/M)"],
    },
    "marketing.seo": {
        "si": ["Paid Media Specialist - Barcelona", "Programmatic Specialist", "PRÀCTIQUES A PAID MEDIA I SEO", "Junior Media Buyer",
               "Paid Media Junior", "SEO/GEO Specialist Junior - Presencial en Barcelona", "Technical SEO & AI Growth Builder",
               "Especialista SEM / Google Ads", "Traffic manager"],
        "no": ["Security Research Engineering Technical Leader", "Junior Research Engineer for Generative AI in Biomedicine (RE1)",
               "Sea freight coordinator", "Marketing digital", "Community manager", "Redactor/a", "Buyer", "Técnico de Comunicación",
               "Backend Developer [Python]"],
    },
    "marketing.contenidos": {
        "si": ["EDITOR DE CONTENIDOS - Canarias", "Junior Content Creator", "Redactor/a", "Copywriter", "Redactora SEO",
               "Content Writer (English)", "Guionista", "Corrector/a de textos", "Technical Writer"],
        "no": ["Content Quality Analyst", "Moderador/a de contenidos", "Community manager", "Técnico de Comunicación",
               "Diseñador/a gráfico/a", "Marketing digital", "Traductor/a", "Periodista deportivo/a", "Profesor/a de lengua"],
    },
    "marketing.comunicacion": {
        "si": ["Técnico de Comunicación", "Especialista en Comunicación (empresa multinacional)", "Periodista", "Gabinete de prensa", "Communications Officer", "Relaciones públicas", "Tècnic/a de comunicació",
               "Internal Communications Specialist"],
        "no": ["Ingeniero/a de Telecomunicación Junior (Barcelona)", "Ingeniero/a Comunicaciones, Electrónica y Software",
               "Network Engineer N2 / Técnico de Comunicaciones", "Instaladores/as de Telecomunicaciones", "Community manager",
               "Marketing digital", "Redactor/a", "Comercial de telecomunicaciones", "Técnico/a de telecomunicaciones"],
    },
    "marketing.diseno": {
        "si": ["Junior UI/UX Designer", "UX/UI Designer Junior", "Product Designer", "Junior Graphic Designer", "Diseñador Gráfico Junior",
               "Web Designer | Based in Europe", "Motion Designer, Stripe Identity", "Diseñador/a web", "Ilustrador/a"],
        "no": ["Diseñador/a Técnico/a - Soporte a Dirección", "Diseñador/a Civil -Fotovoltaico (Remoto) - PVCase", "Delineante",
               "Design Engineer - EU", "Diseñador/a industrial de moldes", "Backend Developer [Python]", "Community manager",
               "Instructional designer", "Visual merchandiser"],
    },
    "marketing.ecommerce": {
        "si": ["ECOMMERCE PERFORMANCE SPECIALIST", "Junior E-commerce Specialist", "Técnico/a de ecommerce", "Responsable tienda online",
               "Marketplace Specialist", "Amazon Account Manager", "Especialista Shopify", "Comercio electrónico (H/M)",
               "E-commerce Assistant"],
        "no": ["Cloud Marketplace and Process Analyst", "Marketing digital", "Mozo/a de almacén", "Dependiente/a", "Comercial junior",
               "Backend Developer [Python]", "Community manager", "Agente de atención al cliente", "Repartidor/a"],
    },
    "marketing.traduccion": {
        "si": ["Traductor/a", "Traductora jurada inglés-español", "Intérprete de lengua de signos", "Translator (English > Spanish)",
               "Interpreter", "Localization Specialist", "Revisor/a lingüístico/a", "Subtitulador/a", "Linguist (Spanish)"],
        "no": ["Interpretación de planos (técnico de taller)", "Técnico/a de localización de averías", "Profesor/a de inglés",
               "Customer Service Representative (Spanish and English)", "Redactor/a", "Comercial con inglés", "Agente de viajes",
               "Técnico de Comunicación", "Auxiliar de conversación"],
    },
    # ------------------------------------------------------------------ FINANZAS
    "finanzas.contable": {
        "si": ["Técnico/a contable", "Contable - Sant Cugat del Vallès", "Comptable", "ADMINISTRATIU/A COMPTABLE", "Accountant | $55/hr Remote",
               "TÉCNICO/A CONTABILIDAD (HMX)", "Auxiliar contable", "Bookkeeper", "Técnico/a Junior de Finanzas y Contabilidad"],
        "no": ["Account Executive", "Accounts Payable Specialist", "Asesor/a fiscal", "Controller", "Auditor/a junior", "Administrativo/a",
               "Account Management Intern", "Técnico de nóminas", "Accounting Business Analyst – Treasury Systems (Remoto)"],
    },
    "finanzas.fiscal": {
        "si": ["Tax Analyst", "Asesor/a fiscal", "Técnico/a fiscal", "Fiscalista", "Gestoría - departamento fiscal", "Asesor/a contable",
               "Tax Associate", "Assessor/a fiscal", "Técnico/a tributario/a"],
        "no": ["Contable", "Controller", "Auditor/a junior", "Asesor/a comercial", "Técnico de nóminas", "Accountant",
               "Abogado/a mercantil", "Administrativo/a", "Asesor/a de ventas"],
    },
    "finanzas.apar": {
        "si": ["Accounts Payable Specialist", "Analyst, Accounts Receivable", "General Accounts Payable Officer", "Collections Specialist with Latvian",
               "Facturación (H/M)", "Técnico/a de cuentas a pagar", "Credit Controller", "Gestión de cobros", "Purchase to pay master data Analyst"],
        "no": ["Business Analyst - Utilities (Billing & Customer Platforms) (100% remote within Spain)", "Account Executive", "Contable",
               "Controller", "Technical Customer Success & QA Coordinator", "Teleoperador/a", "Auditor/a junior", "Comprador/a", "Administrativo/a"],
    },
    "finanzas.control": {
        "si": ["Controller", "Controller Junior and Data Analyst", "Junior Controller (m/w/d)", "Controlling - Young Talent",
               "Técnico/a de Tesorería (Part-time)", "Cost Controller - Hyatt Regency Barcelona Tower", "Analista financiero",
               "Financial Analyst", "Control de gestión"],
        "no": ["Soporte Técnico Financiero SaaS para Asesorías", "Controls Engineer", "Contable", "Auditor/a junior", "Asesor/a fiscal",
               "Técnico/a de Calidad", "Data Engineer", "Finance Systems Specialist to Sweco IT Helsinki", "Accounts Payable Specialist"],
    },
    "finanzas.auditoria": {
        "si": ["Auditor/a junior", "Junior Auditoría recién graduado/a", "Auditor/a de cuentas", "Audit Associate", "Auditoría financiera junior", "Revisor/a de cuentas", "Auditor/a interno/a",
               "Assurance Associate"],
        "no": ["Contable", "Controller", "Asesor/a fiscal", "Técnico/a de Calidad", "Técnico/a Prevención de Riesgos Laborales",
               "Consultor/a de ciberseguridad", "Analista financiero", "Inspector/a de calidad", "Accounts Payable Specialist"],
    },
    # ------------------------------------------------------------------ RRHH
    "rrhh.seleccion": {
        "si": ["Talent Acquisition IT / Recruiting Tech (Perfil Técnico)", "Técnico/a de Selección y Formación", "Recruiter",
               "Técnico/a de selección", "Reclutador/a", "Consultor/a de selección", "Headhunter IT", "Talent Sourcer", "Tècnic/a de selecció"],
        "no": ["Programa Talento Joven - Ciberseguridad", "Talent Community: Field Clinical Specialist, Electrophysiology (Spain)",
               "Técnico/a RRHH", "Técnico de nóminas", "Comercial de ETT", "Captador/a de socios ONG", "Orientador/a Laboral",
               "Técnico/a de Formación Subvencionada", "Selección de materiales (técnico de calidad)"],
    },
    "rrhh.personal": {
        "si": ["Payroll Analyst", "Técnico de nóminas", "Técnico/a de Administración de Personal (RRHH) - Sector Cultural - Barcelona",
               "Técnico/a Laboral con catalán", "Técnico/a de administración laboral y nóminas", "Graduado/a social", "Técnico RRHH - HR Payroll",
               "Asesor/a laboral", "Compensation & Benefits Analyst"],
        "no": ["Técnico/a RRHH", "Recruiter", "Contable", "Asesor/a fiscal", "Técnico/a de selección", "Administrativo/a",
               "Abogado/a laboral", "Controller", "Orientador/a Laboral"],
    },
    "rrhh.generalista": {
        "si": ["HR Generalist", "Técnico/a RRHH", "TÉCNICO/A DE RRHH (HMX)", "Regional HR Business Partner - Americas",
               "HR Operations Specialist, Spain", "HR Analyst (12-Month Fixed-Term Contract)", "Técnico/a de recursos humanos",
               "People Partner", "Tècnic/a de recursos humans"],
        "no": ["Business Analyst (Excel) | $60/hr Remote", "Accountant | $55/hr Remote", "Cyber Security Specialist | 45/hr Remote",
               "AI Personalization Analyst (Japanese) | $15/hr Remote", "Recruiter", "Técnico de nóminas", "Orientador/a Laboral",
               "Técnico/a de Formación Subvencionada", "Administrativo/a"],
    },
    "rrhh.formacion": {
        "si": ["Técnico/a de Formación Subvencionada", "Junior Instructional Designer", "Learning & Development Specialist", "Coordinador/a de formación", "Training Coordinator", "Tècnic/a de formació",
               "Gestor/a de formación", "L&D Partner"],
        "no": ["Formador/a", "Profesor/a de inglés", "Trainee de finanzas", "Técnico/a RRHH", "Recruiter", "Orientador/a Laboral",
               "Monitor/a de comedor", "Personal trainer", "Educador/a social"],
    },
    "rrhh.orientacion": {
        "si": ["Orientador/a Laboral", "Técnico/a de empleo", "Técnico/a de inserción laboral", "Prospector/a de empresas", "Job coach",
               "Agente de empleo y desarrollo local", "Tècnic/a d'ocupació", "Orientador/a sociolaboral", "Preparador/a laboral"],
        "no": ["Recruiter", "Técnico/a RRHH", "Educador/a social", "Profesor/a", "Preparador/a de pedidos", "Técnico de nóminas",
               "Formador/a", "Trabajador/a social", "Monitor/a de tiempo libre"],
    },
    # ------------------------------------------------------------------ SANIDAD
    "sanidad.enfermeria": {
        "si": ["Tècnic/a Sanitari/a (Grau en Infermeria o titulació equivalent. Colegiació.).", "Enfermero/a", "Enfermera de quirófano",
               "ENFERMERO/A UCI", "Graduado/a en Enfermería", "Infermer/a d'atenció primària", "Nurse (Spain)", "Matrona", "Enfermería - residencia"],
        "no": ["Aux. enfermería TCAE tardes- Centro Médico Quironsalud Aribau",
               "CONVOCATÒRIA INTERNA/EXTERNA 4 LLOCS DE TREBALL DE TÈCNIC/A EN CURES AUXILIARS D'INFERMERIA PER A L'HOSPITAL D'ATENCIÓ INTERMÈDIA DEL PARC SANITARI PERE VIRGILI",
               "Auxiliar de enfermería", "Gerocultor/a", "Técnico/a en cuidados auxiliares de enfermería", "Fisioterapeuta",
               "Due Diligence Analyst", "Médico/a de familia", "Farmacéutico/a"],
    },
    "sanidad.tcae": {
        "si": ["Aux. enfermería TCAE tardes- Centro Médico Quironsalud Aribau",
               "CONVOCATÒRIA INTERNA/EXTERNA 4 LLOCS DE TREBALL DE TÈCNIC/A EN CURES AUXILIARS D'INFERMERIA PER A L'HOSPITAL D'ATENCIÓ INTERMÈDIA DEL PARC SANITARI PERE VIRGILI",
               "TCAE", "Auxiliar de enfermería", "Técnico/a en cuidados auxiliares de enfermería", "TCAI", "Auxiliar d'infermeria",
               "Auxiliar de clínica", "Nursing Assistant"],
        "no": ["Enfermero/a", "Gerocultor/a", "Auxiliar de geriatría", "Auxiliar administrativo/a", "Auxiliar de farmacia",
               "Celador/a", "Higienista dental", "Fisioterapeuta", "Auxiliar de ayuda a domicilio"],
    },
    "sanidad.gerocultor": {
        "si": ["Gerocultor/a - Jornada Completa. Mañanas y tardes (Barcelona, Barcelona)", "Auxiliar de Geriatria - Esplugues",
               "Gerocultor/A Centre De Serveis De Torrelavit", "Personal Gerocultor",
               "A) 2 Gerocultors/Es Jornada Completa (Torn Matí) I B) 1 Gerocultor/A Jornada Completa (Torn Tarda) (26_123_Ipv_Sgg_Ger)",
               "Gerocultora", "Auxiliar de residencia", "Técnico/a en atención sociosanitaria", "Cuidador/a de residencia"],
        "no": ["Técnicos/as TASOC/TAFAD y Monitores/as para Centros de Mayores", "Auxiliar de enfermería", "Enfermero/a",
               "Auxiliar de ayuda a domicilio", "Fisioterapeuta", "Animador/a sociocultural", "Limpiador/a", "Cocinero/a", "Celador/a"],
    },
    "sanidad.domicilio": {
        "si": ["Coordinador/a de Servicio de Ayuda a Domicilio SAD Sant Just Desvern", "Auxiliar de ayuda a domicilio", "Cuidadora",
               "Cuidador/a de personas mayores", "Treballador/a familiar", "Asistente personal (diversidad funcional)",
               "Interna para cuidado de persona mayor", "Auxiliar SAD", "Atención domiciliaria"],
        "no": ["Gerocultor/a", "Auxiliar de enfermería", "Enfermero/a", "Niñera", "Limpiador/a", "Fisioterapeuta",
               "Auditor/a interno/a de calidad", "Educador/a social", "Recepcionista"],
    },
    "sanidad.farmacia": {
        "si": ["Clinical Informatics Analyst - Pharmacist", "Técnico/a de farmacia", "Auxiliar de farmacia", "Farmacéutico/a",
               "Tècnic/a de farmàcia", "Pharmacy Technician", "Dependiente/a de parafarmacia", "Farmacéutico/a adjunto/a", "Farmacia hospitalaria"],
        "no": ["Técnico/a de laboratorio", "Auxiliar de enfermería", "Dependiente/a", "Visitador/a médico", "Fisioterapeuta",
               "Técnico/a de Calidad", "Enfermero/a", "Químico/a", "Higienista dental"],
    },
    "sanidad.fisio": {
        "si": ["Fisioterapeuta", "Fisioterapeuta deportivo", "Physiotherapist", "Fisioterapia a domicilio", "Osteópata", "Readaptador/a físico",
               "FISIOTERAPEUTA (H/M)", "Physical Therapist", "Fisio para clínica"],
        "no": ["Enfermero/a", "Monitor/a de natación", "Personal trainer", "Auxiliar de enfermería", "Masajista", "Técnico/a de farmacia",
               "Terapeuta ocupacional", "Logopeda", "Médico/a rehabilitador/a"],
    },
    "sanidad.tecnicos": {
        "si": ["Técnico/a Laboratorio (52,14%) - Centro Médico Boreal", "Tècnic superior Imatge", "Técnico/a Superior de Laboratorio, Ref. S39-2026",
               "Técnico/a de radiodiagnóstico", "TSID", "Técnico/a de rayos", "Técnico/a de anatomía patológica", "Radiographer", "Flebotomista"],
        "no": ["Research Laboratory Technician (Diabetes and Metabolism Research Group)", "Analista de Laboratorio", "Químico/a",
               "Técnico/a de Calidad", "Enfermero/a", "Auxiliar de enfermería", "Farmacéutico/a", "Analista químico/a",
               "Laboratory Technician"],
    },
    "sanidad.dental": {
        "si": ["Protésico Dental (Inglés) -  Trainer & Technical Support Internacional (CAD/CAM)", "Auxiliar de clínica dental",
               "Higienista dental", "Auxiliar dental", "Odontólogo/a", "Dentista", "Recepcionista clínica dental", "Técnico/a en higiene bucodental",
               "Ortodoncista"],
        "no": ["Higienista industrial", "Auxiliar de enfermería", "TCAE", "Enfermero/a", "Fisioterapeuta", "Auxiliar de farmacia",
               "Recepcionista", "Técnico/a de laboratorio", "Comercial junior"],
    },
    # ------------------------------------------------------------------ EDUCACIÓN
    "educacion.profesor": {
        "si": ["Docente de Ciberseguridad", "Profesor/a de matemáticas", "Profesor/a de FP", "Profesora de secundaria", "Docente",
               "Teacher (Primary)", "Professor/a de música", "Maestro/a de primaria", "Profesor/a de academia"],
        "no": ["Candidaturas espontáneas - Personal no docente - Colegio Internacional La Miranda", "Formador/a", "Educador/a infantil",
               "Monitor/a de comedor", "Orientador/a Laboral", "Técnico/a de Formación Subvencionada", "Personal trainer",
               "Educador/a social", "Comercial de formación"],
    },
    "educacion.infantil": {
        "si": ["Educador/a infantil", "Educadora infantil 0-3", "Técnico/a superior en educación infantil", "Maestra de educación infantil",
               "Escola bressol - educadora", "Auxiliar de educación infantil", "Guardería: educador/a", "Tècnic/a d'educació infantil",
               "Early Years Teacher"],
        "no": ["Niñera", "Monitor/a de comedor", "Profesor/a de matemáticas", "Educador/a social", "Canguro", "Au pair", "Formador/a",
               "Cuidadora", "Maestro/a de primaria"],
    },
    "educacion.idiomas": {
        "si": ["Profesor/a de inglés", "English Teacher", "Profesor/a nativo/a de inglés", "Professor/a d'anglès", "Profesor/a de francés",
               "Auxiliar de conversación", "TEFL Teacher", "Language Teacher (Spanish)", "Profesor/a de español (ELE)"],
        "no": ["Customer Service Representative (Spanish and English)", "Traductor/a", "Profesor/a de matemáticas", "Docente",
               "Atención al cliente con inglés", "Formador/a", "Intérprete", "Recepcionista bilingüe", "Comercial con inglés"],
    },
    "educacion.formador": {
        "si": ["Formador/a de sistemas de alarmas en Barcelona", "Formador/a – Instalador/a PMS",
               "Protésico Dental (Inglés) -  Trainer & Technical Support Internacional (CAD/CAM)", "Formador/a", "Formadora de certificados de profesionalidad",
               "Instructor/a de autoescuela", "Docente de formación para el empleo", "Facilitador/a de talleres"],
        "no": ["Técnico/a de Formación Subvencionada", "Profesor/a de matemáticas", "Trainee de finanzas", "Monitor/a de comedor",
               "Coach ejecutivo", "Orientador/a Laboral", "Educador/a social", "Recruiter", "Training of a model (data annotator)"],
    },
    "educacion.monitor": {
        "si": ["Técnicos/as TASOC/TAFAD y Monitores/as para Centros de Mayores", "Monitor/a de comedor", "Monitor/a de tiempo libre", "Monitora de menjador", "Monitor/a de colonias", "Animador/a sociocultural",
               "Director/a de tiempo libre", "Auxiliar de comedor escolar"],
        "no": ["Técnico/a de Monitorización (24x7) Remoto", "Information Security Functional Monitoring (m/f/d)", "Application Security Monitoring",
               "Técnico de sistemas monitorización", "Educador/a infantil", "Profesor/a de matemáticas", "Personal trainer",
               "Socorrista", "Niñera"],
    },
    "educacion.social": {
        "si": ["TREBALLADOR/A SOCIAL - SERVEI BARNAHUS - BARCELONA", "Educador/a social", "Integrador/a social",
               "Técnico/a en integración social", "Trabajador/a social", "Vetllador/a", "Auxiliar técnico/a educativo/a",
               "Educador/a de centro de menores", "Psicopedagogo/a"],
        "no": ["Community manager", "Social media", "Educador/a infantil", "Monitor/a de comedor", "Orientador/a Laboral",
               "Agente cívico", "Profesor/a de matemáticas", "Gerocultor/a", "Social Insights Analyst"],
    },
    "educacion.civico": {
        "si": ["Agente cívico", "Agentes cívicos para convivencia nocturna", "Agent/a cívic/a", "Educador/a de calle", "Mediador/a comunitario/a",
               "Mediador/a intercultural", "Informador/a cívico/a", "Agente de convivencia", "Agentes de movilidad"],
        "no": ["Mediador/a de seguros", "Vigilante de seguridad", "Educador/a social", "Agente de viajes", "Agente de seguros",
               "Informador/a turístico/a", "Agente comercial", "Conserje", "Auxiliar de servicios"],
    },
    "educacion.ninera": {
        "si": ["Niñera", "Niñero/a para tardes", "Canguro", "Au pair", "Nanny (English speaking)", "Babysitter", "Cuidadora de niños",
               "Interna con niños", "Cangur per a les tardes"],
        "no": ["Educador/a infantil", "Cuidadora", "Monitor/a de comedor", "Profesor/a de inglés", "Limpiador/a", "Gerocultor/a",
               "Auxiliar de ayuda a domicilio", "Empleada de hogar", "Maestra de educación infantil"],
    },
    # ------------------------------------------------------------------ HOSTELERÍA
    "hosteleria.camarero": {
        "si": ["Waiter/Waitress", "Camarero/a", "CAMARERA", "Camarero/a de sala", "Cambrer/a", "Camarero/a extra fines de semana",
               "Ayudante de camarero", "Bartender", "Camareros/as para terraza"],
        "no": ["Camarera de pisos", "Camarero/a de pisos", "Cambrera de pisos", "Cocinero/a", "Ayudante de cocina", "Recepcionista de hotel",
               "Barista", "Steward de vuelo", "Gobernanta"],
    },
    "hosteleria.cocinero": {
        "si": ["Sous Chef", "Junior & Sous Chef (H/M)", "Cocinero/a", "COCINERA", "Cuiner/a", "Jefe/a de partida", "Cook",
               "Pizzero/a", "Cocinero/a de colectividades"],
        "no": ["Ayudante de cocina", "Montador/a de cocinas", "Comercial de cocinas", "Camarero/a", "Friegaplatos", "Pastelero/a",
               "Diseñador/a de cocinas", "Dependiente/a de cafetería", "Commis de cocina"],
    },
    "hosteleria.ayudante": {
        "si": ["Ayudante de cocina", "Friegaplatos", "Auxiliar de cocina", "Pinche de cocina", "Kitchen Porter", "Ajudant/a de cuina",
               "Personal de office", "Commis de cocina", "Manipulador/a de alimentos"],
        "no": ["Cocinero/a", "Camarero/a", "Ayudante de camarero", "Montador/a de cocinas", "Limpiador/a", "Operario/a de producción",
               "Barista", "Steward de vuelo", "Ayudante electricista"],
    },
    "hosteleria.recepcion": {
        "si": ["Guest Experience Agent - Hotel Arts Barcelona", "Recepcionista de hotel", "Recepcionista nocturno/a", "Front Desk Agent",
               "Night Auditor", "Recepción de hotel 4*", "Recepcionista d'hotel", "Concierge", "Jefe/a de recepción"],
        "no": ["Recepcionista", "Recepcionista clínica dental", "Camarera de pisos", "Agente de viajes", "Security Supervisor - Hotel Arts Barcelona",
               "Técnico/a de Soporte – Software Hotelero (PMS Cloud)", "TÉCNICO/A JUNIOR DE GDS - RESERVAS Y DISTRIBUCIÓN HOTELERA",
               "Front Office Sales Support", "Conserje"],
    },
    "hosteleria.pisos": {
        "si": ["Camarera de pisos", "Camarero/a de pisos", "Cambrera de pisos", "Gobernanta", "Housekeeping Attendant", "Room Attendant",
               "Valet de pisos", "Auxiliar de pisos", "Camarero/a de habitaciones"],
        "no": ["Camarero/a", "Limpiador/a", "Recepcionista de hotel", "Housekeeper (familia)", "Dependienta de lencería", "Cocinero/a",
               "Ayudante de cocina", "Conserje", "Mozo/a de almacén"],
    },
    "hosteleria.cafeteria": {
        "si": ["Barista", "Dependiente/a de cafetería", "Equipo de restaurante (comida rápida)", "Team member restaurante",
               "Restauración rápida - personal", "Empleado/a de cafetería", "Heladero/a", "Auxiliar de restauración", "Fast food crew"],
        "no": ["Camarero/a", "Cocinero/a", "Dependiente/a", "Cabin crew", "Panadero/a", "Ayudante de cocina", "Cajero/a",
               "Miembro del equipo de ciberseguridad", "Repartidor/a"],
    },
    "hosteleria.turismo": {
        "si": ["Agente de Viajes en Remoto/Híbrido con experiencia", "B2B Travel Advisor Support (French Speaker)",
               "TÉCNICO/A JUNIOR DE GDS - RESERVAS Y DISTRIBUCIÓN HOTELERA", "Agente de viajes", "Guía turístico/a", "Informador/a turístico/a",
               "Tour Leader", "Técnico/a de turismo", "Agent/a de viatges"],
        "no": ["Technology Risk IT Auditor - French Speaker Junior -  EY GDS Spain - Hybrid", "Recepcionista de hotel", "Camarero/a",
               "Atención al público", "Agente de seguros", "Agente cívico", "Conductor de autocar", "Azafata de congresos", "Customer Service Representative"],
    },
    # ------------------------------------------------------------------ COMERCIO
    "comercio.dependiente": {
        "si": ["Asesor de Ventas [ comfort zone ] – Madrid", "Comercial de Tienda", "Dependiente/a", "Dependienta", "Dependent/a",
               "Vendedor/a de tienda", "Sales Assistant (retail)", "Personal de tienda", "Beauty Advisor"],
        "no": ["Atención a personas dependientes", "Cajero/a", "Reponedor/a", "Encargado/a de tienda", "Comercial junior",
               "Asesor/a comercial", "Visual merchandiser", "Camarero/a", "Agente de atención al cliente"],
    },
    "comercio.cajero": {
        "si": ["Cajero/a", "Cajera", "Cajero/a reponedor/a", "Caixer/a", "Cashier", "Personal de caja", "Cajeros/as supermercado",
               "Auxiliar de caja", "CAJERA (H/M)"],
        "no": ["Técnico/a de cajeros automáticos", "Reponedor/a", "Dependiente/a", "Contable", "Tesorería", "Cajero automático - mantenimiento",
               "Encargado/a de tienda", "Camarero/a", "Recepcionista"],
    },
    "comercio.reponedor": {
        "si": ["Reponedor/a", "Reponedora", "Auxiliar de supermercado", "Reposador/a", "Merchandiser", "Promotor/a de punto de venta",
               "Personal de supermercado", "Reposición de lineales", "Stock Associate"],
        "no": ["Visual merchandiser", "Cajero/a", "Mozo/a de almacén", "Dependiente/a", "Encargado/a de tienda", "Preparador/a de pedidos",
               "Carretillero/a", "Comercial junior", "Repartidor/a"],
    },
    "comercio.frescos": {
        "si": ["Carnicero/a", "Pescadero/a", "Charcutero/a", "Panadero/a", "Profesional de frescos", "Carnisser/a", "Forner/a",
               "Frutero/a", "Sección de frescos - dependiente/a"],
        "no": ["Cajero/a", "Reponedor/a", "Cocinero/a", "Operario/a de producción cárnica", "Mozo/a de almacén", "Camarero/a",
               "Dependiente/a", "Encargado/a de tienda", "Técnico/a de Seguridad Alimentaria"],
    },
    "comercio.encargado": {
        "si": ["Encargado/a de tienda", "Responsable de tienda", "Subdirector/a de tienda", "Store Manager", "Assistant Store Manager",
               "Jefe/a de sección", "Encarregat/da de botiga", "Encargado/a de turno", "Supervisor/a de tienda"],
        "no": ["Dependiente/a", "Cajero/a", "Reponedor/a", "Visual merchandiser", "Encargado/a de obra", "Encargado/a de mantenimiento",
               "Área de tienda online", "Comercial junior", "Mozo/a de almacén"],
    },
    "comercio.visual": {
        "si": ["Visual merchandiser", "Visual Merchandising Specialist", "Escaparatista", "Aparadorista", "VM Coordinator",
               "Montaje de escaparates", "Visual Merchandiser (moda)", "Merchandising visual", "Visual coordinator retail"],
        "no": ["Técnico/a de Control de Calidad Visual", "Visual Basic for Appliactions (VBA) - 100% REMOTE, Spain", "Merchandiser",
               "Diseñador/a gráfico/a", "Dependiente/a", "Encargado/a de tienda", "Reponedor/a", "Visual designer", "Fotógrafo/a"],
    },
    # ------------------------------------------------------------------ LOGÍSTICA
    "logistica.mozo": {
        "si": ["Mozo/a de almacén", "Mozo de almacén sin experiencia", "Operario/a de almacén", "Preparador/a de pedidos", "Picking",
               "Mosso/a de magatzem", "Warehouse Operative", "Auxiliar de almacén", "Mozo de carga y descarga"],
        "no": ["Administrador/a de Almacenamiento SAN/NAS (Nivel 2)", "Gestion de Almacén y Logística", "SAP Functional Consultant - Warehouse",
               "Carretillero/a", "Repartidor/a", "Técnico/a de Logística", "Conductor/a de camión", "Reponedor/a", "Administrativo/a de almacén"],
    },
    "logistica.carretillero": {
        "si": ["Carretillero/a", "Carretillera", "Carretillero/a frontal", "Operador/a de carretilla elevadora", "Forklift Driver",
               "Carretoner/a", "Transpaletista", "Conductor/a de carretilla", "Reach truck operator"],
        "no": ["Mozo/a de almacén", "Conductor/a de camión", "Repartidor/a", "Operario/a de producción", "Gruista", "Técnico/a de Logística",
               "Preparador/a de pedidos", "Mecánico/a de automoción", "Peón de obra"],
    },
    "logistica.conductor": {
        "si": ["Conductor/a de camión", "Chófer C+E", "Conductor/a carnet C", "Camionero/a", "Truck Driver", "Conductor/a de tráiler",
               "Conductor/a de autobús", "Xofer de camió", "Transportista nacional"],
        "no": ["Técnico de Control de Transporte  (H/M)", "Analista de Optimización de transporte (H/M)", "Repartidor/a",
               "Conductor/a de línea (fábrica)", "Carretillero/a", "Mozo/a de almacén", "Técnico/a de Logística", "Mecánico/a de automoción",
               "Conductor eléctrico (cable)"],
    },
    "logistica.reparto": {
        "si": ["Repartidor/a", "Repartidora", "Repartidor/a con furgoneta", "Conductor/a de reparto", "Delivery Driver", "Mensajero/a",
               "Rider", "Courier", "Reparto de última milla"],
        "no": ["Conductor/a de camión", "Mozo/a de almacén", "Carretillero/a", "Técnico/a de Logística", "Cajero/a", "Camarero/a",
               "Repartición de tareas (coordinador)", "Comercial junior", "Dependiente/a"],
    },
    "logistica.tecnico": {
        "si": ["Técnico/a de Logística", "Técnico de Control de Transporte  (H/M)", "Analista logístico", "Analista de Optimización de transporte (H/M)",
               "Técnico de logística y administración junior", "Especialista en Gestión de Pedidos y Logística", "Logistics Coordinator",
               "Técnico/a de tráfico", "Administrativo/a de logística"],
        "no": ["Logistics IT Systems Specialist", "Consultor/a Junior SAP Logistica", "Digitalización y BI - Logística", "Mozo/a de almacén",
               "Conductor/a de camión", "Repartidor/a", "Carretillero/a", "Comprador/a", "Gestion de Almacén y Logística"],
    },
    "logistica.compras": {
        "si": ["Buyer", "Técnico de Compras", "Técnico de Compras de Instalaciones Industriales.", "Comprador/a", "Purchasing Assistant",
               "Procurement Specialist", "Aprovisionamiento (H/M)", "Gestor/a de compras", "Tècnic/a de compres"],
        "no": ["Junior Media Buyer", "Técnico/a de Logística", "Contable", "Accounts Payable Specialist", "Mozo/a de almacén",
               "Comercial junior", "Cajero/a", "Purchase to pay master data Analyst", "Administrativo/a"],
    },
    # ------------------------------------------------------------------ INDUSTRIA
    "industria.operario": {
        "si": ["Operario/a de Producción", "Operario/a de producción de impresión", "Operador/a de Producción", "Operaria de producción",
               "Peón industrial", "Operario/a de línea de envasado", "Soldador/a", "Production Operator", "Operari/ària de producció"],
        "no": ["Operario/a de almacén", "Operario/A Mantenimiento Industrial", "Production Process Engineer", "Técnico/a de Calidad",
               "Mozo/a de almacén", "Ingeniero/a de procesos", "Operario/a de limpieza", "Operador/a de CRA", "Peón de obra"],
    },
    "industria.calidad": {
        "si": ["Técnico/a de Calidad", "Técnico/a de Control de Calidad Visual", "Técnico/a de Seguridad Alimentaria", "Inspector/a de calidad",
               "Técnico/a Calidad y Medio Ambiente.", "Quality Specialist", "Técnico de Validaciones Junior", "Tècnic/a de qualitat",
               "Packaging Quality Control"],
        "no": ["Content Quality Analyst", "Agente de Calidad de Contact Center (QA)", "QA Engineer", "Técnico/a Prevención de Riesgos Laborales",
               "Auditor/a junior", "Operario/a de Producción", "Técnico/a de laboratorio", "Ingeniero/a de procesos", "Analista de calidad de llamadas"],
    },
    "industria.mantenimiento": {
        "si": ["Técnico/a de mantenimiento industrial", "Técnico/a de Mantenimiento Eléctrico y Electrónica", "Operario/A Mantenimiento Industrial",
               "Mecánico/a de Mantenimiento de Maquinaria y Equipos Industriales", "Técnico de FP Mecánica o Mecatrónica Industrial (Elevación y Mecánica)",
               "AUTOMATISTAS Y ROBÓTICOS -SERVICIO MANTENIMIENTO INDUSTRIAL - ZONA FRANCA", "Electromecánico/a", "Maintenance Engineer",
               "Técnico/a de automatismos"],
        "no": ["Técnico/a de mantenimiento - Hoteles Yurbban", "Técnico/a de mantenimiento", "Oficial Mantenimiento", "Técnico/a de mantenimiento informático",
               "Mecanico oficial de 1a", "Electricista", "Ingeniero/a de procesos", "Operario/a de Producción", "Técnico/a de Calidad"],
    },
    "industria.ingenieria": {
        "si": ["Process Engineer", "Production Process Engineer", "Manufacturing Engineer", "Controls Engineer", "Ingeniero/a Eléctrico/a Junior",
               "Ingeniero/a de automatización", "Técnico/a de Mejora Continua y Excelencia Operacional", "Ingeniero/a de procesos", "Ingeniero/a industrial"],
        "no": ["AI Automation Engineer", "Junior Fortinet Automation Engineer | Terraform | Python | 100% Remoto", "Android Test Automation Engineer (Native App)",
               "Ingeniero/a de Telecomunicación Junior (Barcelona)", "Ingeniero/a Civil Junior", "Data Engineer", "Delineante",
               "Técnico/a de Calidad", "Operario/a de Producción"],
    },
    "industria.oficina_tecnica": {
        "si": ["Delineante", "Ingeniero/a de Oficina Técnica", "Técnico/a Junior de presupuestos y oficina técnica",
               "Diseñador/a Técnico/a - Soporte a Dirección", "Proyectista", "Dibujante CAD", "Modelador/a BIM", "CAD Technician", "Delineant"],
        "no": ["Diseñador/a gráfico/a", "Junior UI/UX Designer", "Ingeniero/a de procesos", "Topógrafo", "Técnico/a de Calidad",
               "Arquitecto/a técnico", "Diseñador/a web", "Ingeniero/a Civil Junior", "Instructional designer"],
    },
    "industria.prl": {
        "si": ["Técnico de PRL (They/She/He)", "Técnico/a Prevención de Riesgos Laborales", "HSE Specialist", "Junior Health & Safety Technician",
               "Técnico/a de Medioambiente", "Recurso Preventivo - Seguridad en Obra", "COORDINADOR/A DE SEGURIDAD Y SALUD - OBRA INDUSTRIAL",
               "Becario/a Técnico de PRL", "Higienista industrial"],
        "no": ["Técnico/a de Calidad", "Vigilante de Seguridad (Barcelona)", "Técnico/a de ciberseguridad", "Security Operations Analyst",
               "Técnico/a de Seguridad Alimentaria", "Médico/a de familia", "Técnico/a de Sostenibilidad Audiovisual", "Auditor/a junior",
               "Higienista dental"],
    },
    "industria.laboratorio": {
        "si": ["Research Laboratory Technician (Diabetes and Metabolism Research Group)", "Laboratory Technician", "Analista de Laboratorio",
               "Técnico/a de Laboratorio", "Técnico de Microbiología Ambiental (Turno de tarde)", "Químico/a", "Técnico/a de I+D cosmética",
               "Auxiliar de laboratorio", "Analista químico/a"],
        "no": ["Técnico/a de radiodiagnóstico", "Técnico/a de Calidad", "Biomedical Sciences Graduate – Research Project Support (Immunology/Neuromuscular Disorders)",
               "Farmacéutico/a", "Ingeniero/a de procesos", "Enfermero/a", "Operario/a de Producción", "Data Engineer", "Flebotomista"],
    },
    "industria.civil": {
        "si": ["Ingeniero/a Civil Junior", "2026 Spain Graduate Civil & Structural Engineering Career Path", "Topógrafo",
               "Topógrafo/a de Campo Túnel Rubí", "Diseñador/a Civil -Fotovoltaico (Remoto) - PVCase", "TÉCNICO/A DE OBRA Y ESTUDIOS JUNIOR",
               "Ingeniero/a de caminos", "Arquitecto/a técnico", "Ingeniero/a técnico/a de obras públicas"],
        "no": ["Guardia civil", "Técnico/a de protección civil", "Abogado/a civil", "Seguro de responsabilidad civil - comercial",
               "Peón de obra", "Albañil", "Delineante", "Ingeniero/a de procesos", "Aviación civil: auxiliar de vuelo"],
    },
    # ------------------------------------------------------------------ OFICIOS
    "oficios.electricista": {
        "si": ["Electricista", "Oficial electricista", "Ayudante electricista", "Oficial de 1ª electricista", "Electrician",
               "Instalador/a eléctrico", "Técnico/a de instalaciones eléctricas", "Montador/a eléctrico", "ELECTRICISTA (H/M)"],
        "no": ["Ingeniero/a Eléctrico/a Junior", "Técnico/a de mantenimiento", "Fontanero/a", "Técnico/a de telecomunicaciones",
               "Instaladores/as de Telecomunicaciones", "Técnico/a de climatización", "Albañil", "Mecánico/a de automoción", "Conductor eléctrico (cable)"],
    },
    "oficios.fontaneria": {
        "si": ["TÈCNIC / TÈCNICA MANTENIMENT_ CALEFACTOR", "Fontanero/a", "Lampista", "Técnico/a de climatización", "Frigorista",
               "Instalador/a de calefacción", "Técnico/a de calderas", "HVAC Technician", "Oficial de fontanería"],
        "no": ["Electricista", "Técnico/a de mantenimiento", "Albañil", "Asesor energético", "Pintor/a",
               "Ingeniero/a de procesos", "Técnico/a de Calidad", "Mecánico/a de automoción", "Limpiador/a"],
    },
    "oficios.mantenimiento": {
        "si": ["Técnico/a de mantenimiento - Hoteles Yurbban", "5* Hotel Técnico/a de mantenimiento - Grand Hotel Central Barcelona",
               "Oficial Mantenimiento", "Técnico/a de mantenimiento polivalente", "Maintenance Technician - W Barcelona",
               "Técnico/a Mantenimiento Ascensores", "Oficial de Mantenimiento Royal Fira", "Técnico de Mantenimiento", "Mantenimiento de edificios"],
        "no": ["Técnico/a de mantenimiento industrial", "Operario/A Mantenimiento Industrial", "Técnico de Mantenimiento Industrial",
               "Electricista", "Fontanero/a", "Limpiador/a", "Conserje", "Mecánico/a de Mantenimiento de Maquinaria y Equipos Industriales",
               "Ingeniero/a de procesos"],
    },
    "oficios.construccion": {
        "si": ["Albañil", "Peón de obra", "Oficial de primera albañilería", "Paleta", "Encofrador/a", "Ferrallista", "Operario/a de construcción",
               "Peón de la construcción", "Gruista"],
        "no": ["Ingeniero/a Civil Junior", "Arquitecto/a técnico", "Pintor/a", "Electricista", "Fontanero/a", "Jefe/a de obra",
               "Técnico/a Prevención Riesgos Laborales Construcción", "Topógrafo", "Carpintero/a"],
    },
    "oficios.acabados": {
        "si": ["Pintor/a", "Pladurista", "Carpintero/a", "Ebanista", "Montador/a de muebles", "Instalador/a de ventanas de aluminio",
               "Fuster/a", "Cerrajero/a", "Parquetista"],
        "no": ["Albañil", "Electricista", "Fontanero/a", "Chapista", "Diseñador/a gráfico/a", "Delineante",
               "Técnico/a de mantenimiento", "Montador/a industrial", "Interiorista"],
    },
    "oficios.mecanico": {
        "si": ["Mecánico/a de Campo", "Técnico/a de automoción, Badalona", "Mecanico oficial de 1a", "Mecánico/a de automoción",
               "Chapista", "Pintor/a de carrocería", "Mecánico/a de vehículos industriales", "Electromecánico/a de automoción", "Car Mechanic"],
        "no": ["Mecánico/a de Mantenimiento de Maquinaria y Equipos Industriales", "Técnico/a de Montaje de Escaleras Mecánicas. Barcelona (M/F/D)",
               "Técnico de FP Mecánica o Mecatrónica Industrial (Elevación y Mecánica)", "Ingeniero/a mecánico/a", "Electricista",
               "Garage Support Representative (m/f/d) - Especialista en Soporte Técnico de Automoción", "Conductor/a de camión",
               "Técnico/a de mantenimiento", "Comercial de automoción"],
    },
    "oficios.limpieza": {
        "si": ["Limpiador/a", "Limpiadora", "Personal de limpieza", "Operario/a de limpieza", "Limpieza de oficinas", "Netejador/a",
               "Empleada de hogar", "Cleaner", "Auxiliar de limpieza"],
        "no": ["Limpieza de datos (data cleaning)", "Data cleansing analyst", "Camarera de pisos", "Conserje", "Mozo/a de almacén",
               "Técnico/a de mantenimiento", "Reponedor/a", "Comercial de productos de limpieza", "Ayudante de cocina"],
    },
    "oficios.vigilancia": {
        "si": ["Vigilante de Seguridad (Vic)", "Vigilante de Seguridad en Barcelona (H/M/D)", "Security Supervisor - Hotel Arts Barcelona",
               "Conserje", "Auxiliar de servicios", "Controlador/a de accesos", "Vigilant/a de seguretat", "Security Guard", "Portero/a de discoteca"],
        "no": ["Information System Security Officer (Cybersecurity Assessment & Authorization)", "Security Operations Analyst",
               "Técnico/a de Sistemas de Seguridad  (Barcelona)", "Técnico/a Prevención de Riesgos Laborales", "Kitchen Porter",
               "Agente cívico", "Recepcionista", "Limpiador/a", "IT Security Officer"],
    },
}


# Títulos que casan con el núcleo pero que un negativo DEL PROPIO puesto tiene que bajar: es lo que
# prueba que los negativos hacen algo. No van en «si», porque no deben llegar a Encaje alto por el
# núcleo y nada más.
REBAJADAS = {
    "it.soporte": ["Técnico/a de soporte preventa", "Técnico/a de Soporte N3"],
    "it.desarrollo": ["Desarrollador/a de negocio", "SOPORTE N1 SHAREPOINT, MICROSOFT 365  ITIL INGLES B2/ HIBRIDO MADRID/H/M/X"],
    "it.qa": ["QA en planta farmacéutica (GMP)"],
    "admin.administrativo": ["Abogado/a contencioso-administrativo", "Administrativo/a comercial"],
    "admin.recepcion": ["Recepción de mercancías (almacén)"],
    "atencion.teleoperador": ["Helpdesk / jornada parcial (Teleoperador/a)", "TELEOPERADOR SOPORTE TÉCNICO (H/M)"],
    "atencion.postventa": ["Técnico/a de Automatización Postventa", "Aftersales Engineer Coordinator", "Gestor/a de incidencias informáticas"],
    "marketing.comunicacion": ["Infrastructure Security Communication Specialist", "Operario/a de prensa"],
    "finanzas.auditoria": ["Assurance | Auditor IT junior 2025 Barcelona", "Auditor Hacking Ético (Barcelona)", "Night Auditor (hotel)"],
    "rrhh.generalista": ["Technical Product Support Analyst (HR Talent)"],
    "rrhh.formacion": ["Técnico/a de Formación (FUNDAE) y Comercial"],
    "sanidad.tcae": ["Auxiliar de clínica dental", "Auxiliar de clínica veterinaria"],
    "sanidad.domicilio": ["Cuidador/a de perros"],
    "educacion.formador": ["Trainer (Sales)", "Personal trainer"],
    "educacion.monitor": ["Monitor de Natación y Aquagym en Pechina", "Monitor/a de sistemas 24x7"],
    "logistica.mozo": ["Mozo de almacén autónomo"],
    "industria.laboratorio": ["Técnico/a de laboratorio clínico"],
}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    con = sqlite3.connect("file:%s?mode=ro" % Path(argv[0]).as_posix(), uri=True)
    reales = {json.loads(d).get("title", "").strip() for (d,) in con.execute("SELECT datos FROM ofertas")}
    con.close()
    salida = {}
    for puesto, listas in M.items():
        salida[puesto] = {}
        for lado in ("si", "no"):
            salida[puesto][lado] = [{"titulo": t} if t in reales else {"titulo": t, "sintetico": True}
                                    for t in listas[lado]]
        if puesto in REBAJADAS:
            salida[puesto]["rebajadas"] = [{"titulo": t} if t in reales else {"titulo": t, "sintetico": True}
                                           for t in REBAJADAS[puesto]]
    texto = json.dumps(salida, ensure_ascii=False, indent=1) + "\n"
    SALIDA.write_text(texto, encoding="utf-8", newline="\n")
    n = sum(len(x) for p in salida.values() for x in p.values())
    r = sum(1 for p in salida.values() for x in p.values() for t in x if "sintetico" not in t)
    print("catalogo_muestras.json: %d puestos, %d títulos, %d reales" % (len(salida), n, r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
