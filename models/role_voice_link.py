from typing import Union

from sqlalchemy import Column, BigInteger

from PyDrocsid.database import db


class RoleVoiceLink(db.Base):
    __tablename__ = "role_voice_link"

    role: Union[Column, int] = Column(BigInteger, primary_key=True)
    voice_channel: Union[Column, int] = Column(BigInteger, primary_key=True)

    @staticmethod
    def create(role: int, voice_channel: int) -> "RoleVoiceLink":
        link = RoleVoiceLink(role=role, voice_channel=voice_channel)

        db.add(link)

        return link
