"""ETags from row content, so two people can't overwrite each other's changes unnoticed.

The ETag of a record is a hash of its current columns. A client sends it back in `If-Match`
when it changes the record; if someone else changed it in between (through the API or not),
the hashes differ and the API answers 412.
"""

import hashlib

from fastapi import Request

from .audit import canonical
from .errors import APIError


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
