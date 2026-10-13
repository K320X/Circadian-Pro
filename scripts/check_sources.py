"""Validate runtime source and translation structure without importing HA."""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "circadian_pro"


def leaf_keys(value: dict, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    keys: set[tuple[str, ...]] = set()
    for name, child in value.items():
        path = (*prefix, name)
        if isinstance(child, dict):
            keys.update(leaf_keys(child, path))
        else:
            keys.add(path)
    return keys


def main() -> None:
    conflicts = [str(path.relative_to(COMPONENT)) for path in COMPONENT.rglob("*") if "edit conflict" in path.name.lower()]
    if conflicts:
        raise ValueError(f"Resolve synchronized source conflicts before packaging: {conflicts}")
    sources = list(COMPONENT.rglob("*.py"))
    for path in sources:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    documents = {path: json.loads(path.read_text(encoding="utf-8")) for path in COMPONENT.rglob("*.json")}
    baseline = leaf_keys(documents[COMPONENT / "strings.json"])
    for path, document in documents.items():
        if path.parent.name != "translations":
            continue
        missing = baseline - leaf_keys(document)
        if missing:
            raise ValueError(f"Missing translations in {path.name}: {sorted(missing)}")
    manifest = documents[COMPONENT / "manifest.json"]
    if manifest["domain"] != COMPONENT.name:
        raise ValueError("Manifest domain does not match the component folder")
    print(f"Validated {len(sources)} Python sources, {len(documents)} JSON documents and all required translations.")


if __name__ == "__main__":
    main()
