"""AWS Lambda adapter for the shared FastAPI application."""

from mangum import Mangum

from adaptsg.web_api import app

handler = Mangum(app, lifespan="off")
