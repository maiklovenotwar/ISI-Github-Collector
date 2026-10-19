"""Ausgabedateien mit lizenzierten Inhalten nur in Pfade, die Git ignoriert.

Orbis-Exporte enthalten Firmennamen und BvD-Kennungen. Eine solche Datei im
Repository-Wurzelverzeichnis wäre nicht ignoriert und könnte versehentlich
committet werden (detect-secrets erkennt Orbis-Daten nicht).
"""

import subprocess
from pathlib import Path

DEFAULT_ORBIS_OUTPUT_DIR = Path("workstreams/orbis_matching/data")


def require_ignored_path(path: Path, repo_root: Path) -> Path:
    """Gib `path` zurück, wenn Git ihn ignoriert; sonst ValueError.

    Args:
        path: Zielpfad der Ausgabedatei (muss noch nicht existieren).
        repo_root: Wurzel des Git-Repositorys, gegen dessen Regeln geprüft wird.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(
            f"{path} is not ignored by Git; write files with Orbis content to a path "
            f"such as {DEFAULT_ORBIS_OUTPUT_DIR}/ instead"
        )
    return path
