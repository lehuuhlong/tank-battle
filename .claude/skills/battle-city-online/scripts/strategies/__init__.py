"""Strategy registry. Add a strategy: create a module here, subclass Strategy, decorate with @register."""
import importlib
import pkgutil

REGISTRY = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


def load_all():
    for m in pkgutil.iter_modules(__path__):
        if m.name != "base":
            importlib.import_module(f"{__name__}.{m.name}")
    return REGISTRY


def get(name, **params):
    load_all()
    if name not in REGISTRY:
        raise SystemExit(f"Không có chiến thuật '{name}'. Có: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name](**params)
