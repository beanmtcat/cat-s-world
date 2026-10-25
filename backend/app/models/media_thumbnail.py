from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MediaThumbnail(Base):
    """URL mapping also covers legacy images not registered in the media library."""

    __tablename__ = "mmcat_media_thumbnails"

    source_url: Mapped[str] = mapped_column(Text, primary_key=True)
    thumbnail_url: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
