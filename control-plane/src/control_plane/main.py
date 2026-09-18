from fastapi import FastAPI

from control_plane.routers import health, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
