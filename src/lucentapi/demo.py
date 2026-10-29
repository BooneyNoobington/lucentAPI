"""Demo data for development and tests: the same people, locations and methods as lucentLIMS's
mock data (with the same ids), plus a few weeks of collections and values.

`load(conn, now)` fills an empty database. Collections and values are generated with a fixed
seed relative to `now`, so a run looks the same every time.
"""

import random
from datetime import datetime, timedelta

from sqlalchemy import Connection, insert

from .db import utcnow
from .geometry import from_text, polygon_wkt
from .numbering import LUCENT
from .schema import (
    action,
    city,
    collection,
    collection_class,
    crs,
    device,
    dimension,
    district,
    house,
    j_action_collection_class,
    j_action_dependency,
    j_action_device,
    j_action_implication,
    j_action_person,
    j_action_variable,
    j_person_organization,
    j_sop_link,
    j_sop_person,
    link,
    matrix,
    message,
    organization,
    person,
    raw_value,
    site,
    sop,
    spot,
    street,
    task,
    unit,
    unit_notation,
    value,
    variable,
)

NOOBINGTON, DOE, DORN, SIMPSON, PICARD, RIPLEY = 23, 24, 26, 27, 28, 29
NUMBERING_OFFSET = 3000


def rows(table, columns: str, *values) -> tuple:
    names = columns.split()
    return table, [dict(zip(names, v), vis=True) for v in values]


def reference_data() -> list[tuple]:
    return [
        rows(organization, "id_organization name shorthand",
             (5, "Redhsoft Inc", "Redhsoft"), (10, "Umweltlabor Lucent", "Lucent"),
             (11, "Ionic Instruments GmbH", "Ionic"), (12, "Spectra Analytik AG", "Spectra"),
             (13, "Klarsicht Messtechnik KG", "Klarsicht"), (14, "Aerosol Systems GmbH", "Aerosol")),
        rows(person, "id_person given_name surname unix_account id_organization",
             (NOOBINGTON, "Booney", "Noobington", "grindel", 5), (DOE, "John", "Doe", "jd", 10),
             (DORN, "Michael", "Dorn", "worf", 10), (SIMPSON, "Homer", "Simpson", "s7e23", 10),
             (PICARD, "Jean Luc", "Picard", "p47at", 10), (RIPLEY, "Ellen", "Ripley", "xx121", 10)),
        rows(j_person_organization, "id_person id_organization position",
             (NOOBINGTON, 5, "Administration"), (DOE, 10, "Analytik"), (DORN, 10, "Probenahme"),
             (SIMPSON, 10, "Luftmessnetz"), (PICARD, 10, "Laborleitung"), (RIPLEY, 10, "Qualitätssicherung")),
        rows(crs, "id_crs epsg name description", (1, 4326, "WGS 84", "World Geodetic System 1984")),
        rows(city, "id_city name", (1, "Karlsruhe")),
        rows(district, "id_district name id_city zip",
             (1, "Oststadt", 1, "76131"), (2, "Durlach", 1, "76227"), (3, "Knielingen", 1, "76187")),
        rows(street, "id_street name id_district", (1, "Laborstraße", 1), (2, "Am Wasserwerk", 2), (3, "Rheinauenweg", 3)),
        rows(house, "id_house number number_addition id_street", (1, "4", None, 1), (2, "1", "a", 2), (3, "12", None, 3)),
        rows(site, "id_site name description id_house id_crs borders",
             (1, "Betriebsgelände Laborstraße", "Labor- und Verwaltungsgebäude", 1, 1,
              [(8.4102, 49.0103), (8.4139, 49.0106), (8.4145, 49.0088), (8.4108, 49.0084)]),
             (2, "Wasserwerk Ost", "Fassungsgelände mit den Brunnen B3 und B7", 2, 1,
              [(8.4510, 48.9990), (8.4600, 48.9995), (8.4612, 48.9950), (8.4515, 48.9944)]),
             (3, "Messfeld Rheinaue", "Grundwassermessstellen am Altrhein", 3, 1,
              [(8.3010, 49.0335), (8.3095, 49.0340), (8.3105, 49.0290), (8.3020, 49.0282)])),
        rows(spot, "id_spot name description x y id_crs id_site",
             (2, "Test 1", "Erster Test", 49.006986, 8.376389, 1, None),
             (3, "Garten", "Unter dem Dach der Fahrradgarage", 49.006097, 8.382112, 1, None),
             (4, "Zapfstelle Labor 2.14", "Kaltwasserhahn im Altbau, 2. OG", 49.0096, 8.4118, 1, 1),
             (5, "Zapfstelle Kantine", "Spülküche, Mischbatterie links", 49.0091, 8.4132, 1, 1),
             (6, "Hochbehälter Nord", "Probenahmehahn Kammer 1", 49.0352, 8.4212, 1, None),
             (7, "Wasserwerk Reinwasser", "Reinwasserbehälter, Ausgang ins Netz", 48.9962, 8.4566, 1, 2),
             (8, "Brunnen B3", "Vertikalfilterbrunnen, 28 m", 48.9978, 8.4531, 1, 2),
             (9, "Brunnen B7", "Vertikalfilterbrunnen, 34 m", 48.9958, 8.4592, 1, 2),
             (10, "GWM 12", "Grundwassermessstelle am Feldrand", 49.0318, 8.3046, 1, 3),
             (11, "Messcontainer Innenstadt", "Verkehrsnahe Luftmessstation", 49.0094, 8.4010, 1, None)),
        rows(matrix, "id_matrix name description",
             (8, "Luft", "Außen- oder Innenluft"), (9, "Leitungswasser", "Aus der Hausinstallation"),
             (10, "Trinkwasser", "Gemäß Trinkwasserverordnung"), (11, "Grundwasser", "Aus Brunnen und Messstellen")),
        rows(collection_class, "id_collection_class name description",
             (1, "Routineprobe", "Routinemäßige Untersuchung von Wasser"),
             (2, "Zeitreihe", "Wiederkehrende Luftmessung an einem Spot"),
             (3, "Umfassende Untersuchung", "Routine plus Anionen und Metalle"),
             (4, "Grundwassermonitoring", "Überwachung der Grundwasserbeschaffenheit")),
        rows(dimension, "id_dimension name description",
             (1, "Dimensionslos", "Größen ohne Einheit, z. B. der pH-Wert"),
             (2, "Temperatur", "Thermodynamische Temperatur"),
             (3, "Elektrische Leitfähigkeit", "Leitwert pro Länge"),
             (4, "Massenkonzentration", "Masse pro Volumen"),
             (5, "Anzahlkonzentration", "Anzahl pro Volumen, z. B. koloniebildende Einheiten")),
        rows(unit, "id_unit name id_dimension",
             (1, "Ohne Einheit", 1), (2, "Grad Celsius", 2), (3, "Mikrosiemens pro Zentimeter", 3),
             (4, "Milligramm pro Liter", 4), (5, "Mikrogramm pro Liter", 4), (6, "Mikrogramm pro Kubikmeter", 4),
             (7, "Koloniebildende Einheiten pro 100 Milliliter", 5)),
        rows(unit_notation, "id_unit notation",
             (2, "°C"), (3, "µS/cm"), (3, "µS cm⁻¹"), (4, "mg/L"), (4, "mg l⁻¹"), (4, "ppm"), (5, "µg/L"),
             (5, "µg l⁻¹"), (5, "ppb"), (6, "µg/m³"), (6, "µg m⁻³"), (7, "KBE/100 mL"), (7, "CFU/100 mL")),
        rows(variable, "id_variable name value_class description id_dimension",
             (1, "Temperatur", "decimal", "Wassertemperatur bei der Probenahme", 2),
             (2, "pH-Wert", "decimal", "Negativer dekadischer Logarithmus der Oxoniumaktivität", 1),
             (3, "Elektrische Leitfähigkeit", "decimal", "Bezogen auf 25 °C", 3),
             (4, "Nitrat", "decimal", "NO₃⁻", 4), (5, "Chlorid", "decimal", "Cl⁻", 4),
             (6, "Sulfat", "decimal", "SO₄²⁻", 4), (7, "Blei", "decimal", "Pb, gesamt", 4),
             (8, "Kupfer", "decimal", "Cu, gesamt", 4), (9, "Nickel", "decimal", "Ni, gesamt", 4),
             (10, "Escherichia coli", "decimal", "Fäkalindikator", 5),
             (11, "Coliforme Bakterien", "decimal", "Hygieneindikator", 5),
             (12, "Feinstaub PM10", "decimal", "Partikel ≤ 10 µm, Tagesmittel", 4),
             (13, "Feinstaub PM2,5", "decimal", "Partikel ≤ 2,5 µm, Tagesmittel", 4),
             (14, "Stickstoffdioxid", "decimal", "NO₂, Tagesmittel", 4),
             (15, "Färbung", "text", "Visuelle Prüfung", None),
             (16, "Geruch auffällig", "boolean", "Olfaktorische Prüfung", None)),
        rows(device, "id_device name number id_manufacturer id_operator active",
             (1, "pH-Meter Lab 1", "PH-2207-114", 11, DOE, True),
             (2, "pH-Meter Lab 2 (Reserve)", "PH-1903-027", 11, DOE, False),
             (3, "Leitfähigkeitsmessgerät LF 4", "LF-2105-033", 11, DOE, True),
             (4, "Ionenchromatograph IC-3", "IC3-0098", 12, DOE, True),
             (5, "ICP-MS Q2", "ICPMS-Q2-4411", 12, PICARD, True),
             (6, "Brutschrank 36 °C", "BS36-002", 13, RIPLEY, True),
             (7, "Feinstaubsammler LVS-3 Nr. 1", "LVS3-1187", 14, SIMPSON, True),
             (8, "Feinstaubsammler LVS-3 Nr. 2", "LVS3-1192", 14, SIMPSON, True),
             (9, "NO₂-Analysator", "NOX500-221", 14, SIMPSON, True),
             (10, "Feinwaage XS205", "XS205-778", 13, DOE, True),
             (11, "Einstichthermometer", "TH-0042", 13, DORN, True)),
        rows(sop, "id_sop name active",
             (1, "SOP-001 Probenahme Wasser", True), (2, "SOP-002 pH-Wert", True),
             (3, "SOP-003 Elektrische Leitfähigkeit", True), (4, "SOP-004 Anionen mittels IC", True),
             (5, "SOP-005 Metalle mittels ICP-MS", True), (6, "SOP-006 E. coli und coliforme Bakterien", True),
             (7, "SOP-007 Feinstaub gravimetrisch", True), (8, "SOP-008 Stickstoffdioxid", True),
             (9, "SOP-009 Datenfreigabe (Vier-Augen-Prinzip)", True), (10, "SOP-010 Probenahme Luft", False)),
        rows(link, "id_link name file_path url accepted",
             (1, "Probenahme Wasser, Rev. 3", "/srv/lucent/sop/SOP-001_r3.pdf", None, True),
             (2, "Probenahme Wasser, Rev. 2 (ersetzt)", "/srv/lucent/sop/SOP-001_r2.pdf", None, False),
             (3, "pH-Wert, Rev. 2", "/srv/lucent/sop/SOP-002_r2.pdf", None, True),
             (4, "Elektrische Leitfähigkeit, Rev. 1", "/srv/lucent/sop/SOP-003_r1.pdf", None, True),
             (5, "Anionen mittels IC, Rev. 4", "/srv/lucent/sop/SOP-004_r4.pdf", None, True),
             (6, "Gerätehandbuch IC-3", None, "https://docs.example.org/ic3/handbuch.pdf", True),
             (7, "Metalle mittels ICP-MS, Rev. 2", "/srv/lucent/sop/SOP-005_r2.pdf", None, True),
             (8, "Entwurf Rev. 3", "/srv/lucent/sop/entwurf/SOP-005_r3.docx", None, False),
             (9, "Membranfiltration, Rev. 1", "/srv/lucent/sop/SOP-006_r1.pdf", None, True),
             (10, "Feinstaub gravimetrisch, Rev. 2", "/srv/lucent/sop/SOP-007_r2.pdf", None, True),
             (11, "Stickstoffdioxid, Rev. 1", "/srv/lucent/sop/SOP-008_r1.pdf", None, True),
             (12, "Datenfreigabe, Rev. 1", "/srv/lucent/sop/SOP-009_r1.pdf", None, True),
             (13, "Entwurf", "/srv/lucent/sop/entwurf/SOP-010.docx", None, False)),
        rows(j_sop_link, "id_sop id_link",
             (1, 1), (1, 2), (2, 3), (3, 4), (4, 5), (4, 6), (5, 7), (5, 8), (6, 9), (7, 10), (8, 11), (9, 12), (10, 13)),
        rows(j_sop_person, "id_sop id_person role",
             (1, DORN, "Autor"), (1, RIPLEY, "Prüfung"), (1, PICARD, "Freigabe"),
             (2, DOE, "Autor"), (2, RIPLEY, "Prüfung"), (2, PICARD, "Freigabe"),
             (3, DOE, "Autor"), (3, RIPLEY, "Prüfung"), (3, PICARD, "Freigabe"),
             (4, DOE, "Autor"), (4, RIPLEY, "Prüfung"), (4, PICARD, "Freigabe"),
             (5, PICARD, "Autor"), (5, RIPLEY, "Prüfung"), (6, RIPLEY, "Autor"), (6, PICARD, "Freigabe"),
             (7, SIMPSON, "Autor"), (7, DOE, "Prüfung"), (7, PICARD, "Freigabe"),
             (8, SIMPSON, "Autor"), (8, PICARD, "Freigabe"),
             (9, RIPLEY, "Autor"), (9, NOOBINGTON, "Prüfung"), (9, PICARD, "Freigabe"), (10, SIMPSON, "Autor")),
        rows(action, "id_action name standard iteration active description id_sop",
             (1, "Probenahme Wasser", "DIN ISO 5667-5", 3, True, "Entnahme der Probe und Messung der Wassertemperatur vor Ort.", 1),
             (2, "Sensorische Prüfung", "DIN EN 1622", 1, True, "Färbung und Geruch, direkt nach der Probenahme.", 1),
             (3, "pH-Wert", "DIN EN ISO 10523", 2, True, "Potentiometrische Messung mit Glaselektrode, Drei-Punkt-Kalibrierung.", 2),
             (4, "Elektrische Leitfähigkeit", "DIN EN 27888", 1, True, "Konduktometrische Messung mit Temperaturkompensation auf 25 °C.", 3),
             (5, "Anionen (IC)", "DIN EN ISO 10304-1", 4, True, "Nitrat, Chlorid und Sulfat mittels Ionenchromatographie.", 4),
             (6, "Metalle (ICP-MS)", "DIN EN ISO 17294-2", 2, True, "Stagnationsprobe S0. Eine Überschreitung wird mit einer Wiederholungsmessung bestätigt.", 5),
             (7, "E. coli und coliforme Bakterien", "DIN EN ISO 9308-1", 1, True, "Membranfiltration, Bebrütung 21 ± 3 h bei 36 °C.", 6),
             (8, "Feinstaub (gravimetrisch)", "DIN EN 12341", 2, True, "24-Stunden-Probenahme auf Filter, Wägung vor und nach der Beaufschlagung.", 7),
             (9, "Stickstoffdioxid", "DIN EN 14211", 1, True, "Chemilumineszenz, Tagesmittelwert.", 8)),
        rows(j_action_collection_class, "id_action id_collection_class",
             *[(a, 1) for a in (1, 2, 3, 4, 7)], *[(a, 2) for a in (8, 9)],
             *[(a, 3) for a in (1, 2, 3, 4, 5, 6, 7)], *[(a, 4) for a in (1, 3, 4, 5)]),
        rows(j_action_dependency, "id_dependant id_dependency dependency_class",
             *[(a, 1, "requires") for a in (2, 3, 4, 5, 6, 7)]),
        rows(j_action_implication, "id_implier id_implication", (1, 2), (5, 4)),
        rows(j_action_device, "id_action id_device",
             (1, 11), (3, 1), (3, 2), (4, 3), (5, 4), (6, 5), (7, 6), (8, 7), (8, 8), (8, 10), (9, 9)),
        rows(j_action_person, "id_action id_person role",
             (1, DORN, "Verantwortlich"), (2, DORN, "Prüfer"), (3, DOE, "Analyst"), (3, PICARD, "Verantwortlich"),
             (4, DOE, "Analyst"), (5, DOE, "Analyst"), (5, PICARD, "Verantwortlich"), (6, PICARD, "Analyst"),
             (6, RIPLEY, "Prüfer"), (7, RIPLEY, "Analyst"), (8, SIMPSON, "Probenehmer"), (8, DOE, "Analyst"),
             (9, SIMPSON, "Verantwortlich")),
        rows(j_action_variable,
             "id_j_action_variable id_action id_variable id_unit uncertainty uncertainty_class decimals lower_limit upper_limit calculation",
             (1, 1, 1, 2, 0.2, "absolute", 1, None, None, None),
             (2, 2, 15, None, None, None, None, None, None, None),
             (3, 2, 16, None, None, None, None, None, 0, None),
             (4, 3, 2, 1, 0.05, "absolute", 2, 6.5, 9.5, None),
             (5, 4, 3, 3, 2, "relative", 0, None, 2790, "κ₂₅ = κ_T · f₂₅(T)"),
             (6, 5, 4, 4, 5, "relative", 1, None, 50, None),
             (7, 5, 5, 4, 5, "relative", 1, None, 250, None),
             (8, 5, 6, 4, 5, "relative", 1, None, 250, None),
             (9, 6, 7, 5, 10, "relative", 1, None, 10, None),
             (10, 6, 8, 4, 8, "relative", 2, None, 2, None),
             (11, 6, 9, 5, 10, "relative", 1, None, 20, None),
             (12, 7, 10, 7, None, None, 0, None, 0, None),
             (13, 7, 11, 7, None, None, 0, None, 0, None),
             (14, 8, 12, 6, 7, "relative", 0, None, 50, "c = (m₂ − m₁) / V"),
             (15, 8, 13, 6, 7, "relative", 0, None, 25, "c = (m₂ − m₁) / V"),
             (16, 9, 14, 6, 7.5, "relative", 0, None, 40, None)),
    ]


# Who samples what where: (spot, site, matrix, class, every n days, first offset, sampler)
SCHEDULES = [
    (3, None, 8, 2, 7, 1, SIMPSON), (11, None, 8, 2, 7, 2, SIMPSON), (4, None, 9, 3, 14, 3, DORN),
    (6, None, 10, 1, 7, 0, DORN), (8, None, 11, 4, 14, 2, DORN), (10, None, 11, 4, 7, 3, RIPLEY),
    (None, 3, 11, 4, 28, 12, RIPLEY),
]
ANALYSTS = {1: DORN, 2: DORN, 3: DOE, 4: DOE, 5: DOE, 6: PICARD, 7: RIPLEY, 8: DOE, 9: SIMPSON}
DEVICES = {1: 11, 3: 1, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 9}


def mock_reading(av: int, spot_id: int | None, progress: float, rng: random.Random):
    """(column, value) for a plausible reading; progress runs from 0 (oldest) to 1 (now)."""
    gauss = rng.gauss
    match av:
        case 1: return "decimal", round(gauss(11.5 if spot_id in (8, 10, None) else 14, 1), 1)
        case 2: return "text", "farblos" if rng.random() < 0.93 else "schwach gelblich"
        case 3: return "boolean", rng.random() < 0.04
        case 4: return "decimal", round(gauss(7.15 if spot_id in (8, 10, None) else 7.6, 0.08), 2)
        case 5: return "decimal", round(gauss(880 if spot_id == 10 else 650, 30))
        case 6: return "decimal", round(38 + 18 * progress + gauss(0, 2) if spot_id == 10 else gauss(18, 2), 1)
        case 7: return "decimal", round(gauss(45, 4), 1)
        case 8: return "decimal", round(gauss(62, 5), 1)
        case 9: return "decimal", round(max(0.1, gauss(12.5, 1) if progress > 0.8 else gauss(7.5, 2.8)), 1)
        case 10: return "decimal", round(max(0.01, gauss(0.45, 0.12)), 2)
        case 11: return "decimal", round(max(0.1, gauss(2.5, 0.8)), 1)
        case 12 | 13: return "decimal", float(rng.choice([0] * 30 + [1, 2]))
        case 14: return "decimal", float(round(max(3, gauss(31 if spot_id == 11 else 19, 9))))
        case 15: return "decimal", float(round(max(2, gauss(19 if spot_id == 11 else 12, 5))))
        case _: return "decimal", float(round(max(3, gauss(36 if spot_id == 11 else 18, 6))))


def load(conn: Connection, now: datetime | None = None, weeks: int = 8) -> None:
    """Fills an empty lucentBASE with demo data. Writes no log rows: this is the initial state."""
    now = now or utcnow()
    for table, data in reference_data():
        for row in data:
            if table is site:
                row["borders"] = from_text(polygon_wkt(row["borders"]))
            conn.execute(insert(table).values(row))

    rng = random.Random(1997)
    planned = {}
    for table, data in reference_data():
        if table is j_action_collection_class:
            for row in data:
                planned.setdefault(row["id_collection_class"], []).append(row["id_action"])
        if table is j_action_variable:
            avs = {row["id_j_action_variable"]: row for row in data}

    horizon = weeks * 7
    drafts = []
    for spot_id, site_id, matrix_id, class_id, every, offset, sampler in SCHEDULES:
        for days_ago in range(offset, horizon + 1, every):
            sampled_at = (now - timedelta(days=days_ago)).replace(hour=rng.randint(7, 10), minute=rng.randint(0, 59),
                                                                  second=0, microsecond=0)
            if sampled_at <= now:
                drafts.append((sampled_at, spot_id, site_id, matrix_id, class_id, sampler))
    drafts.sort()

    value_id = 0
    for collection_id, (sampled_at, spot_id, site_id, matrix_id, class_id, sampler) in enumerate(drafts, start=1):
        conn.execute(insert(collection).values(
            id_collection=collection_id, name=LUCENT.identifier(collection_id + NUMBERING_OFFSET),
            id_matrix=matrix_id, id_collection_class=class_id, id_spot=spot_id, id_site=site_id,
            sampled_at=sampled_at, id_sampler=sampler, vis=True))
        age = (now - sampled_at).total_seconds() / 86_400
        progress = 1 - age / horizon
        actions = planned[class_id]
        if age >= 10:
            done, accepted = len(actions), len(actions)
        elif age >= 4:
            done, accepted = len(actions), len(actions) if rng.random() < 0.5 else len(actions) // 2
        elif age >= 1.5:
            done, accepted = rng.randint(1, len(actions)), 0
        else:
            done, accepted = rng.choice([0, 1]), 0
        for index, action_id in enumerate(actions[:done]):
            measured_at = min(now, sampled_at + timedelta(hours=5 * index, minutes=rng.randint(0, 59)))
            for av_id, av in avs.items():
                if av["id_action"] != action_id:
                    continue
                column, reading = mock_reading(av_id, spot_id, progress, rng)
                repetitions = [reading]
                if av_id == 9 and reading > 10:  # lead above the limit is confirmed by a repetition
                    repetitions.append(round(reading * rng.gauss(1, 0.04), 1))
                for repetition, number in enumerate(repetitions, start=1):
                    value_id += 1
                    conn.execute(insert(value).values(
                        id_value=value_id, id_collection=collection_id, id_j_action_variable=av_id,
                        repetition=repetition, accepted=index < accepted, vis=True, **{f"{column}_value": number}))
                    conn.execute(insert(raw_value).values(
                        id_value=value_id, t=measured_at, id_person=ANALYSTS[action_id],
                        id_device=DEVICES.get(action_id), accepted=index < accepted, vis=True,
                        **{f"{column}_raw_value": number}))

    day = now.replace(hour=16, minute=0, second=0, microsecond=0)
    for row in [
        (1, "pH-Meter Lab 1 kalibrieren", "Drei-Punkt-Kalibrierung mit Puffer 4, 7 und 10.", PICARD, DOE, -1, False),
        (2, "Blei an Zapfstelle 2.14 nachprüfen", "Stagnationsproben S0 und S1 nehmen.", RIPLEY, DORN, 2, False),
        (3, "Offene Kollektionen freigeben", "Vier-Augen-Prinzip nach SOP-009.", PICARD, RIPLEY, 1, False),
        (4, "Nitrat-Trend an GWM 12 melden", "Bericht an die Wasserbehörde.", PICARD, PICARD, 5, False),
        (5, "lucentLIMS an lucentAPI anbinden", "Die Mock-Daten durch die API ersetzen.", NOOBINGTON, NOOBINGTON, 30, False),
        (6, "Probenflaschen nachbestellen", "500 mL PE und 250 mL Glas, steril.", DORN, NOOBINGTON, -6, True),
    ]:
        task_id, request, description, assigner, assignee, days, done = row
        conn.execute(insert(task).values(id_task=task_id, request=request, description=description,
                                         id_assigner=assigner, id_assignee=assignee,
                                         deadline=day + timedelta(days=days), done=done, vis=True))
    for message_id, sender, subject, body, read, hours in [
        (1, PICARD, "Freigaberechte für Ellen", "Kannst du Ellen die Rechte für die Freigabe geben?", False, 2),
        (2, RIPLEY, "Nitrat an GWM 12", "Kannst du mir die Zeitreihe exportieren?", False, 5),
        (3, DOE, "IC-3 wieder online", "Die Trennsäule ist getauscht.", True, 72),
    ]:
        conn.execute(insert(message).values(id_message=message_id, sender=sender, reciever=NOOBINGTON,
                                            subject=subject, message=body, read=read,
                                            sent_at=now - timedelta(hours=hours), vis=True))
