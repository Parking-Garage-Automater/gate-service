import uvicorn
import os
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import init_db, get_db
from app.schemas import VehicleEntryCreate, VehicleEntryResponse, VehicleExitResponse
from datetime import datetime
from app.crud import (
    get_active_session_by_plate,
    create_parking_session,
    mark_session_exited
)
import httpx

app = FastAPI(title="Gate Service", version="1.0.0")

PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL")

@app.on_event("startup")
async def on_startup():
    await init_db()

@app.post("/api/v1/sessions/entry/", response_model=VehicleEntryResponse)
async def vehicle_entry(entry: VehicleEntryCreate, db: AsyncSession = Depends(get_db)):
    existing = await get_active_session_by_plate(db, entry.plate_number)
    if existing:
        raise HTTPException(status_code=400, detail="Vehicle already inside")

    new_session = await create_parking_session(db, entry.plate_number)

    return VehicleEntryResponse(
        message="Vehicle entry recorded",
        plate_number=new_session.license_plate,
        entry_timestamp=new_session.entry_timestamp
    )

@app.put("/api/v1/sessions/exit/", response_model=VehicleExitResponse)
async def vehicle_exit(entry: VehicleEntryCreate, db: AsyncSession = Depends(get_db)):
    """Handles vehicle exit by requesting payment and marking session as exited."""

    session_data = await get_active_session_by_plate(db, entry.plate_number)
    if not session_data:
        raise HTTPException(status_code=400, detail="No active parking session found")

    # Request fee calculation and payment processing from Payment Service
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYMENT_SERVICE_URL}/api/v1/payments/",
            json={
                "parking_session_id": session_data.id,
                "plate_number": entry.plate_number  # Ensure Payment Service knows which car
            }
        )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Payment processing failed")

    pay_data = response.json()
    if pay_data.get("status") not in ["success", "already_paid"]:
        raise HTTPException(
            status_code=402,
            detail=f"Payment failed: {pay_data.get('message')}"
        )

    exit_session = await mark_session_exited(db, entry.plate_number)

    return VehicleExitResponse(
        message="Exit recorded, payment successful",
        plate_number=entry.plate_number,
        exit_timestamp=exit_session.exit_timestamp,
        fee=pay_data.get("fee")
    )

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
