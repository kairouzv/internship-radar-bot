"""Chargement de la configuration (profil + sources) et des secrets (variables d'environnement)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    profile: dict
    sources: list[dict]
    dir: Path
    env: dict[str, str] = field(default_factory=dict)

    def secret(self, name: str) -> str:
        return (self.env.get(name) or "").strip()


def _as_date(v, name: str) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError as e:
        raise ConfigError(f"profile.yaml: '{name}' doit être une date AAAA-MM-JJ (reçu : {v!r})") from e


def load_config(config_dir: str | Path | None = None, env: dict[str, str] | None = None) -> Config:
    d = Path(config_dir) if config_dir else DEFAULT_DIR
    try:
        profile = yaml.safe_load((d / "profile.yaml").read_text(encoding="utf-8")) or {}
        sources_doc = yaml.safe_load((d / "sources.yaml").read_text(encoding="utf-8")) or {}
    except FileNotFoundError as e:
        raise ConfigError(f"Fichier de configuration introuvable : {e.filename}") from e

    w = profile.get("window") or {}
    for key in ("earliest_start", "latest_start"):
        if key not in w:
            raise ConfigError(f"profile.yaml: window.{key} manquant")
        w[key] = _as_date(w[key], f"window.{key}")
    if w["earliest_start"] > w["latest_start"]:
        raise ConfigError("profile.yaml: window.earliest_start doit précéder window.latest_start")
    w.setdefault("min_months", 4)
    w.setdefault("max_months", 6)
    profile["window"] = w
    profile.setdefault("contract", {})
    profile.setdefault("thresholds", {"notify": 60, "top": 85, "digest": 35})
    profile.setdefault("notifications", {})

    sources = sources_doc.get("sources") or []
    names = set()
    for s in sources:
        if "type" not in s or "name" not in s:
            raise ConfigError(f"sources.yaml: chaque source doit avoir 'name' et 'type' (reçu : {s})")
        if s["name"] in names:
            raise ConfigError(f"sources.yaml: nom de source en double : {s['name']}")
        names.add(s["name"])

    return Config(profile=profile, sources=sources, dir=d, env=dict(os.environ if env is None else env))
