"""Request and response bodies. Requests reject unknown fields."""

import datetime as dt
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["registered", "in_analysis", "in_review", "approved"]
Role = Literal["sampler", "analyst", "reviewer", "admin"]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Note(Request):
    note: str | None = Field(None, max_length=2000, description="Why, for the audit trail.")


# People and organizations

class Organization(BaseModel):
    id: int
    name: str
    shorthand: str | None


class Person(BaseModel):
    id: int
    given_name: str | None
    surname: str
    unix_account: str | None
    organization_id: int | None
    position: str | None


class Me(BaseModel):
    person: Person
    roles: list[Role]


# Locations

class GeoPoint(BaseModel):
    latitude: float
    longitude: float


class Address(BaseModel):
    street: str | None
    house_number: str | None
    addition: str | None
    zip: str | None
    district: str | None
    city: str | None


class ReferenceSystem(BaseModel):
    id: int
    epsg: int | None
    name: str


class Site(BaseModel):
    id: int
    name: str
    description: str | None
    address: Address | None
    border: list[GeoPoint]
    crs_id: int | None


class Spot(BaseModel):
    id: int
    name: str
    description: str | None
    x: float | None = Field(description="Latitude, as in lucentBASE.")
    y: float | None = Field(description="Longitude, as in lucentBASE.")
    crs_id: int | None
    site_id: int | None


# Catalog

class Matrix(BaseModel):
    id: int
    name: str
    description: str | None


class CollectionClass(BaseModel):
    id: int
    name: str
    description: str | None
    action_ids: list[int] = Field(description="The actions this class plans.")


class Dimension(BaseModel):
    id: int
    name: str
    description: str | None


class Unit(BaseModel):
    id: int
    name: str
    notations: list[str]
    dimension_id: int | None


class Variable(BaseModel):
    id: int
    name: str
    value_class: Literal["decimal", "text", "boolean", "datetime"]
    description: str | None
    dimension_id: int | None


class Device(BaseModel):
    id: int
    name: str
    number: str | None
    manufacturer_id: int | None
    operator_id: int | None
    active: bool
    etag: str


class DevicePatch(Note):
    active: bool


class Assignment(BaseModel):
    person_id: int
    role: str | None


class SOPDocument(BaseModel):
    id: int
    name: str
    url: str | None
    file_path: str | None
    accepted: bool
    sha256: str | None


class SOP(BaseModel):
    id: int
    name: str
    active: bool
    documents: list[SOPDocument]
    people: list[Assignment]


class ActionVariable(BaseModel):
    id: int
    action_id: int
    variable_id: int
    unit_id: int | None
    uncertainty: float | None
    uncertainty_class: str | None
    uncertainty_unit_id: int | None
    calculation: str | None
    decimals: int | None
    lower_limit: float | None
    upper_limit: float | None


class Action(BaseModel):
    id: int
    name: str
    standard: str | None
    iteration: int | None
    active: bool
    description: str | None
    sop_id: int | None
    device_ids: list[int]
    people: list[Assignment]
    depends_on: list[int]
    implies: list[int]
    collection_class_ids: list[int]
    variables: list[ActionVariable]


# Collections and values

class Reading(Request):
    """Exactly one of the four, matching the variable's value class."""

    decimal: float | None = None
    text: str | None = Field(None, max_length=10_000)
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


class Value(BaseModel):
    id: int
    collection_id: int
    action_variable_id: int
    repetition: int
    reading: Reading
    accepted: bool
    measured_at: dt.datetime | None
    person_id: int | None = Field(description="Who measured it (the latest raw value).")
    device_id: int | None
    exceeds: bool = Field(description="Outside the action variable's limits.")
    etag: str


class ValueIn(Note):
    action_variable_id: int
    reading: Reading
    device_id: int | None = Field(None, description="Defaults to the first active device of the action.")
    measured_at: dt.datetime | None = Field(None, description="Defaults to now.")


class Collection(BaseModel):
    id: int
    name: str
    matrix_id: int | None
    class_id: int | None
    spot_id: int | None
    site_id: int | None
    sampled_at: dt.datetime | None
    sampler_id: int | None
    status: Status
    measured: int = Field(description="Planned variables with at least one value.")
    planned: int
    exceedances: int


class CollectionDetail(Collection):
    values: list[Value]


class CollectionPage(BaseModel):
    items: list[Collection]
    total: int
    limit: int
    offset: int


class CollectionIn(Note):
    matrix_id: int
    class_id: int
    spot_id: int | None = None
    site_id: int | None = None
    sampled_at: dt.datetime | None = Field(None, description="Defaults to now.")
    sampler_id: int | None = Field(None, description="Defaults to the caller.")

    @model_validator(mode="after")
    def one_location(self) -> Self:
        if (self.spot_id is None) == (self.site_id is None):
            raise ValueError("relate the collection to a spot or a site")
        return self


class SeriesPoint(BaseModel):
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
    assigner_id: int | None
    assignee_id: int | None
    deadline: dt.datetime | None
    done: bool
    etag: str


class TaskIn(Note):
    request: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    assignee_id: int
    deadline: dt.datetime | None = None


class TaskPatch(Note):
    done: bool | None = None
    deadline: dt.datetime | None = None
    assignee_id: int | None = None


class Message(BaseModel):
    id: int
    sender_id: int | None
    receiver_id: int | None
    subject: str | None
    body: str | None
    read: bool
    sent_at: dt.datetime | None


class MessageIn(Request):
    receiver_id: int
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(max_length=10_000)


class Health(BaseModel):
    status: Literal["ok"]
    version: str
