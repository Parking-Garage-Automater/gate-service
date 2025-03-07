from sqlalchemy import Column, Integer, String, Boolean, TIMESTAMP
from datetime import datetime
from app.database import Base

class ParkingSession(Base):
    __tablename__ = "parking_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    license_plate = Column(String(20), nullable=False)
    entry_timestamp = Column(TIMESTAMP, default=datetime.utcnow)
    exit_timestamp = Column(TIMESTAMP, nullable=True)
    is_active = Column(Boolean, default=True)

