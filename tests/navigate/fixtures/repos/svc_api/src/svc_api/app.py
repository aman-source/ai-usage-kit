from fastapi import FastAPI

from . import routes

app = FastAPI()
app.include_router(routes.router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}
