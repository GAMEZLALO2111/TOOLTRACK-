"""Cliente HTTP común de la interfaz: agrega el token, impone un timeout por defecto y avisa si la sesión venció."""
import os

import requests

API_BASE = os.getenv("TOOLTRACK_API_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_TIMEOUT = 15

_state = {"token": None, "permissions": frozenset(), "on_unauthorized": None, "installed": False}


def set_session(token, permissions):
    _state["token"] = token
    _state["permissions"] = frozenset(permissions or ())


def clear_session():
    set_session(None, ())


def token():
    return _state["token"]


def can(permission):
    return permission in _state["permissions"]


def set_unauthorized_handler(handler):
    _state["on_unauthorized"] = handler


def install():
    """Parcha requests.Session.request una sola vez: todas las llamadas existentes heredan token y timeout."""
    if _state["installed"]:
        return
    original = requests.sessions.Session.request

    def request(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = DEFAULT_TIMEOUT
        is_api = str(url).startswith(API_BASE)
        if is_api and _state["token"]:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("Authorization", f"Bearer {_state['token']}")
            kwargs["headers"] = headers
        response = original(self, method, url, **kwargs)
        if is_api and response.status_code == 401 and _state["token"] and not str(url).endswith("/login"):
            handler = _state["on_unauthorized"]
            if handler:
                handler()
        return response

    requests.sessions.Session.request = request
    _state["installed"] = True


def error_detail(response, fallback="The operation could not be completed"):
    """Texto legible del error de la API (sin códigos HTTP)."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) for item in detail)
    if response.status_code == 403:
        return detail or "Your role is not allowed to do this."
    return detail or fallback
