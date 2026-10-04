#!/usr/bin/env python3
"""Export the Walk Planner message catalog to docs/messages.json (machine-readable, for the app).

    python tools/export_messages.py              # (re)write docs/messages.json
    python tools/export_messages.py --check      # exit 1 when docs/messages.json is not current (CI / tests)
    python tools/export_messages.py --out -      # print to stdout

One entry per code of ``walk_planner.messages``: the user-facing messages (``MESSAGES``: inside 200
responses, scopes request / variant / stop) and the HTTP errors (``ERRORS``: the planner's and the
service's codes, with their HTTP status). Each entry has

    code, kind ("message" | "error"), severity, scope, http_status (errors; null for messages),
    params (always present), optional_params (present in some responses),
    ru, en (the texts of the first example), examples [{params, ru, en}] (one per text variant)

The API already sends every message / error rendered (``text`` / ``message`` in the requested ``lang``);
this file lets the app list the codes, localise them itself, or show a fallback for a code it does not
know. ``place_temporarily_closed`` is both a stop warning and a 422 error (two entries). Built from the
code alone (no data, no network); stable output (sorted codes, sorted keys).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "messages.json"
FORMAT = "walk-messages/v1"


def build_catalog() -> dict:
    """The catalog document (a dict) from ``walk_planner.messages``."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from walk_planner.messages import ERRORS, LANGS, MESSAGES, render, render_error
    from walk_planner.version import ALGORITHM_VERSION, API_VERSION

    def entry(d, kind: str) -> dict:
        draw = render_error if kind == "error" else render
        examples = [{"params": dict(ex), "ru": draw(d.code, ex, "ru"), "en": draw(d.code, ex, "en")}
                    for ex in (d.examples or ({},))]
        return {"code": d.code, "kind": kind, "severity": d.severity, "scope": d.scope,
                "http_status": d.http_status if kind == "error" else None,
                "params": list(d.params), "optional_params": list(d.optional),
                "ru": examples[0]["ru"], "en": examples[0]["en"], "examples": examples}

    messages = [entry(MESSAGES[c], "message") for c in sorted(MESSAGES)]
    errors = [entry(ERRORS[c], "error") for c in sorted(ERRORS)]
    return {
        "format": FORMAT,
        "generated_by": "tools/export_messages.py",
        "versions": {"api": API_VERSION, "algorithm": ALGORITHM_VERSION},
        "languages": list(LANGS),
        "notes": [
            "Messages (kind 'message') come inside 200 responses: plan 'messages' (scope request), variant 'messages' "
            "(scope variant; scope stop with 'stop_index'). Each carries code, severity, scope, params, stop_index and "
            "'text' rendered in the request's lang.",
            "Errors (kind 'error') are the HTTP error bodies {\"error\": {\"code\", \"message\", \"params\"}} with "
            "'http_status'; 503 busy and not_ready come with a Retry-After header (2 / 5 s).",
            "Codes are append-only: treat an unknown message code as informational and an unknown error code by its "
            "HTTP status.",
            "ru / en are the texts of the first example; the API renders the real params. Russian texts of the codes "
            "the dashboard already showed reproduce it byte for byte; Russian texts of codes new in v1 (closed places, "
            "routing estimate, validation and service errors) and all English texts are drafts needing product sign-off.",
            "params are always present; optional_params only in some responses (e.g. validation_error from the "
            "request-format check carries 'errors', from the planner 'field' / 'reason').",
        ],
        "messages": messages,
        "errors": errors,
    }


def render_text(doc: dict) -> str:
    """The canonical text: 2-space indent, sorted keys, UTF-8, trailing newline."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output file, or - for stdout (default %(default)s)")
    parser.add_argument("--check", action="store_true", help="compare with --out instead of writing; 1 = stale")
    args = parser.parse_args(argv)
    text = render_text(build_catalog())
    if args.out == "-":
        sys.stdout.write(text)
        return 0
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current != text:
            print(f"{out} is not current: run python tools/export_messages.py", file=sys.stderr)
            return 1
        print(f"{out} is current")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    doc = json.loads(text)
    print(f"wrote {out} ({len(text)} bytes, {len(doc['messages'])} messages, {len(doc['errors'])} errors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
