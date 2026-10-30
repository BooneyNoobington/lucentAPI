"""The lucentBASE tables, as SQLAlchemy Core metadata.

This mirrors lucentBASE's DDL (github.com/BooneyNoobington/lucentBase, including the branch
harmonize-with-lucentapi: threshold, parameter.value_class, task.done). lucentBASE has the
final say: `checks.check_schema()` compares a live database with these tables at startup, and
the MariaDB tests compare them with lucentBASE's real DDL.

Conventions the API relies on:

- every table `x` has the primary key `id_x` and a visibility flag `vis`; the API shows only
  visible rows and never deletes;
- every table `x` has a log table `l_x`, written by lucentBASE's triggers. The API sets the
  session variables `@current_user_id` and `@change_note` before it writes (see `db.py`).

On SQLite (development and tests), `devdb.py` creates the same tables and imitates the
triggers.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.types import UserDefinedType

metadata = MetaData()
MARIADB = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

COLLECTION_CLASSES = ("spot sample", "mixed sample", "collective sample", "time series", "free measurement")
VALUE_CLASSES = ("decimal", "text", "boolean", "datetime")
ACTION_ROLES = ("auditor", "manager", "worker")
DEPENDENCY_CLASSES = ("strict", "possibility")


class Polygon(UserDefinedType):
    """A spatial polygon on MariaDB, WKT text on SQLite (see `geometry.py`)."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "POLYGON"


def choices(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


def ref(table: str, nullable: bool = True, name: str | None = None) -> Column:
    return Column(name or f"id_{table}", Integer, ForeignKey(f"{table}.id_{table}"), nullable=nullable)


# MariaDB reads `INSERT INTO value (…)` as `INSERT INTO … VALUE (…)`, so this name is always quoted.
QUOTED = {"value"}


def lucent_table(name: str, *items) -> Table:
    """A lucentBASE table and its log table (as lucentBASE's create_all_log_tables() makes it)."""
    table = Table(
        name,
        metadata,
        Column(f"id_{name}", Integer, primary_key=True, autoincrement=True),
        *items,
        Column("vis", Boolean, nullable=False, server_default=text("1")),
        quote=name in QUOTED,
        **MARIADB,
    )
    Table(
        f"l_{name}",
        metadata,
        Column(f"id_l_{name}", Integer, primary_key=True, autoincrement=True),
        Column(f"id_{name}", Integer, ForeignKey(f"{name}.id_{name}"), nullable=False),
        Column("modification", String(50), nullable=False),
        Column("t", DateTime, server_default=text("CURRENT_TIMESTAMP")),
        Column("id_editor", Integer, ForeignKey("person.id_person")),
        Column("note", String(500)),
        Column("checksum", String(100), nullable=False),
        **MARIADB,
    )
    return table


def log_table(table: Table) -> Table:
    return metadata.tables[f"l_{table.name}"]


# SCIENTIFIC.SQL

dimension = lucent_table(
    "dimension",
    Column("name", String(40), nullable=False, unique=True),
    Column("description", String(50)),
)
unit = lucent_table(
    "unit",
    Column("name", String(40), nullable=False, unique=True),
    Column("description", String(100)),
    ref("dimension"),
)
unit_notation = lucent_table(
    "unit_notation",
    ref("unit", nullable=False),
    Column("notation", String(40), unique=True),
)

# SPATIAL.SQL

city = lucent_table("city", Column("name", String(60), nullable=False, unique=True))
district = lucent_table(
    "district",
    Column("name", String(60), nullable=False),
    ref("city", nullable=False),
    Column("zip", String(10), nullable=False),
    UniqueConstraint("name", "id_city", name="one_district_per_city"),
)
street = lucent_table(
    "street",
    Column("name", String(100), nullable=False),
    ref("district", nullable=False),
    UniqueConstraint("name", "id_district", name="one_street_per_district"),
)
house = lucent_table(
    "house",
    Column("name", String(50)),
    Column("number", Integer, nullable=False),
    Column("number_addition", String(5)),
    ref("street", nullable=False),
    UniqueConstraint("number", "number_addition", "id_street", name="house_number_addition_street"),
)
crs = lucent_table(
    "crs",
    Column("epsg", Integer, nullable=False),
    Column("name", String(25), nullable=False, unique=True),
    Column("description", String(150)),
)
site = lucent_table(
    "site",
    Column("name", String(20), nullable=False, unique=True),
    ref("house"),
    Column("borders", Polygon),
    ref("crs"),
    Column("description", String(50)),
    CheckConstraint("id_house IS NOT NULL OR borders IS NOT NULL OR description IS NOT NULL",
                    name="site_needs_location"),
)
spot = lucent_table(
    "spot",
    Column("name", String(50), nullable=False, unique=True),
    Column("x", Double),
    Column("y", Double),
    Column("z", Double),
    Column("z_class", String(20)),
    ref("unit", name="z_id_unit"),
    ref("crs"),
    Column("description", String(50)),
    ref("site"),
    CheckConstraint("description IS NOT NULL OR (x IS NOT NULL AND y IS NOT NULL)", name="spot_needs_location"),
)

# PEOPLE_AND_ORGANIZATIONS.SQL

organization = lucent_table(
    "organization",
    Column("name", String(50), unique=True),
    Column("shorthand", String(15)),
    Column("relation", String(25)),
)
person = lucent_table(
    "person",
    ref("organization"),
    Column("given_name", String(50), nullable=False),
    Column("surname", String(50), nullable=False),
    Column("linux_user", String(50), unique=True),
)
j_person_house = lucent_table("j_person_house", ref("person"), ref("house"), Column("address_class", String(20)))
j_organization_house = lucent_table(
    "j_organization_house", ref("organization"), ref("house"), Column("address_class", String(20))
)
j_person_organization = lucent_table(
    "j_person_organization", ref("person"), ref("organization"), Column("position", String(20))
)

# MISCELLANEOUS.SQL

task = lucent_table(
    "task",
    Column("request", String(50), nullable=False),
    Column("description", String(250)),
    ref("person", nullable=False, name="id_assigner"),
    ref("person", nullable=False, name="id_assignee"),
    Column("deadline", DateTime),
    Column("done", Boolean, nullable=False, server_default=text("0")),
)
message = lucent_table(
    "message",
    ref("person", nullable=False, name="id_sender"),
    ref("person", nullable=False, name="id_receiver"),
    Column("subject", String(250), nullable=False),
    Column("message", String(500), nullable=False),
    Column("new", Boolean, nullable=False, server_default=text("1"), quote=True),
)

# CLASSES.SQL

matrix = lucent_table(
    "matrix",
    Column("name", String(40), nullable=False, unique=True),
    Column("description", String(250)),
)

# DEVICES.SQL

device = lucent_table(
    "device",
    Column("name", String(50), nullable=False),
    Column("number", Integer, nullable=False),
    ref("organization", name="id_manufacturer"),
    ref("organization", name="id_operator"),
    Column("active", Boolean, nullable=False, server_default=text("1")),
    UniqueConstraint("name", "number", name="name_and_number_unique"),
)

# ACTIONS.SQL

link = lucent_table(
    "link",
    Column("name", String(50), nullable=False, unique=True),
    Column("uri", String(500)),
    Column("file_path", String(500)),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
    Column("sha256", String(64)),
)
action = lucent_table(
    "action",
    Column("name", String(50), nullable=False),
    Column("iteration", DateTime, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("description", String(250)),
    UniqueConstraint("name", "iteration", name="action_name_iteration_unique"),
)
j_action_link = lucent_table("j_action_link", ref("action", nullable=False), ref("link", nullable=False))
j_action_action = lucent_table(
    "j_action_action",
    ref("action", nullable=False, name="id_action1"),
    ref("action", nullable=False, name="id_action2"),
    Column("dependency_class", String(12), nullable=False, server_default=text("'strict'")),
    Column("description", String(100)),
    choices("dependency_class", DEPENDENCY_CLASSES, "dependency_class_choices"),
    UniqueConstraint("id_action1", "id_action2", name="id_action1_id_action2_unique"),
)
j_action_person = lucent_table(
    "j_action_person",
    ref("action", nullable=False),
    ref("person", nullable=False),
    Column("role", String(40), nullable=False),
    choices("role", ACTION_ROLES, "role_choices"),
    UniqueConstraint("id_action", "id_person", "role", name="id_action_id_person_role_unique"),
)
j_action_device = lucent_table("j_action_device", ref("action", nullable=False), ref("device", nullable=False))

# COLLECTIONS.SQL

collection = lucent_table(
    "collection",
    Column("name", String(40), unique=True),
    ref("matrix", nullable=False),
    Column("collection_class", String(50), nullable=False),
    ref("spot"),
    ref("site"),
    CheckConstraint("(id_spot IS NULL AND id_site IS NOT NULL) OR (id_spot IS NOT NULL AND id_site IS NULL)",
                    name="spot_or_site"),
    choices("collection_class", COLLECTION_CLASSES, "collection_class_choices"),
)

# PARAMETERS_VALUES_RESULTS.SQL

parameter = lucent_table(
    "parameter",
    Column("name", String(30), nullable=False, unique=True),
    Column("description", String(80)),
    ref("unit"),
    Column("value_class", String(8), nullable=False, server_default=text("'decimal'")),
    choices("value_class", VALUE_CLASSES, "value_class_choices"),
)
effect = lucent_table(
    "effect",
    ref("action", nullable=False),
    ref("parameter", nullable=False),
    Column("uncertainty", Double),
    Column("uncertainty_class", String(9)),
    ref("unit", name="uncertainty_id_unit"),
    Column("calculation", String(40)),
    UniqueConstraint("id_action", "id_parameter", name="id_action_id_parameter_unique"),
)
coefficient = lucent_table(
    "coefficient",
    ref("effect", name="id_effect1"),
    ref("effect", name="id_effect2"),
    Column("description", String(250)),
)
threshold = lucent_table(
    "threshold",
    ref("parameter", nullable=False),
    ref("link", nullable=False),
    Column("lower_limit", Double),
    Column("upper_limit", Double),
    Column("description", String(100)),
    CheckConstraint("lower_limit IS NOT NULL OR upper_limit IS NOT NULL", name="threshold_has_a_limit"),
    CheckConstraint("lower_limit IS NULL OR upper_limit IS NULL OR lower_limit <= upper_limit",
                    name="lower_limit_below_upper_limit"),
    UniqueConstraint("id_parameter", "id_link", name="id_parameter_id_link_unique"),
)
value = lucent_table(
    "value",
    Column("name", String(30)),
    ref("collection", nullable=False),
    ref("effect", nullable=False),
    UniqueConstraint("id_collection", "id_effect", name="collection_effect_unique"),
)
raw_value = lucent_table(
    "raw_value",
    ref("value"),
    Column("t", DateTime),
    ref("person"),
    ref("device"),
    ref("unit"),
    Column("decimal_raw_value", Double),
    Column("text_raw_value", String(50)),
    Column("boolean_raw_value", Boolean),
    Column("datetime_raw_value", DateTime),
    Column("accepted", Boolean, server_default=text("0")),
    CheckConstraint(
        "(CASE WHEN decimal_raw_value IS NULL THEN 0 ELSE 1 END + CASE WHEN text_raw_value IS NULL THEN 0 ELSE 1 END"
        " + CASE WHEN boolean_raw_value IS NULL THEN 0 ELSE 1 END"
        " + CASE WHEN datetime_raw_value IS NULL THEN 0 ELSE 1 END) <= 1",
        name="one_entry_for_value_only"),
    UniqueConstraint("id_value", "t", name="one_value_per_t"),
)

# The tables lucentBASE defines (without log tables), in the order of its CREATE statements.
LUCENT_TABLES = [t for t in metadata.tables.values() if not t.name.startswith("l_")]
