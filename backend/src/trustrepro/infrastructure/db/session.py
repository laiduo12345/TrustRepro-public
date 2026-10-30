from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trustrepro.core.config import Settings


def create_session_factory(settings: Settings):
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
