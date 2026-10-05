"""Epsilon v2 backend registry. New languages plug in without touching the core."""
from __future__ import annotations


REGISTRY: dict[str, object] = {}


def register(name: str, backend: object) -> None:
    REGISTRY[name] = backend


def get_backend(language: str):
    lang = (language or 'python').strip().lower()
    if lang in REGISTRY:
        return REGISTRY[lang]
    if lang in ('python', 'py'):
        from .gen_python import PythonBackend
        be = PythonBackend()
        REGISTRY['python'] = be
        return be
    if lang in ('javascript', 'js', 'typescript', 'ts'):
        from .gen_js import JsBackend
        be = JsBackend(mode='ts' if lang in ('typescript', 'ts') else 'js')
        REGISTRY[lang] = be
        return be
    raise ValueError("unsupported language %r: expected python|javascript|typescript" % (language,))


def supported_languages() -> list[str]:
    return ['python', 'javascript', 'typescript']
