#!/usr/bin/env python3
"""Show the built app as Frontier instead of Crest.

Frontier keeps Crest's source text unchanged so syncing with Crest stays
conflict-free. Xcode runs this script as the last build phase of each app
target, before it signs the bundle. It rewrites the product name in the text a
person sees: the compiled string tables in every .lproj folder and the values
of the bundle's Info.plist. Keys, identifiers, URL schemes and type names are
left alone, because only the whole word "Crest" in a value is replaced.

Xcode leaves a string out of the compiled source-language table when the
catalog has no explicit value for it, and the app then shows the key itself.
For those, the script reads the string catalogs and adds the branded key to
the table, so every catalog string that says Crest reads Frontier.

Usage: brand-app.py <path to built .app or .appex>
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

PRODUCT_NAME = "Frontier"
UPSTREAM_NAME = re.compile(r"\bCrest\b")

# Values that must keep Crest's spelling even though they read as a word:
# class names resolved at runtime and engine-facing switches.
PROTECTED_INFO_KEYS = frozenset({
    "NSExtensionPrincipalClass",
    "NSDockTilePlugIn",
    "NSPrincipalClass",
    "CFBundleExecutable",
    "CFBundleIdentifier",
})


def rebrand(value):
    """Return `value` with every whole-word Crest replaced, recursively.

    Values under a protected key keep their spelling at any depth."""
    if isinstance(value, str):
        return UPSTREAM_NAME.sub(PRODUCT_NAME, value)
    if isinstance(value, list):
        return [rebrand(item) for item in value]
    if isinstance(value, dict):
        return {key: item if key in PROTECTED_INFO_KEYS else rebrand(item)
                for key, item in value.items()}
    return value


def load(path: Path):
    """Read a plist in any format; old-style text .strings go through plutil."""
    data = path.read_bytes()
    try:
        return plistlib.loads(data), data.startswith(b"bplist")
    except Exception:
        subprocess.run(["plutil", "-convert", "binary1", str(path)], check=True)
        return plistlib.loads(path.read_bytes()), True


def save(path: Path, value, binary: bool) -> None:
    fmt = plistlib.FMT_BINARY if binary else plistlib.FMT_XML
    path.write_bytes(plistlib.dumps(value, fmt=fmt, sort_keys=False))


def brand_string_table(path: Path) -> int:
    table, binary = load(path)
    branded = rebrand(table)
    if branded == table:
        return 0
    save(path, branded, binary)
    return sum(1 for key in table if table[key] != branded[key])


def add_missing_catalog_strings(resources: Path, catalog: Path) -> int:
    """Add branded entries for catalog keys the compiled table left out."""
    strings = json.loads(catalog.read_text(encoding="utf-8"))
    language = strings.get("sourceLanguage", "en")
    folder = resources / f"{language}.lproj"
    if not folder.is_dir():
        return 0
    table_path = folder / f"{catalog.stem}.strings"
    plurals_path = folder / f"{catalog.stem}.stringsdict"
    table, binary = load(table_path) if table_path.is_file() else ({}, True)
    plurals = load(plurals_path)[0] if plurals_path.is_file() else {}

    added = 0
    for key in strings.get("strings", {}):
        if key in table or key in plurals or not UPSTREAM_NAME.search(key):
            continue
        table[key] = rebrand(key)
        added += 1
    if added:
        save(table_path, table, binary)
    return added


def catalogs() -> list[Path]:
    """String catalogs compiled into Frontier's bundles."""
    source_root = os.environ.get("SRCROOT")
    if not source_root:
        return []
    return sorted(Path(source_root, "CrestShared/Resources").glob("*.xcstrings"))


def brand_info_plist(path: Path) -> int:
    info, binary = load(path)
    branded = rebrand(info)
    if branded == info:
        return 0
    save(path, branded, binary)
    return sum(1 for key in info if info[key] != branded[key])


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 64
    bundle = Path(sys.argv[1])
    if not bundle.is_dir():
        print(f"error: {bundle} is not a built bundle", file=sys.stderr)
        return 66

    # macOS bundles keep resources in Contents/Resources; iOS bundles are flat.
    contents = bundle / "Contents"
    resources = contents / "Resources" if contents.is_dir() else bundle
    info = (contents if contents.is_dir() else bundle) / "Info.plist"

    changed = 0
    for table in sorted(resources.glob("*.lproj/*")):
        if table.suffix in {".strings", ".stringsdict"}:
            changed += brand_string_table(table)
    for catalog in catalogs():
        changed += add_missing_catalog_strings(resources, catalog)
    if info.is_file():
        changed += brand_info_plist(info)

    print(f"Frontier branding: updated {changed} entries in {bundle.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
