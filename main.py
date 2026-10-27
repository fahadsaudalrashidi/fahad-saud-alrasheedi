"""Production entry point for the FastAPI application."""

from support_backend.main import app

__all__ = ["app"]

if __name__ == "__main__":
    import os
    import uvicorn

    uvicorn.run(
        "support_backend.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
    )
