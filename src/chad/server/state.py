"""Server state management."""

import time

from chad.util.config_manager import ConfigManager
from chad.util.model_catalog import ModelCatalog

# Track server start time for uptime calculation
_start_time: float = 0.0

# Singletons for shared state
_config_manager: ConfigManager | None = None
_model_catalog: ModelCatalog | None = None


def init_start_time() -> None:
    """Initialize the server start time."""
    global _start_time
    _start_time = time.time()


def get_uptime() -> float:
    """Get server uptime in seconds."""
    if _start_time == 0.0:
        return 0.0
    return time.time() - _start_time


def get_config_manager() -> ConfigManager:
    """Get the global ConfigManager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_model_catalog() -> ModelCatalog:
    """Get the global ModelCatalog instance.

    ModelCatalog uses ConfigManager as security_mgr for stored model lookup.
    """
    global _model_catalog
    if _model_catalog is None:
        _model_catalog = ModelCatalog(security_mgr=get_config_manager())
    return _model_catalog


def reset_state() -> None:
    """Reset all global state (for testing)."""
    global _start_time, _config_manager, _model_catalog
    _start_time = 0.0
    _config_manager = None
    _model_catalog = None
