"""FastAPI service. Exposes core over HTTP. Contains no analysis logic."""

from fastapi import FastAPI

from pdf2sdmx.api.routes import router

app = FastAPI(
    title="pdf2sdmx",
    description="Turn tables printed in a statistical PDF report into validated SDMX data.",
    version="0.1.0",
)

app.include_router(router)
