"""Root conftest — sets test environment before any app module is imported."""

import os

os.environ.setdefault("APP_ENV", "test")
