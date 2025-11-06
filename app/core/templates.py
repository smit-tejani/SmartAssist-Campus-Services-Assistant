from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.config import settings


_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_ROOT = _BASE_DIR / "templates"

variant_dir = _TEMPLATE_ROOT / settings.template_variant
if not variant_dir.exists():
    # Fallback to the enhanced Smart Campus UI if the requested directory is
    # missing. This keeps the application operational even when only one theme
    # has been deployed to the server.
    variant_dir = _TEMPLATE_ROOT / "smart_campus"

templates = Jinja2Templates(directory=str(variant_dir))
