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
from starlette.status import HTTP_424_FAILED_DEPENDENCY
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Gate Service",
    version="1.0.1",
    root_path="/gs"
)

# Compute default certificate path relative to this file
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CA_CERT = os.path.join(BASE_DIR, "mqtt", "iot_mqtt_ca.crt")
CLIENT_CERT = os.path.join(BASE_DIR, "mqtt", "iot_mqtt_client.crt")
CLIENT_KEY = os.path.join(BASE_DIR, "mqtt", "iot_mqtt_client.key")

# Environment variables
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL")
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TLS_PORT = int(os.getenv("MQTT_TLS_PORT", "8883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_ENTRY_TOPIC = os.getenv("MQTT_ENTRY_TOPIC", "parking/gate/entry")
MQTT_EXIT_TOPIC = os.getenv("MQTT_EXIT_TOPIC", "parking/gate/exit")
MQTT_TLS_ENABLED = os.getenv("MQTT_TLS_ENABLED", "true").lower() == "true"

@app.on_event("startup")
async def on_startup():
    await init_db()

@app.post("/api/v1/sessions/entry/", response_model=VehicleEntryResponse)
async def vehicle_entry(entry: VehicleEntryCreate, db: AsyncSession = Depends(get_db)):
    existing = await get_active_session_by_plate(db, entry.plate_number)
    if existing:
        raise HTTPException(status_code=400, detail="Vehicle already inside")

    new_session = await create_parking_session(db, entry.plate_number)

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
            f"{PAYMENT_SERVICE_URL}/ps/api/v1/payments/",
            json={
                "parking_session_id": session_data.id,
                "plate_number": entry.plate_number
            }
        )

    if response.status_code != 200:
        raise HTTPException(status_code=HTTP_424_FAILED_DEPENDENCY, detail="Payment processing failed")

    pay_data = response.json()
    if pay_data.get("status") not in ["success", "already_paid"]:
        raise HTTPException(
            status_code=402,
            detail=f"Payment failed: {pay_data.get('message')}"
        )

    exit_session = await mark_session_exited(db, entry.plate_number)

    asyncio.create_task(publish_mqtt(MQTT_EXIT_TOPIC, "open"))

    return VehicleExitResponse(
        message="Exit recorded, payment successful",
        plate_number=entry.plate_number,
        exit_timestamp=exit_session.exit_timestamp,
        fee=pay_data.get("fee")
    )

async def publish_mqtt(topic: str, message: str):
    try:
        tls_context = None

        if MQTT_TLS_ENABLED:
            logging.info("TLS is enabled. Setting up SSL context.")

            tls_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            tls_context.load_verify_locations(cafile=CA_CERT)
            tls_context.load_cert_chain(certfile=CLIENT_CERT, keyfile=CLIENT_KEY)

        port = MQTT_TLS_PORT if MQTT_TLS_ENABLED else MQTT_PORT
        logging.info(f"Connecting to MQTT broker at {MQTT_HOST}:{port}")

        async with Client(
            hostname=MQTT_HOST,
            port=port,
            username=MQTT_USERNAME,
            password=MQTT_PASSWORD,
            tls_context=tls_context
        ) as client:
            logging.info(f"Publishing message '{message}' to topic '{topic}'")
            await client.publish(topic, message.encode())
            logging.info(f"Successfully published '{message}' to '{topic}'")
    except Exception as e:
        logging.error(f"MQTT publish failed: {e}")

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
