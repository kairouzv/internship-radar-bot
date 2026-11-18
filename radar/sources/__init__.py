from . import apis, ats, auto, email_alerts, feeds  # noqa: F401  (enregistrent les types de sources)
from .base import REGISTRY, Source


def build_sources(source_cfgs: list[dict], secret) -> list[Source]:
    out = []
    for cfg in source_cfgs:
        if cfg.get("enabled", True) is False:
            continue
        cls = REGISTRY.get(cfg["type"])
        if cls is None:
            raise ValueError(f"Type de source inconnu : {cfg['type']} (source {cfg['name']})")
        out.append(cls(cfg, secret))
    return out
