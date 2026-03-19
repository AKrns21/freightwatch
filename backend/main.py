"""Entry point — imports from app package.

Start with:
    uvicorn main:app --reload --port 4000
or:
    uvicorn app.main:app --reload --port 4000
"""

from app.main import app  # noqa: F401
