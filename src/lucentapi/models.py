"""Request and response bodies. Requests reject unknown fields. Names follow lucentBASE."""

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["registered", "in_analysis", "in_review", "approved"]
Role = Literal["sampler", "analyst", "reviewer", "admin"]
ValueClass = Literal["decimal", "text", "boolean", "datetime"]
CollectionClass = Literal["spot sample", "mixed sample", "collective sample", "time series", "free measurement"]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Note(Request):
    note: str | None = Field(None, max_length=500, description="Why, for lucentBASE's log (l_<table>.note).")


# People and organizations

class Organization(BaseModel):
    id: int
    name: str | None
    shorthand: str | None
    relation: str | None


class Person(BaseModel):
    id: int
    given_name: str
    surname: str
    linux_user: str | None = Field(description="The account the person signs in with (the token's username).")
    organization_id: int | None
    position: str | None = Field(description="In their organization, from j_person_organization.")
    active: bool = Field(description="Whether the person can sign in. Inactive people stay in the history.")
    roles: list[Role] = Field(description="What the person may do, from j_person_role.")
    etag: str = Field(description="Send it as If-Match when changing the person or their roles.")


class Me(BaseModel):
    person: Person
    roles: list[Role]


class RoleInfo(BaseModel):
    name: Role
    description: str | None


class PersonIn(Note):
    given_name: str = Field(min_length=1, max_length=50)
    surname: str = Field(min_length=1, max_length=50)
    linux_user: str | None = Field(None, min_length=1, max_length=50, description="Must be unique.")
    organization_id: int | None = None
    position: str | None = Field(None, max_length=20, description="Needs an organization.")
    roles: list[Role] = []


class PersonPatch(Note):
    """Fields left out stay as they are."""

    given_name: str | None = Field(None, min_length=1, max_length=50)
    surname: str | None = Field(None, min_length=1, max_length=50)
    linux_user: str | None = Field(None, min_length=1, max_length=50)
    organization_id: int | None = None
    position: str | None = Field(None, max_length=20)
    active: bool | None = None


class RolesIn(Note):
    roles: list[Role] = Field(description="All roles the person should have; the others are revoked.")


class HistoryEntry(BaseModel):
    t: dt.datetime | None
    editor_id: int | None
    modification: Literal["created", "changed"]
    subject: Literal["person", "role", "organization"] = Field(
        description="The person row, one of their roles (j_person_role) or their organization (j_person_organization).")
    role: Role | None = None
    note: str | None = Field(description="What changed and why, as the API wrote it.")


# Locations

class GeoPoint(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Address(BaseModel):
    street: str | None
    house_number: int | None
    addition: str | None
    zip: str | None
    district: str | None
    city: str | None


class ReferenceSystem(BaseModel):
    id: int
    epsg: int
    name: str
    description: str | None


class Site(BaseModel):
    id: int
    name: str
    description: str | None
    address: Address | None
    border: list[GeoPoint]
    crs_id: int | None
    etag: str = Field(description="Send it as If-Match when changing the site.")


class Spot(BaseModel):
    id: int
    name: str
    description: str | None
    x: float | None = Field(description="Latitude, as in lucentBASE.")
    y: float | None = Field(description="Longitude, as in lucentBASE.")
    z: float | None
    crs_id: int | None
    site_id: int | None
    etag: str = Field(description="Send it as If-Match when changing the spot.")


class SpotIn(Note):
    name: str = Field(min_length=1, max_length=50, description="Unique among spots.")
    description: str | None = Field(None, max_length=50)
    x: float = Field(ge=-90, le=90, description="Latitude, as in lucentBASE.")
    y: float = Field(ge=-180, le=180, description="Longitude, as in lucentBASE.")
    site_id: int | None = Field(None, description="The site the spot lies in, if any.")
    crs_id: int | None = Field(None, description="Defaults to WGS 84 (EPSG:4326) if lucentBASE has it.")


class SpotPatch(Note):
    """Fields left out stay as they are. An empty description or a `site_id` of null removes it."""

    name: str | None = Field(None, min_length=1, max_length=50)
    description: str | None = Field(None, max_length=50)
    x: float | None = Field(None, ge=-90, le=90)
    y: float | None = Field(None, ge=-180, le=180)
    site_id: int | None = None


class SiteIn(Note):
    name: str = Field(min_length=1, max_length=20, description="Unique among sites.")
    description: str | None = Field(None, max_length=50)
    border: list[GeoPoint] = Field(min_length=3, max_length=500,
                                   description="The corners in order, without repeating the first.")
    crs_id: int | None = Field(None, description="Defaults to WGS 84 (EPSG:4326) if lucentBASE has it.")


class SitePatch(Note):
    """Fields left out stay as they are. An empty description removes it."""

    name: str | None = Field(None, min_length=1, max_length=20)
    description: str | None = Field(None, max_length=50)
    border: list[GeoPoint] | None = Field(None, min_length=3, max_length=500,
                                          description="All corners in order, replacing the old border.")


# Catalog

class Matrix(BaseModel):
    id: int
    name: str
    description: str | None


class Dimension(BaseModel):
    id: int
    name: str
    description: str | None


class Unit(BaseModel):
    id: int
    name: str
    description: str | None
    notations: list[str]
    dimension_id: int | None


class Parameter(BaseModel):
    id: int
    name: str
    description: str | None
    unit_id: int | None
    value_class: ValueClass


class Device(BaseModel):
    id: int
    name: str
    number: int
    manufacturer_id: int | None = Field(description="An organization.")
    operator_id: int | None = Field(description="An organization, as in lucentBASE.")
    active: bool
    etag: str


class DevicePatch(Note):
    active: bool


class Document(BaseModel):
    """A link: an SOP, a standard, a regulation, any file or web page."""

    id: int
    name: str
    uri: str | None
    file_path: str | None
    accepted: bool
    sha256: str | None
    action_ids: list[int] = Field(description="Actions that refer to it (j_action_link).")


class Effect(BaseModel):
    """What an action yields: one parameter, with method-specific uncertainty."""

    id: int
    action_id: int
    parameter_id: int
    uncertainty: float | None
    uncertainty_class: str | None
    uncertainty_unit_id: int | None
    calculation: str | None


class ActionPerson(BaseModel):
    person_id: int
    role: Literal["auditor", "manager", "worker"]


class Dependency(BaseModel):
    """This action depends on `action_id` (j_action_action: id_action1 depends on id_action2)."""

    action_id: int
    dependency_class: Literal["strict", "possibility"]
    description: str | None


class Action(BaseModel):
    id: int
    name: str
    iteration: dt.datetime = Field(description="The version of the action, as a point in time.")
    active: bool
    description: str | None
    document_ids: list[int]
    device_ids: list[int]
    people: list[ActionPerson]
    dependencies: list[Dependency]
    effects: list[Effect]


class Coefficient(BaseModel):
    id: int
    effect1_id: int | None
    effect2_id: int | None
    description: str | None


class Threshold(BaseModel):
    """Limits of a parameter in the context of a document, in the parameter's unit."""

    id: int
    parameter_id: int
    document_id: int
    lower_limit: float | None
    upper_limit: float | None
    description: str | None


class Configuration(BaseModel):
    sampling_time_parameter_id: int | None = Field(
        description="The parameter whose datetime raw value is a collection's sampling time.")
    collection_classes: list[CollectionClass]


# Collections, values and raw values

class Reading(Request):
    """What a raw value holds: exactly one of the four, matching the parameter's value class."""

    decimal: float | None = None
    text: str | None = Field(None, max_length=50)
    boolean: bool | None = None
    datetime: dt.datetime | None = None

    @model_validator(mode="after")
    def one(self) -> Self:
        if sum(v is not None for v in (self.decimal, self.text, self.boolean, self.datetime)) != 1:
            raise ValueError("set exactly one of decimal, text, boolean, datetime")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in ("decimal", "text", "boolean", "datetime") if getattr(self, k) is not None)


class Limit(BaseModel):
    threshold_id: int
    document_id: int
    lower_limit: float | None
    upper_limit: float | None


class RawValue(BaseModel):
    id: int
    value_id: int
    t: dt.datetime | None
    person_id: int | None
    device_id: int | None
    unit_id: int | None
    reading: Reading | None = Field(description="None if the raw value holds nothing.")
    accepted: bool
    exceeds: bool = Field(description="Outside one of the limits that apply.")
    etag: str


class Value(BaseModel):
    """A collection's slot for one effect; its raw values are the readings, oldest first."""

    id: int
    collection_id: int
    effect_id: int
    name: str | None
    limits: list[Limit] = Field(description="Thresholds of the parameter in the documents of the action.")
    raw_values: list[RawValue]


class Collection(BaseModel):
    id: int
    name: str | None
    matrix_id: int
    collection_class: CollectionClass
    spot_id: int | None
    site_id: int | None
    registered_at: dt.datetime | None = Field(description="From the insert entry in l_collection.")
    registered_by: int | None
    sampled_at: dt.datetime | None = Field(
        description="The sampling-time raw value, else the registration time.")
    sampled_by: int | None
    status: Status
    planned: int = Field(description="Values (effects) planned for the collection.")
    measured: int = Field(description="Values with at least one raw value.")
    exceedances: int


class CollectionDetail(Collection):
    values: list[Value]


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class CollectionIn(Note):
    matrix_id: int
    collection_class: CollectionClass
    spot_id: int | None = None
    site_id: int | None = None
    action_ids: list[int] = Field(default_factory=list,
                                  description="Plan every effect of these actions as a value.")
    sampled_at: dt.datetime | None = Field(
        None, description="Recorded as the sampling-time raw value; needs that parameter planned.")

    @model_validator(mode="after")
    def one_location(self) -> Self:
        if (self.spot_id is None) == (self.site_id is None):
            raise ValueError("relate the collection to a spot or a site")
        return self


class PlanIn(Note):
    action_ids: list[int] = Field(min_length=1)


class RawValueIn(Note):
    reading: Reading
    t: dt.datetime | None = Field(None, description="When it was measured; defaults to now.")
    device_id: int | None = Field(None, description="Defaults to the first active device of the action.")
    unit_id: int | None = Field(None, description="Defaults to the parameter's unit.")


class SeriesPoint(BaseModel):
    raw_value_id: int
    value_id: int
    collection_id: int
    sampled_at: dt.datetime | None
    value: float
    exceeds: bool


# Management

class Task(BaseModel):
    id: int
    request: str
    description: str | None
    assigner_id: int
    assignee_id: int
    deadline: dt.datetime | None
    done: bool
    etag: str


class TaskIn(Note):
    request: str = Field(min_length=1, max_length=50)
    description: str | None = Field(None, max_length=250)
    assignee_id: int
    deadline: dt.datetime | None = None


class TaskPatch(Note):
    done: bool | None = None
    deadline: dt.datetime | None = None
    assignee_id: int | None = None


class Message(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    subject: str
    body: str
    read: bool = Field(description="The opposite of lucentBASE's message.new.")
    sent_at: dt.datetime | None = Field(description="From the insert entry in l_message.")


class MessageIn(Request):
    receiver_id: int
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=500)


class Health(BaseModel):
    status: Literal["ok"]
    version: str
