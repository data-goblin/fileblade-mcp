from pathlib import Path
import sys

_plugins = Path(__file__).resolve().parents[2]
_core = _plugins / "data-goblin.fileblade"
if not _core.is_dir():
    _core = _plugins / "fileblade"
sys.path.insert(0, str(_core / "python"))

from .inventory import Inventory

__all__ = ["Inventory"]
