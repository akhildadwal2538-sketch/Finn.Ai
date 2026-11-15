"""Vercel Python Functions entry point for the FastAPI application."""

try:
    from .main import app
except ImportError:
    from main import app

