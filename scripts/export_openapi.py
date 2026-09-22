"""Write the OpenAPI document to ``openapi/openapi.yaml``.

The committed snapshot is the API contract that ``tests/contract`` validates
against. Run ``make openapi`` after changing any endpoint or schema.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DEFAULT_OUTPUT = ROOT / "openapi" / "openapi.yaml"


def build_spec() -> dict[str, Any]:
    settings = Settings(_env_file=None, environment="test", database_url="sqlite://")
    return create_app(settings).openapi()


def render(spec: dict[str, Any]) -> str:
    return yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if the snapshot is stale instead of writing")
    args = parser.parse_args()
    rendered = render(build_spec())
    if args.check:
        current = args.output.read_text() if args.output.exists() else ""
        if current != rendered:
            print(f"{args.output} is out of date; run `make openapi`", file=sys.stderr)
            return 1
        print("OpenAPI snapshot is up to date")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
