from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime
from app.models import ParkingSession

async def get_active_session_by_plate(db: AsyncSession, plate_number: str):
    result = await db.execute(
        select(ParkingSession).where(
            ParkingSession.license_plate == plate_number,
            ParkingSession.is_active == True
        )
    )
    return result.scalars().first()

async def create_parking_session(db: AsyncSession, plate_number: str):
    new_session = ParkingSession(license_plate=plate_number)
    db.add(new_session)
    await db.flush()
    await db.refresh(new_session)
    return new_session

async def mark_session_exited(db: AsyncSession, plate_number: str):
    session = await get_active_session_by_plate(db, plate_number)
    if not session:
        return None

    session.exit_timestamp = datetime.utcnow()
    session.is_active = False
    await db.flush()
    await db.refresh(session)

    return session
