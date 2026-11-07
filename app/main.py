"""Assemble the FastAPI application without changing the public API contract."""

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.api.errors import register_exception_handlers
from app.api.routes import router
from app.lifecycle import lifespan


def custom_generate_unique_id(route: APIRoute) -> str:
    return route.name


app = FastAPI(lifespan=lifespan, generate_unique_id_function=custom_generate_unique_id)


register_exception_handlers(app)


app.include_router(router)
