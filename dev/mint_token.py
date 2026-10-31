"""Prints a development token.

    uv run python dev/mint_token.py grindel
    uv run python dev/mint_token.py xx121 --minutes 5

The token only says who you are. What you may do comes from lucentBASE's roles, which an admin
grants in lucentLIMS (or with PUT /v1/people/{id}/roles).

The first run creates a signing key in dev/.keys/ and dev/jwks.json, which .env.example points
the API at.
"""

import argparse
import sys
from pathlib import Path

from lucentapi.devtokens import DevSigner

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("username", help="a person.linux_user, e.g. grindel")
parser.add_argument("roles", nargs="*", help=argparse.SUPPRESS)  # tokens carried roles until 2026-10-31
parser.add_argument("--minutes", type=int, default=60)
args = parser.parse_args()
if args.roles:
    print(f"Ignoring {' '.join(args.roles)}: roles come from lucentBASE now.", file=sys.stderr)
print(DevSigner(Path(__file__).parent).token(args.username, lifetime=args.minutes * 60))
