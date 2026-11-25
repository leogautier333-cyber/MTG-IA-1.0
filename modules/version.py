"""Numéro de version affiché en bas de page : le commit Git du dossier de l'application."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def current_version(root=ROOT):
    """Retourne les 7 premiers caractères du commit courant, ou None si le dépôt est illisible."""
    git_dir = Path(root) / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7] or None
        reference = head.split(":", 1)[1].strip()
        reference_file = git_dir / reference
        if reference_file.is_file():
            return reference_file.read_text(encoding="utf-8").strip()[:7] or None
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + reference):
                return line.split(" ", 1)[0][:7]
    except OSError:
        return None
    return None
