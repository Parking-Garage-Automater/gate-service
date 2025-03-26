import uvicorn
import os
import asyncio
import httpx
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import init_db, get_db
from app.schemas import VehicleEntryCreate, VehicleEntryResponse, VehicleExitResponse
from app.crud import (
    get_active_session_by_plate,
    create_parking_session,
    mark_session_exited
)
from aiomqtt import Client
import ssl


app = FastAPI(title="Gate Service", version="1.0.1")

# Environment variables
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL")
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_ENTRY_TOPIC = os.getenv("MQTT_ENTRY_TOPIC", "parking/gate/entry")
MQTT_EXIT_TOPIC = os.getenv("MQTT_EXIT_TOPIC", "parking/gate/exit")


@app.on_event("startup")
async def on_startup():
    await init_db()


@app.post("/api/v1/sessions/entry/", response_model=VehicleEntryResponse)
async def vehicle_entry(entry: VehicleEntryCreate, db: AsyncSession = Depends(get_db)):
    existing = await get_active_session_by_plate(db, entry.plate_number)
    if existing:
        raise HTTPException(status_code=400, detail="Vehicle already inside")

    new_session = await create_parking_session(db, entry.plate_number)

    # MQTT: Trigger entry gate
    asyncio.create_task(publish_mqtt(MQTT_ENTRY_TOPIC, "open"))

    return VehicleEntryResponse(
        message="Vehicle entry recorded",
        plate_number=new_session.license_plate,
        entry_timestamp=new_session.entry_timestamp
    )


@app.put("/api/v1/sessions/exit/", response_model=VehicleExitResponse)
async def vehicle_exit(entry: VehicleEntryCreate, db: AsyncSession = Depends(get_db)):
    session_data = await get_active_session_by_plate(db, entry.plate_number)
    if not session_data:
        raise HTTPException(status_code=400, detail="No active parking session found")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PAYMENT_SERVICE_URL}/api/v1/payments/",
            json={
                "parking_session_id": session_data.id,
                "plate_number": entry.plate_number
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

    # MQTT: Trigger exit gate
    asyncio.create_task(publish_mqtt(MQTT_EXIT_TOPIC, "open"))

    return VehicleExitResponse(
        message="Exit recorded, payment successful",
        plate_number=entry.plate_number,
        exit_timestamp=exit_session.exit_timestamp,
        fee=pay_data.get("fee")
    )

async def publish_mqtt(topic: str, message: str):
    try:
        tls_enabled = os.getenv("MQTT_TLS_ENABLED", "false").lower() == "true"
        ssl_context = None

        if tls_enabled:
            ca_cert = os.getenv("MQTT_CA_CERT")
            client_cert = os.getenv("MQTT_CLIENT_CERT")
            client_key = os.getenv("MQTT_CLIENT_KEY")

            ssl_context = ssl.create_default_context(cafile=ca_cert)

            if client_cert and client_key:
                ssl_context.load_cert_chain(certfile=client_cert, keyfile=client_key)

        async with Client(
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
            ssl_context=ssl_context
        ) as client:
            await client.publish(topic, message.encode())
            print(f"Published MQTT message '{message}' to topic '{topic}'")
    except Exception as e:
        print(f"MQTT publish failed: {e}")



if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
