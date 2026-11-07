import os
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_PATH = Path(os.getenv("MODEL_PATH", "model.joblib"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "iris-v1")
model = None


class PredictionRequest(BaseModel):
    features: list[float] = Field(min_length=4, max_length=4)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global model
    model = joblib.load(MODEL_PATH)
    yield
    model = None


app = FastAPI(title="Iris classifier", lifespan=lifespan)


@app.get("/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health")
def health() -> dict[str, str]:
    if model is None:
        raise HTTPException(status_code=503, detail="model is not loaded")
    return {"status": "ready", "model_version": MODEL_VERSION}


@app.post("/predict")
def predict(request: PredictionRequest) -> dict[str, str | int]:
    if model is None:
        raise HTTPException(status_code=503, detail="model is not loaded")
    prediction = int(model.predict([request.features])[0])
    return {"class_id": prediction, "model_version": MODEL_VERSION}
