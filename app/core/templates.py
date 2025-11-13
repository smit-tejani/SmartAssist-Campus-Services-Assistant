from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from starlette.requests import Request

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_TEMPLATE_ROOT = _BASE_DIR / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))


@pass_context
def url_for(context, name: str, /, **path_params):
    """
    Jinja helper that returns **relative paths** instead of absolute URLs.

    This avoids mixed-content issues (http vs https) on platforms like
    Hugging Face Spaces, because the browser will reuse the current
    scheme for paths such as "/static/...".
    """
    request: Request = context["request"]
    # This uses the app router directly and only returns the path,
    # e.g. "/static/css/common.css" instead of "http://...".
    return request.app.url_path_for(name, **path_params)


# Override Starlette's default helper globally for all templates.
templates.env.globals["url_for"] = url_for
