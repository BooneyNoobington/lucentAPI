"""Prints a development token.

    uv run python dev/mint_token.py grindel admin
    uv run python dev/mint_token.py xx121 reviewer --minutes 5

The first run creates a signing key in dev/.keys/ and dev/jwks.json, which .env.example points
the API at.
"""

import argparse
from pathlib import Path

from lucentapi.devtokens import DevSigner

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("username", help="a person.linux_user, e.g. grindel")
parser.add_argument("roles", nargs="*", help="sampler, analyst, reviewer, admin")
parser.add_argument("--minutes", type=int, default=60)
args = parser.parse_args()
print(DevSigner(Path(__file__).parent).token(args.username, args.roles, lifetime=args.minutes * 60))
