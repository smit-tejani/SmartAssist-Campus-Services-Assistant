from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates


_BASE_DIR = Path(__file__).resolve().parent.parent.parent

_TEMPLATE_ROOT = _BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))
