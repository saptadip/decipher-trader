from fastapi import FastAPI

from control_plane.routers import audit, health, kill, metrics, strategies

app = FastAPI(title="decipher-trader control-plane")
app.include_router(health.router)
app.include_router(strategies.router)
app.include_router(kill.router)
app.include_router(metrics.router)
app.include_router(audit.router)
