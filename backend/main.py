from fastapi import FastAPI

app = FastAPI(
    title="FreightWatch API",
    description="Freight cost analysis platform",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
