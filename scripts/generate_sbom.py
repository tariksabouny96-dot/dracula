"""Write a CycloneDX 1.5 SBOM (JSON) for the pinned Python dependencies.

Uses only installed package metadata (no network). Run inside the project venv:
    python scripts/generate_sbom.py --out release/SBOM.cdx.json
"""
import argparse
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "release" / "SBOM.cdx.json")
    args = parser.parse_args()
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    components, missing = [], []
    for line in req.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;#]+)", line.strip())
        if not m:
            continue
        name, pinned = m.groups()
        try:
            dist = metadata.distribution(name)
            installed = dist.version
            lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "UNKNOWN"
        except metadata.PackageNotFoundError:
            installed, lic = None, "UNKNOWN"
            missing.append(name)
        components.append({
            "type": "library", "name": name, "version": pinned,
            "purl": f"pkg:pypi/{name.lower()}@{pinned}",
            "licenses": [{"license": {"name": (lic or "UNKNOWN").splitlines()[0][:120]}}],
            "properties": [{"name": "hood:installed_version", "value": str(installed)},
                           {"name": "hood:pin_matches_installed", "value": str(installed == pinned)}]})
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(),
                         "component": {"type": "application", "name": "hood-system", "version": commit},
                         "properties": [{"name": "hood:requirements_sha256",
                                         "value": hashlib.sha256(req.encode()).hexdigest()}]},
            "components": components}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    print(json.dumps({"components": len(components), "not_installed": missing, "out": str(args.out)}))


if __name__ == "__main__":
    main()
