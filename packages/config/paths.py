"""Where HOOD keeps persistent data: everything under ONE folder, ``HOOD_DATA_DIR``.

Before security batch 1 several stores used paths relative to the folder HOOD was started from
(``artifacts/hood_data.db``, ``artifacts/economic_engine.db``...), the encrypted vault lived inside
the code folder, and missions went to ``~/.hood`` whatever ``HOOD_DATA_DIR`` said. In the container
none of that was on the ``/data`` volume (lost when the container is recreated) and none of it was
in backups of the data folder.

``store_path`` keeps an absolute path as given and puts every relative one under the data folder
(a leading ``artifacts/`` is dropped: ``artifacts/hood_data.db`` -> ``<data>/hood_data.db``).
``migrate_legacy_data`` (called once at start-up) copies a store still at its old place (relative to
the current folder or the HOOD code folder, or ``~/.hood/nova21``) into the data folder; SQLite goes
through its backup API, so a database in WAL mode copies consistently. Old copies are left untouched
for the owner to delete after checking.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Iterable, Optional, Union

REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.Lock()


def data_dir() -> Path:
    return Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")).expanduser()


def _relative_target(rel: Path) -> Path:
    parts = rel.parts
    if parts and parts[0] == "artifacts":
        parts = parts[1:]
    return data_dir().joinpath(*parts) if parts else data_dir()


def _legacy_candidates(rel: Path, extra: Iterable[Path] = ()) -> list:
    seen, out = set(), []
    for cand in [Path.cwd() / rel, REPO_ROOT / rel, *extra]:
        try:
            key = cand.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _no_links(folder: str, names: list) -> list:
    return [n for n in names if os.path.islink(os.path.join(folder, n))]


def _trusted_source(path: Path) -> bool:
    """Never follow a link, and (POSIX) only copy what HOOD's own account owns: a link or someone
    else's file must not become the owner's data (and then end up in backups)."""
    if path.is_symlink():
        return False
    if os.name == "posix" and hasattr(os, "geteuid"):
        try:
            return path.stat().st_uid == os.geteuid()
        except OSError:
            return False
    return True


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".migrating")
    if src.is_dir():
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp, symlinks=True, ignore=_no_links)   # links inside are dropped
    elif _is_sqlite(src):
        if tmp.exists():
            tmp.unlink()
        source, target = sqlite3.connect(str(src)), sqlite3.connect(str(tmp))
        try:
            with target:
                source.backup(target)
        finally:
            source.close()
            target.close()
    else:
        shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def store_path(configured: Optional[Union[str, os.PathLike]], default: str, **_ignored) -> Path:
    """Where a persistent store lives: an absolute path as given, a relative one under HOOD_DATA_DIR
    (``artifacts/x.db`` -> ``<data>/x.db``). Pure resolution: old copies are carried over only by
    ``migrate_legacy_data`` at start-up."""
    raw = Path(configured) if configured else Path(default)
    return raw if raw.is_absolute() else _relative_target(raw)


def store_dir(configured: Optional[Union[str, os.PathLike]], default: str, *_args, **_kwargs) -> Path:
    """Same as store_path for a folder; the folder is created."""
    path = store_path(configured, default)
    path.mkdir(parents=True, exist_ok=True)
    return path


# Stores that used to live outside the data folder: (old relative path, files that go with it).
# Scratch folders (logs, downloads, browser evidence, caches) are deliberately not carried over.
LEGACY_STORES = (
    ("artifacts/auth.db", ()), ("artifacts/hood_data.db", ()), ("artifacts/vault.enc", (".vault_key",)),
    ("artifacts/economic_engine.db", ()), ("artifacts/impossible_list.db", ()),
    ("artifacts/intelligence_lab.db", ()), ("artifacts/tech_intelligence.db", ()),
    ("artifacts/acceleration_engine.db", ()), ("artifacts/self_evolution.db", ()),
    ("artifacts/nodes/node_registry.json", ()), ("artifacts/sentinel_integrity_baseline.json", ()),
    ("artifacts/sentinel_vulnerabilities.json", ()), ("artifacts/sentinel_self_healing.log", ()),
    ("artifacts/sentinel_patches", ()), ("artifacts/checkpoints", ()), ("artifacts/evolution", ()),
)


def migrate_legacy_data(roots: Optional[Iterable[Path]] = None, home: Optional[Path] = None) -> list:
    """Copy stores found at their pre-batch-1 places into HOOD_DATA_DIR, once (start-up only).

    Looks in the HOOD code folder (``roots``) and in ``~/.hood/nova21``; skips links and files owned
    by another account.
    Never overwrites: a store already in the data folder wins. Old copies are left in place.
    Returns [(old, new), ...]."""
    if roots is None and os.environ.get("HOOD_SKIP_LEGACY_MIGRATION") == "1":
        return []           # the test suite: never copy the machine's real data into a test folder
    # Only the HOOD code folder: the folder HOOD happens to be started from is not trusted (someone
    # else's artifacts/auth.db there must never become this HOOD's Root Owner).
    roots = list(roots) if roots is not None else [REPO_ROOT]
    moves = [(rel, sidecars, [r / rel for r in roots]) for rel, sidecars in LEGACY_STORES]
    moves.append(("nova21", (), [Path(home or Path.home()) / ".hood" / "nova21"]))
    done = []
    with _LOCK:
        for rel, sidecars, candidates in moves:
            target = _relative_target(Path(rel))
            if target.exists():
                continue
            seen = set()
            for old in candidates:
                try:
                    key = old.resolve()
                except OSError:
                    continue
                if key in seen or not old.exists() or key == target.resolve() or not _trusted_source(old):
                    continue
                seen.add(key)
                _copy(old, target)
                for name in sidecars:
                    side_old, side_new = old.parent / name, target.parent / name
                    if side_old.is_file() and not side_new.exists() and _trusted_source(side_old):
                        _copy(side_old, side_new)
                        if os.name == "posix":
                            side_new.chmod(0o600)
                done.append((old, target))
                sys.stderr.write(f"HOOD data: copied {old} -> {target} (all of HOOD's data now lives in "
                                 f"{data_dir()}; the old copy can be deleted after checking).\n")
                break
    return done
