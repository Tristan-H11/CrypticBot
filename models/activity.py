from datetime import datetime
from typing import Union

from PyDrocsid.database import db
from sqlalchemy import Column, BigInteger, DateTime


class Activity(db.Base):
    __tablename__ = "activity"

    user_id: Union[Column, int] = Column(BigInteger, primary_key=True, unique=True)
    last_message: Union[Column, datetime] = Column(DateTime)

    @staticmethod
    def create(user_id: int, last_message: datetime) -> "Activity":
        row = Activity(user_id=user_id, last_message=last_message)
        db.add(row)
        return row

    @staticmethod
    def update(user_id: int, last_message: datetime) -> "Activity":
        if (row := db.get(Activity, user_id)) is None:
            row = Activity.create(user_id, last_message)
        else:
            row.last_message = last_message
        return row
