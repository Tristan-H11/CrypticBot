from typing import Union, List

from PyDrocsid.database import db
from sqlalchemy import Column, BigInteger


class AutoRole(db.Base):
    __tablename__ = "autorole"

    role_id: Union[Column, int] = Column(BigInteger, primary_key=True, unique=True)

    @staticmethod
    def add(role_id: int):
        db.add(AutoRole(role_id=role_id))

    @staticmethod
    def exists(role_id: int) -> bool:
        return db.get(AutoRole, role_id) is not None

    @staticmethod
    def all() -> List[int]:
        return [le.role_id for le in db.query(AutoRole)]

    @staticmethod
    def remove(role_id: int):
        db.delete(db.get(AutoRole, role_id))
