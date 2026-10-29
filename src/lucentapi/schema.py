"""The lucentBASE tables, as SQLAlchemy Core metadata.

Reconstructed from the column lists of lucentAndShiny's demo data (`share/demo_data/*.ods`):
names are exact, types are a best guess. Compare with your lucentBASE DDL before you point the
API at a real database.

Conventions of lucentBASE that the API relies on:

- every table `x` has the primary key `id_x` and a visibility flag `vis` (1 = visible,
  0 = deleted); the API only shows visible rows and never deletes;
- every table `x` has a log table `l_x` (`id_l_x`, `id_x`, `change`, `t`, `id_editor`,
  `note`, `checksum`). Every write of the API adds a log row (see `audit.py`).

Columns the API needs but lucentBASE doesn't have yet are marked with `addition()`; they are
added by `sql/lucentbase-additions.sql`.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.types import UserDefinedType

metadata = MetaData()
MARIADB = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

ADDITION = "lucentapi_addition"


def addition(column: Column) -> Column:
    """A column that `sql/lucentbase-additions.sql` adds to lucentBASE."""
    column.info[ADDITION] = True
    return column


class Polygon(UserDefinedType):
    """A spatial polygon on MariaDB, WKT text elsewhere (see `geometry.py`)."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "POLYGON"


def ref(table: str, nullable: bool = True, name: str | None = None) -> Column:
    return Column(name or f"id_{table}", Integer, ForeignKey(f"{table}.id_{table}"), nullable=nullable)


def lucent_table(name: str, *columns: Column, vis: bool = True, log: bool = True) -> Table:
    table = Table(
        name,
        metadata,
        Column(f"id_{name}", Integer, primary_key=True, autoincrement=True),
        *columns,
        *([Column("vis", Boolean, nullable=False, server_default=text("1"))] if vis else []),
        **MARIADB,
    )
    if log:
        Table(
            f"l_{name}",
            metadata,
            Column(f"id_l_{name}", Integer, primary_key=True, autoincrement=True),
            Column(f"id_{name}", Integer, nullable=False, index=True),
            Column("change", Text, nullable=False),
            Column("t", DateTime, nullable=False),
            Column("id_editor", Integer, ForeignKey("person.id_person"), nullable=False),
            Column("note", Text),
            Column("checksum", String(64), nullable=False),
            **MARIADB,
        )
    return table


def log_table(table: Table) -> Table:
    return metadata.tables[f"l_{table.name}"]


# Addresses

city = lucent_table("city", Column("name", String(255), nullable=False))
district = lucent_table(
    "district", Column("name", String(255), nullable=False), ref("city"), Column("zip", String(16))
)
street = lucent_table("street", Column("name", String(255), nullable=False), ref("district"))
house = lucent_table(
    "house",
    Column("name", String(255)),
    Column("number", String(16)),
    Column("number_addition", String(16)),
    ref("street"),
)

# People and organizations

organization = lucent_table(
    "organization",
    Column("name", String(255), nullable=False),
    Column("shorthand", String(64)),
    Column("connection", Text),
)
person = lucent_table(
    "person",
    ref("organization"),
    Column("given_name", String(255)),
    Column("surname", String(255), nullable=False),
    Column("unix_account", String(64), unique=True),
)
j_person_organization = lucent_table(
    "j_person_organization", ref("person", False), ref("organization", False), Column("position", String(255))
)
j_person_house = lucent_table(
    "j_person_house", ref("person", False), ref("house", False), Column("address_class", String(64))
)
j_organization_house = lucent_table(
    "j_organization_house", ref("organization", False), ref("house", False), Column("address_class", String(64))
)

# Locations

crs = lucent_table(
    "crs", Column("epsg", Integer), Column("name", String(255), nullable=False), Column("description", Text)
)
site = lucent_table(
    "site",
    Column("name", String(255), nullable=False),
    ref("house"),
    Column("borders", Polygon),
    ref("crs"),
    Column("description", Text),
)
spot = lucent_table(
    "spot",
    Column("name", String(255), nullable=False),
    Column("x", Double),
    Column("y", Double),
    Column("z", Double),
    Column("z_class", String(64)),
    Column("z_id_unit", Integer, ForeignKey("unit.id_unit")),
    ref("crs"),
    Column("description", Text),
    ref("site"),
)

# Units and variables

dimension = lucent_table("dimension", Column("name", String(255), nullable=False), Column("description", Text))
unit = lucent_table(
    "unit", Column("name", String(255), nullable=False), Column("description", Text), ref("dimension")
)
unit_notation = lucent_table("unit_notation", ref("unit", False), Column("notation", String(64), nullable=False))
constant = lucent_table(
    "constant", Column("name", String(255), nullable=False), Column("value", Double), ref("unit")
)
variable = lucent_table(
    "variable",
    Column("name", String(255), nullable=False),
    Column("value_class", String(16), nullable=False),  # decimal, text, boolean, datetime
    Column("description", Text),
    ref("dimension"),
)
j_variable_variable = lucent_table(
    "j_variable_variable",
    ref("variable", False, "id_variable1"),
    ref("variable", False, "id_variable2"),
    Column("description", Text),
)

# Collections

matrix = lucent_table("matrix", Column("name", String(255), nullable=False), Column("description", Text))
collection_class = lucent_table(
    "collection_class", Column("name", String(255), nullable=False), Column("description", Text)
)
collection = lucent_table(
    "collection",
    Column("name", String(64), nullable=False, unique=True),
    ref("matrix"),
    ref("collection_class"),
    ref("spot"),
    ref("site"),
    addition(Column("sampled_at", DateTime)),
    addition(Column("id_sampler", Integer, ForeignKey("person.id_person"))),
)

# Devices, documents, SOPs

device = lucent_table(
    "device",
    Column("name", String(255), nullable=False),
    Column("number", String(64)),
    ref("organization", name="id_manufacturer"),
    ref("person", name="id_operator"),
    Column("active", Boolean, nullable=False, server_default=text("1")),
)
link = lucent_table(
    "link",
    Column("name", String(255), nullable=False),
    Column("url", Text),
    Column("file_path", Text),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
    Column("SHA256", String(64)),
)
sop = lucent_table(
    "sop", Column("name", String(255), nullable=False), Column("active", Boolean, nullable=False, server_default=text("1"))
)
j_sop_link = lucent_table("j_sop_link", ref("sop", False), ref("link", False))
# The demo data has no log table for j_sop_person.
j_sop_person = lucent_table("j_sop_person", ref("sop", False), ref("person", False), Column("role", String(64)), log=False)

# Actions (methods) and what they measure

action = lucent_table(
    "action",
    Column("name", String(255), nullable=False),
    Column("iteration", Integer),
    Column("active", Boolean, nullable=False, server_default=text("1")),
    Column("description", Text),
    ref("sop"),
    addition(Column("standard", String(255))),
)
j_action_collection_class = lucent_table(
    "j_action_collection_class", ref("action", False), ref("collection_class", False)
)
j_action_dependency = lucent_table(
    "j_action_dependency",
    ref("action", False, "id_dependant"),
    ref("action", False, "id_dependency"),
    Column("dependency_class", String(64)),
    Column("description", Text),
)
j_action_implication = lucent_table(
    "j_action_implication", ref("action", False, "id_implier"), ref("action", False, "id_implication")
)
j_action_device = lucent_table("j_action_device", ref("action", False), ref("device", False))
j_action_person = lucent_table(
    "j_action_person", ref("action", False), ref("person", False), Column("role", String(64))
)
j_action_variable = lucent_table(
    "j_action_variable",
    ref("action", False),
    ref("variable", False),
    Column("uncertainty", Double),
    Column("uncertainty_class", String(16)),  # absolute, relative
    ref("unit", name="uncertainty_id_unit"),
    Column("calculation", Text),
    addition(ref("unit")),
    addition(Column("decimals", SmallInteger)),
    addition(Column("lower_limit", Double)),
    addition(Column("upper_limit", Double)),
)

# Results

value = lucent_table(
    "value",
    ref("collection", False),
    ref("j_action_variable", False),
    Column("repetition", Integer, nullable=False, server_default=text("1")),
    Column("repetition_class", String(64)),
    Column("decimal_value", Double),
    Column("text_value", Text),
    Column("boolean_value", Boolean),
    Column("datetime_value", DateTime),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
)
raw_value = lucent_table(
    "raw_value",
    ref("value", False),
    Column("t", DateTime, nullable=False),
    ref("person"),
    ref("device"),
    Column("decimal_raw_value", Double),
    Column("text_raw_value", Text),
    Column("boolean_raw_value", Boolean),
    Column("datetime_raw_value", DateTime),
    Column("accepted", Boolean, nullable=False, server_default=text("0")),
)
j_value_value = lucent_table("j_value_value", ref("value", False, "id_value1"), ref("value", False, "id_value2"), vis=False)

# Management

task = lucent_table(
    "task",
    Column("request", String(255), nullable=False),
    Column("description", Text),
    ref("person", name="id_assigner"),
    ref("person", name="id_assignee"),
    Column("deadline", DateTime),
    addition(Column("done", Boolean, nullable=False, server_default=text("0"))),
)
message = lucent_table(
    "message",
    ref("person", name="sender"),
    ref("person", name="reciever"),  # sic, as in lucentBASE
    Column("subject", String(255)),
    Column("message", Text),
    Column("read", Boolean, nullable=False, server_default=text("0")),
    addition(Column("sent_at", DateTime)),
)
