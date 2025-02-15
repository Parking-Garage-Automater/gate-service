from pydantic import BaseModel
from datetime import datetime

class VehicleEntryCreate(BaseModel):
    plate_number: str

class VehicleEntryResponse(BaseModel):
    message: str
    plate_number: str
    entry_timestamp: datetime

class VehicleExitResponse(BaseModel):
    message: str
    plate_number: str
    exit_timestamp: datetime
    fee: float
