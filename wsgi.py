"""WSGI entrypoint for production servers such as Gunicorn."""

from webapp.app import app

application = app
