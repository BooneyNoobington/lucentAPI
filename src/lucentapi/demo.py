"""Demo data for development and tests, in lucentBASE's model.

`load(conn, now)` fills a lucentBASE that holds only its initial data (the "System User" with
linux_user grindel, the organization Redhsoft.org, the four roles and grindel as admin, as
INITIAL_DATA.SQL inserts them; on SQLite they're added here). Everything is written as that user, so the log triggers record it.
Collections and raw values are generated with a fixed seed relative to `now`.

Regulations are documents too: an action that refers to the TrinkwV makes the TrinkwV's
thresholds apply to its raw values.

Ellen Ripley takes over quality assurance, and Picard asks for her to get the reviewer role:
granting it is left to whoever tries the people management.
"""

import hashlib
import random
from datetime import datetime, timedelta

from sqlalchemy import Connection, insert, select

from .db import set_editor, utcnow
from .geometry import from_text, polygon_wkt
from .numbering import LUCENT
from .schema import (
    action,
    city,
    coefficient,
    collection,
    crs,
    device,
    dimension,
    district,
    effect,
    house,
    j_action_action,
    j_action_device,
    j_action_link,
    j_action_person,
    j_person_organization,
    j_person_role,
    link,
    matrix,
    message,
    organization,
    parameter,
    person,
    raw_value,
    role,
    site,
    spot,
    street,
    task,
    threshold,
    unit,
    unit_notation,
    value,
)

SYSTEM, DOE, DORN, SIMPSON, PICARD, RIPLEY = 1, 2, 3, 4, 5, 6
LAB = 2
NUMBERING_OFFSET = 3000
SAMPLING_TIME = "Probenahmezeitpunkt"

# As in lucentBASE's INITIAL_DATA.SQL.
INITIAL_ROLES = [
    ("sampler", "Registers collections."),
    ("analyst", "Records raw values."),
    ("reviewer", "Accepts and withdraws raw values, except their own."),
    ("admin", "May do everything, including managing people, roles and devices."),
]
# Who may do what in the demo. The System User is admin from the initial data.
DEMO_ROLES = {DOE: ["analyst"], DORN: ["sampler", "analyst"], SIMPSON: ["sampler", "analyst"],
              PICARD: ["reviewer", "admin"], RIPLEY: ["analyst"]}


def rows(table, columns: str, *values) -> tuple:
    names = columns.split()
    return table, [dict(zip(names, v), vis=True) for v in values]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def reference_data() -> list[tuple]:
    sop = "/srv/lucent/sop/"
    law = "https://www.gesetze-im-internet.de/"
    return [
        rows(organization, "id_organization name shorthand relation",
             (LAB, "Umweltlabor Lucent", "Lucent", "Labor"),
             (3, "Ionic Instruments GmbH", "Ionic", "Hersteller"), (4, "Spectra Analytik AG", "Spectra", "Hersteller"),
             (5, "Klarsicht Messtechnik KG", "Klarsicht", "Hersteller"),
             (6, "Aerosol Systems GmbH", "Aerosol", "Hersteller")),
        rows(person, "id_person given_name surname linux_user id_organization",
             (DOE, "John", "Doe", "jd", LAB), (DORN, "Michael", "Dorn", "worf", LAB),
             (SIMPSON, "Homer", "Simpson", "s7e23", LAB), (PICARD, "Jean Luc", "Picard", "p47at", LAB),
             (RIPLEY, "Ellen", "Ripley", "xx121", LAB)),
        rows(j_person_organization, "id_person id_organization position",
             (SYSTEM, 1, "Administration"), (DOE, LAB, "Analytik"), (DORN, LAB, "Probenahme"),
             (SIMPSON, LAB, "Luftmessnetz"), (PICARD, LAB, "Laborleitung"), (RIPLEY, LAB, "Qualitätssicherung")),
        rows(crs, "id_crs epsg name description", (1, 4326, "WGS 84", "World Geodetic System 1984")),
        rows(city, "id_city name", (1, "Karlsruhe")),
        rows(district, "id_district name id_city zip",
             (1, "Oststadt", 1, "76131"), (2, "Durlach", 1, "76227"), (3, "Knielingen", 1, "76187")),
        rows(street, "id_street name id_district", (1, "Laborstraße", 1), (2, "Am Wasserwerk", 2),
             (3, "Rheinauenweg", 3)),
        rows(house, "id_house number number_addition id_street", (1, 4, None, 1), (2, 1, "a", 2), (3, 12, None, 3)),
        rows(site, "id_site name description id_house id_crs borders",
             (1, "Laborstraße", "Labor- und Verwaltungsgebäude", 1, 1,
              [(8.4102, 49.0103), (8.4139, 49.0106), (8.4145, 49.0088), (8.4108, 49.0084)]),
             (2, "Wasserwerk Ost", "Fassungsgelände, Brunnen B3 und B7", 2, 1,
              [(8.4510, 48.9990), (8.4600, 48.9995), (8.4612, 48.9950), (8.4515, 48.9944)]),
             (3, "Messfeld Rheinaue", "Grundwassermessstellen am Altrhein", 3, 1,
              [(8.3010, 49.0335), (8.3095, 49.0340), (8.3105, 49.0290), (8.3020, 49.0282)])),
        rows(dimension, "id_dimension name description",
             (1, "Temperatur", "Thermodynamische Temperatur"), (2, "Elektrische Leitfähigkeit", "Leitwert pro Länge"),
             (3, "Massenkonzentration", "Masse pro Volumen"),
             (4, "Anzahlkonzentration", "Anzahl pro Volumen, z. B. KBE")),
        rows(unit, "id_unit name id_dimension",
             (1, "Grad Celsius", 1), (2, "Mikrosiemens pro Zentimeter", 2), (3, "Milligramm pro Liter", 3),
             (4, "Mikrogramm pro Liter", 3), (5, "Mikrogramm pro Kubikmeter", 3), (6, "KBE pro 100 Milliliter", 4)),
        rows(unit_notation, "id_unit notation",
             (1, "°C"), (2, "µS/cm"), (2, "µS cm⁻¹"), (3, "mg/L"), (3, "mg l⁻¹"), (3, "ppm"), (4, "µg/L"),
             (4, "µg l⁻¹"), (4, "ppb"), (5, "µg/m³"), (5, "µg m⁻³"), (6, "KBE/100 mL"), (6, "CFU/100 mL")),
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
             (1, "Luft", "Außen- oder Innenluft"), (2, "Leitungswasser", "Aus der Hausinstallation"),
             (3, "Trinkwasser", "Gemäß Trinkwasserverordnung"), (4, "Grundwasser", "Aus Brunnen und Messstellen")),
        rows(parameter, "id_parameter name description id_unit value_class",
             (1, SAMPLING_TIME, "Wann die Probe genommen wurde", None, "datetime"),
             (2, "Temperatur", "Wassertemperatur bei der Probenahme", 1, "decimal"),
             (3, "Färbung", "Visuelle Prüfung", None, "text"),
             (4, "Geruch auffällig", "Olfaktorische Prüfung", None, "boolean"),
             (5, "pH-Wert", "Negativer dekadischer Logarithmus der Oxoniumaktivität", None, "decimal"),
             (6, "Elektrische Leitfähigkeit", "Bezogen auf 25 °C", 2, "decimal"),
             (7, "Nitrat", "NO₃⁻", 3, "decimal"), (8, "Chlorid", "Cl⁻", 3, "decimal"),
             (9, "Sulfat", "SO₄²⁻", 3, "decimal"), (10, "Blei", "Pb, gesamt", 4, "decimal"),
             (11, "Kupfer", "Cu, gesamt", 3, "decimal"), (12, "Nickel", "Ni, gesamt", 4, "decimal"),
             (13, "Escherichia coli", "Fäkalindikator", 6, "decimal"),
             (14, "Coliforme Bakterien", "Hygieneindikator", 6, "decimal"),
             (15, "Feinstaub PM10", "Partikel ≤ 10 µm, Tagesmittel", 5, "decimal"),
             (16, "Feinstaub PM2,5", "Partikel ≤ 2,5 µm, Tagesmittel", 5, "decimal"),
             (17, "Stickstoffdioxid", "NO₂, Tagesmittel", 5, "decimal")),
        rows(link, "id_link name file_path uri accepted sha256",
             *[(i, n, p, u, a, sha256(p or u)) for i, n, p, u, a in [
                 (1, "SOP-001 Probenahme Wasser", sop + "SOP-001_r3.pdf", None, True),
                 (2, "SOP-002 pH-Wert", sop + "SOP-002_r2.pdf", None, True),
                 (3, "SOP-003 Leitfähigkeit", sop + "SOP-003_r1.pdf", None, True),
                 (4, "SOP-004 Anionen (IC)", sop + "SOP-004_r4.pdf", None, True),
                 (5, "SOP-005 Metalle (ICP-MS)", sop + "SOP-005_r2.pdf", None, True),
                 (6, "SOP-005 Entwurf Rev. 3", sop + "entwurf/SOP-005_r3.docx", None, False),
                 (7, "SOP-006 Mikrobiologie", sop + "SOP-006_r1.pdf", None, True),
                 (8, "SOP-007 Feinstaub", sop + "SOP-007_r2.pdf", None, True),
                 (9, "SOP-008 Stickstoffdioxid", sop + "SOP-008_r1.pdf", None, True),
                 (10, "SOP-009 Datenfreigabe", sop + "SOP-009_r1.pdf", None, True),
                 (11, "Gerätehandbuch IC-3", None, "https://docs.example.org/ic3/handbuch.pdf", True),
                 (12, "Trinkwasserverordnung (TrinkwV)", None, law + "trinkwv_2023/", True),
                 (13, "Grundwasserverordnung (GrwV)", None, law + "grwv_2010/", True),
                 (14, "39. BImSchV (Luftqualität)", None, law + "bimschv_39/", True),
             ]]),
        rows(threshold, "id_parameter id_link lower_limit upper_limit description",
             (4, 12, None, 0, "Ohne anormale Veränderung"), (5, 12, 6.5, 9.5, "Anlage 3"), (6, 12, None, 2790, "Anlage 3"),
             (7, 12, None, 50, "Anlage 2 Teil I"), (8, 12, None, 250, "Anlage 3"), (9, 12, None, 250, "Anlage 3"),
             (10, 12, None, 10, "Anlage 2 Teil II"), (11, 12, None, 2, "Anlage 2 Teil II"),
             (12, 12, None, 20, "Anlage 2 Teil II"), (13, 12, None, 0, "Anlage 1"), (14, 12, None, 0, "Anlage 3"),
             (7, 13, None, 50, "Schwellenwert Anlage 2"), (8, 13, None, 250, "Schwellenwert Anlage 2"),
             (9, 13, None, 250, "Schwellenwert Anlage 2"), (10, 13, None, 10, "Schwellenwert Anlage 2"),
             (15, 14, None, 50, "Tagesgrenzwert"), (16, 14, None, 25, "Jahresgrenzwert"),
             (17, 14, None, 40, "Jahresgrenzwert")),
        rows(action, "id_action name iteration active description",
             (1, "Probenahme Wasser", at("2024-03-01"), True, "Entnahme nach DIN ISO 5667-5, Temperatur vor Ort."),
             (2, "Sensorische Prüfung", at("2023-06-01"), True, "Färbung und Geruch nach DIN EN 1622."),
             (3, "pH-Wert", at("2024-09-15"), True, "DIN EN ISO 10523, Glaselektrode, Drei-Punkt-Kalibrierung."),
             (4, "Elektrische Leitfähigkeit", at("2023-01-10"), True, "DIN EN 27888, kompensiert auf 25 °C."),
             (5, "Anionen (IC)", at("2025-02-01"), True, "Nitrat, Chlorid und Sulfat nach DIN EN ISO 10304-1."),
             (6, "Metalle (ICP-MS)", at("2024-11-20"), True,
              "DIN EN ISO 17294-2, Stagnationsprobe S0. Überschreitungen werden wiederholt."),
             (7, "E. coli und coliforme Bakterien", at("2023-05-05"), True,
              "Membranfiltration nach DIN EN ISO 9308-1, 21 h bei 36 °C."),
             (8, "Feinstaub (gravimetrisch)", at("2024-04-01"), True, "DIN EN 12341, 24-Stunden-Probenahme auf Filter."),
             (9, "Stickstoffdioxid", at("2023-09-01"), True, "Chemilumineszenz nach DIN EN 14211, Tagesmittel."),
             (10, "pH-Wert", at("2021-02-01"), False, "Vorgängerversion mit Zwei-Punkt-Kalibrierung.")),
        rows(j_action_link, "id_action id_link",
             (1, 1), (1, 12), (2, 1), (2, 12), (3, 2), (3, 12), (4, 3), (4, 12), (5, 4), (5, 11), (5, 12), (5, 13),
             (6, 5), (6, 6), (6, 12), (6, 13), (7, 7), (7, 12), (8, 8), (8, 14), (9, 9), (9, 14), (10, 2)),
        rows(j_action_action, "id_action1 id_action2 dependency_class description",
             *[(a, 1, "strict", "Erst nach der Probenahme") for a in (2, 3, 4, 5, 6, 7)],
             (5, 4, "possibility", "Ionenbilanz mit der Leitfähigkeit plausibilisieren")),
        rows(j_action_person, "id_action id_person role",
             (1, DORN, "worker"), (1, PICARD, "manager"), (2, DORN, "worker"), (3, DOE, "worker"),
             (3, PICARD, "manager"), (3, RIPLEY, "auditor"), (4, DOE, "worker"), (5, DOE, "worker"),
             (5, PICARD, "manager"), (6, PICARD, "worker"), (6, RIPLEY, "auditor"), (7, RIPLEY, "worker"),
             (8, SIMPSON, "worker"), (8, DOE, "worker"), (9, SIMPSON, "worker"), (10, DOE, "worker")),
        rows(device, "id_device name number id_manufacturer id_operator active",
             (1, "pH-Meter Lab 1", 2207114, 3, LAB, True), (2, "pH-Meter Lab 2", 1903027, 3, LAB, False),
             (3, "Leitfähigkeitsmessgerät LF 4", 2105033, 3, LAB, True), (4, "Ionenchromatograph IC-3", 98, 4, LAB, True),
             (5, "ICP-MS Q2", 4411, 4, LAB, True), (6, "Brutschrank 36 °C", 2, 5, LAB, True),
             (7, "Feinstaubsammler LVS-3", 1187, 6, LAB, True), (8, "Feinstaubsammler LVS-3", 1192, 6, LAB, True),
             (9, "NO₂-Analysator", 500221, 6, LAB, True), (10, "Feinwaage XS205", 205778, 5, LAB, True),
             (11, "Einstichthermometer", 42, 5, LAB, True)),
        rows(j_action_device, "id_action id_device",
             (1, 11), (3, 1), (3, 2), (4, 3), (5, 4), (6, 5), (7, 6), (8, 7), (8, 8), (8, 10), (9, 9), (10, 2)),
        rows(effect, "id_effect id_action id_parameter uncertainty uncertainty_class calculation",
             (1, 1, 1, None, None, None), (2, 1, 2, 0.2, "absolute", None), (3, 2, 3, None, None, None),
             (4, 2, 4, None, None, None), (5, 3, 5, 0.05, "absolute", None),
             (6, 4, 6, 2, "relative", "κ25 = κT · f25(T)"), (7, 5, 7, 5, "relative", None),
             (8, 5, 8, 5, "relative", None), (9, 5, 9, 5, "relative", None), (10, 6, 10, 10, "relative", None),
             (11, 6, 11, 8, "relative", None), (12, 6, 12, 10, "relative", None), (13, 7, 13, None, None, None),
             (14, 7, 14, None, None, None), (15, 8, 1, None, None, None),
             (16, 8, 15, 7, "relative", "c = (m2 − m1) / V"), (17, 8, 16, 7, "relative", "c = (m2 − m1) / V"),
             (18, 9, 17, 7.5, "relative", None), (19, 10, 5, 0.1, "absolute", None)),
        rows(coefficient, "id_effect1 id_effect2 description",
             (6, 2, "Die Leitfähigkeit wird auf die Probentemperatur bezogen")),
    ]


# Who samples what where, and which actions that plans:
# (spot, site, matrix, class, actions, every n days, first offset, sampler)
WATER_ROUTINE, WATER_FULL, GROUND_WATER, AIR = [1, 2, 3, 4, 7], [1, 2, 3, 4, 5, 6, 7], [1, 3, 4, 5], [8, 9]
SCHEDULES = [
    (3, None, 1, "time series", AIR, 7, 1, SIMPSON), (11, None, 1, "time series", AIR, 7, 2, SIMPSON),
    (4, None, 2, "spot sample", WATER_FULL, 14, 3, DORN), (6, None, 3, "spot sample", WATER_ROUTINE, 7, 0, DORN),
    (8, None, 4, "spot sample", GROUND_WATER, 14, 2, DORN), (10, None, 4, "spot sample", GROUND_WATER, 7, 3, RIPLEY),
    (None, 3, 4, "mixed sample", GROUND_WATER, 28, 12, RIPLEY),
]
WORKERS = {1: DORN, 2: DORN, 3: DOE, 4: DOE, 5: DOE, 6: PICARD, 7: RIPLEY, 8: DOE, 9: SIMPSON}
DEVICES = {1: 11, 3: 1, 4: 3, 5: 4, 6: 5, 7: 6, 9: 9}


def mock_reading(parameter_id: int, spot_id: int | None, progress: float, rng: random.Random):
    """(column, content) for a plausible reading; progress runs from 0 (oldest) to 1 (now)."""
    gauss = rng.gauss
    ground = spot_id in (8, 10, None)
    match parameter_id:
        case 2: return "decimal", round(gauss(11.5 if ground else 14, 1), 1)
        case 3: return "text", "farblos" if rng.random() < 0.93 else "schwach gelblich"
        case 4: return "boolean", rng.random() < 0.04
        case 5: return "decimal", round(gauss(7.15 if ground else 7.6, 0.08), 2)
        case 6: return "decimal", round(gauss(880 if spot_id == 10 else 650, 30))
        case 7: return "decimal", round(38 + 18 * progress + gauss(0, 2) if spot_id == 10 else gauss(18, 2), 1)
        case 8: return "decimal", round(gauss(45, 4), 1)
        case 9: return "decimal", round(gauss(62, 5), 1)
        case 10: return "decimal", round(max(0.1, gauss(12.5, 1) if progress > 0.8 else gauss(7.5, 2.8)), 1)
        case 11: return "decimal", round(max(0.01, gauss(0.45, 0.12)), 2)
        case 12: return "decimal", round(max(0.1, gauss(2.5, 0.8)), 1)
        case 13 | 14: return "decimal", float(rng.choice([0] * 30 + [1, 2]))
        case 15: return "decimal", float(round(max(3, gauss(31 if spot_id == 11 else 19, 9))))
        case 16: return "decimal", float(round(max(2, gauss(19 if spot_id == 11 else 12, 5))))
        case _: return "decimal", float(round(max(3, gauss(36 if spot_id == 11 else 18, 6))))


def ensure_initial_data(conn: Connection) -> None:
    """What lucentBASE's INITIAL_DATA.SQL inserts, if it isn't there (as on SQLite)."""
    if conn.scalar(select(person.c.id_person).where(person.c.linux_user == "grindel")) is None:
        conn.execute(insert(organization).values(id_organization=1, name="Redhsoft.org", shorthand="redhsoft", vis=True))
        conn.execute(insert(person).values(id_person=SYSTEM, given_name="System", surname="User",
                                           linux_user="grindel", id_organization=1, vis=True))
        for name, description in INITIAL_ROLES:
            conn.execute(insert(role).values(name=name, description=description, vis=True))
        admin = conn.scalar(select(role.c.id_role).where(role.c.name == "admin"))
        conn.execute(insert(j_person_role).values(id_person=SYSTEM, id_role=admin, vis=True))


def load(conn: Connection, now: datetime | None = None, weeks: int = 8) -> None:
    """Fills lucentBASE (holding only its initial data) with demo data, as the System User."""
    now = now or utcnow()
    ensure_initial_data(conn)
    set_editor(conn, SYSTEM, "Demo data")
    data = reference_data()
    for table, items in data:
        for row in items:
            if table is site:
                row["borders"] = from_text(polygon_wkt(row["borders"]))
            conn.execute(insert(table).values(row))
    role_ids = dict(conn.execute(select(role.c.name, role.c.id_role)).all())
    for person_id, names in DEMO_ROLES.items():
        for name in names:
            conn.execute(insert(j_person_role).values(id_person=person_id, id_role=role_ids[name], vis=True))

    effects = next(items for table, items in data if table is effect)
    rng = random.Random(1997)
    horizon = weeks * 7
    drafts = []
    for spot_id, site_id, matrix_id, collection_class, actions, every, offset, sampler in SCHEDULES:
        for days_ago in range(offset, horizon + 1, every):
            sampled_at = (now - timedelta(days=days_ago)).replace(hour=rng.randint(7, 10), minute=rng.randint(0, 59),
                                                                  second=0, microsecond=0)
            if sampled_at <= now:
                drafts.append((sampled_at, spot_id, site_id, matrix_id, collection_class, tuple(actions), sampler))
    drafts.sort()

    for collection_id, draft in enumerate(drafts, start=1):
        sampled_at, spot_id, site_id, matrix_id, collection_class, actions, sampler = draft
        conn.execute(insert(collection).values(
            id_collection=collection_id, name=LUCENT.identifier(collection_id + NUMBERING_OFFSET),
            id_matrix=matrix_id, collection_class=collection_class, id_spot=spot_id, id_site=site_id, vis=True))
        values = {}
        for row in effects:
            if row["id_action"] in actions:
                values[row["id_effect"]] = conn.execute(insert(value).values(
                    id_collection=collection_id, id_effect=row["id_effect"], vis=True)).inserted_primary_key[0]

        age = (now - sampled_at).total_seconds() / 86_400
        progress = 1 - age / horizon
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
            for row in effects:
                if row["id_action"] != action_id:
                    continue
                if row["id_parameter"] == 1:  # the sampling time, recorded by whoever sampled
                    readings, column, worker, t = [sampled_at], "datetime", sampler, sampled_at
                else:
                    column, content = mock_reading(row["id_parameter"], spot_id, progress, rng)
                    readings, worker, t = [content], WORKERS[action_id], measured_at
                    if row["id_parameter"] == 10 and content > 10:  # lead above the limit is measured again
                        readings.append(round(content * rng.gauss(1, 0.04), 1))
                unit_id = next(p["id_unit"] for p in next(i for tb, i in data if tb is parameter)
                               if p["id_parameter"] == row["id_parameter"])
                device_id = (8 if spot_id == 11 else 7) if action_id == 8 else DEVICES.get(action_id)
                for repetition, content in enumerate(readings):
                    conn.execute(insert(raw_value).values(
                        id_value=values[row["id_effect"]], t=t + timedelta(hours=3 * repetition), id_person=worker,
                        id_device=device_id, id_unit=unit_id, accepted=index < accepted, vis=True,
                        **{f"{column}_raw_value": content}))

    day = now.replace(hour=16, minute=0, second=0, microsecond=0)
    for task_id, request, description, assigner, assignee, days, done in [
        (1, "pH-Meter Lab 1 kalibrieren", "Drei-Punkt-Kalibrierung mit Puffer 4, 7 und 10.", PICARD, DOE, -1, False),
        (2, "Blei an Zapfstelle 2.14 nachprüfen", "Stagnationsproben S0 und S1 nehmen.", RIPLEY, DORN, 2, False),
        (3, "Offene Kollektionen freigeben", "Vier-Augen-Prinzip nach SOP-009.", PICARD, RIPLEY, 1, False),
        (4, "Nitrat-Trend an GWM 12 melden", "Bericht an die Wasserbehörde.", PICARD, PICARD, 5, False),
        (5, "lucentLIMS an lucentAPI anbinden", "Die Mock-Daten durch die API ersetzen.", SYSTEM, SYSTEM, 30, False),
        (6, "Probenflaschen nachbestellen", "500 mL PE und 250 mL Glas, steril.", DORN, SYSTEM, -6, True),
    ]:
        conn.execute(insert(task).values(id_task=task_id, request=request, description=description,
                                         id_assigner=assigner, id_assignee=assignee,
                                         deadline=day + timedelta(days=days), done=done, vis=True))
    for message_id, sender, subject, body, new in [
        (1, PICARD, "Freigaberechte für Ellen", "Kannst du Ellen die Rechte für die Freigabe geben?", True),
        (2, RIPLEY, "Nitrat an GWM 12", "Kannst du mir die Zeitreihe exportieren?", True),
        (3, SIMPSON, "Messcontainer", "Filterwechsel ist erledigt. Die Donuts im Pausenraum leider auch.", False),
    ]:
        conn.execute(insert(message).values(id_message=message_id, id_sender=sender, id_receiver=SYSTEM,
                                            subject=subject, message=body, new=new, vis=True))
    set_editor(conn, None, None)
