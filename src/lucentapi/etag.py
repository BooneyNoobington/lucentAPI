"""ETags from row content, so two people can't overwrite each other's changes unnoticed.

The ETag of a record is a hash of its current columns. A client sends it back in `If-Match`
when it changes the record; if someone else changed it in between (through the API or not),
the hashes differ and the API answers 412.
"""

import hashlib
import json
from datetime import date, datetime

from fastapi import Request

from .errors import APIError


def _plain(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_plain)


def etag(row: dict) -> str:
    return '"' + hashlib.sha256(canonical(row).encode()).hexdigest()[:24] + '"'


def check(request: Request, row: dict, required: bool) -> None:
    sent = request.headers.get("if-match")
    if sent is None:
        if required:
            raise APIError(428, "precondition_required",
                           "Send the record's ETag in If-Match, so changes by others aren't overwritten.")
        return
    if sent.strip() != "*" and etag(row) not in [tag.strip() for tag in sent.split(",")]:
        raise APIError(412, "precondition_failed",
                       "The record has changed since you loaded it. Load it again and retry.",
                       headers={"ETag": etag(row)})
