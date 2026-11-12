"""FastAPI interface for Coordinator Agent."""
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from .agent import ConsistencyCheckingAgent
from .schema import ConsistencyInput, ConsistencyReport

app = FastAPI(title="Consistency Checking Agent API", version="1.0.0")
agent = ConsistencyCheckingAgent()

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "consistency-checking-agent", "schema_version": "1.0.0"}

@app.post("/check-consistency", response_model=ConsistencyReport)
async def check_consistency(payload: ConsistencyInput):
    try:
        return await agent.run(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Consistency checking failed: {type(exc).__name__}: {exc}") from exc
