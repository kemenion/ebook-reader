"""Application layer: QML controller, settings persistence and startup wiring."""

from __future__ import annotations

from .settings_store import BookState, SettingsStore, config_dir, config_path

__all__ = ["BookState", "SettingsStore", "config_dir", "config_path"]
