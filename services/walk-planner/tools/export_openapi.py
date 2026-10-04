#!/usr/bin/env python3
"""Export the walk-planner service's OpenAPI document to docs/openapi.json (sorted keys, stable output).

    python tools/export_openapi.py              # (re)write docs/openapi.json
    python tools/export_openapi.py --check      # exit 1 when docs/openapi.json is not current (CI)
    python tools/export_openapi.py --out -      # print to stdout

The document is built from the code alone: ``create_app`` with default settings and no bundles -- nothing is
loaded, no network, the environment is ignored. FastAPI / pydantic versions shape the generated JSON Schema,
so ``info.x-generated-with`` records them; regenerate after upgrading either (deploy/requirements.lock pins
both). Run from anywhere; the package root is put on ``sys.path``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "openapi.json"


def build_openapi() -> dict:
    """The service's OpenAPI document (a dict), independent of the environment."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import fastapi
    import pydantic

    from walk_planner.service.app import create_app
    from walk_planner.service.settings import Settings

    app = create_app(Settings.model_construct(), bundles=None, setup_logging=False)   # defaults, no env
    doc = app.openapi()
    doc["info"]["x-generated-with"] = {"tool": "tools/export_openapi.py", "fastapi": fastapi.__version__,
                                       "pydantic": pydantic.__version__}
    return doc


def render(doc: dict) -> str:
    """The canonical text: 2-space indent, sorted keys, UTF-8, trailing newline."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _without_stamp(text: str) -> dict:
    """The document minus info.x-generated-with, so --check does not fail on a patch-level library bump."""
    doc = json.loads(text)
    doc.get("info", {}).pop("x-generated-with", None)
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output file, or - for stdout (default %(default)s)")
    parser.add_argument("--check", action="store_true", help="compare with --out instead of writing; 1 = stale")
    args = parser.parse_args(argv)
    text = render(build_openapi())
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current is not None and current != text and _without_stamp(current) == _without_stamp(text):
            print(f"{out} is current (only the fastapi/pydantic version stamp differs)")
            return 0
        if current != text:
            print(f"{out} is not current: run python tools/export_openapi.py", file=sys.stderr)
            return 1
        print(f"{out} is current")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    doc = json.loads(text)
    print(f"wrote {out} ({len(text)} bytes, {len(doc.get('paths', {}))} paths, "
          f"{len(doc.get('components', {}).get('schemas', {}))} schemas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
