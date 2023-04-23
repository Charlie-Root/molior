from sqlalchemy import Column, Integer, ForeignKey, Boolean, String
from sqlalchemy.orm import relationship

from .database import Base
from ..tools import db2array
from ..molior.configuration import Configuration


class Chroot(Base):
    __tablename__ = "chroot"

    id = Column(Integer, primary_key=True)
    build_id = Column(ForeignKey("build.id"))
    basemirror_id = Column(ForeignKey("projectversion.id"))
    basemirror = relationship("ProjectVersion")
    architecture = Column(String)
    ready = Column(Boolean)

    def get_mirror_url(self):
        if self.basemirror.external_repo:
            return self.basemirror.mirror_url
        cfg = Configuration()
        apt_url = cfg.aptly.get("apt_url")
        return f"{apt_url}/{self.basemirror.project.name}/{self.basemirror.name}"

    def get_mirror_keys(self):
        cfg = Configuration()
        apt_url = cfg.aptly.get("apt_url")
        keyfile = cfg.aptly.get("key")
        mirror_keys = f"{apt_url}/{keyfile}"
        if self.basemirror.external_repo and self.basemirror.mirror_keys:
            if self.basemirror.mirror_keys[0].keyurl:
                mirror_keys += f" {self.basemirror.mirror_keys[0].keyurl}"
            elif self.basemirror.mirror_keys[0].keyids:
                mirror_keys += (
                    f" {self.basemirror.mirror_keys[0].keyserver}#"
                    + ",".join(db2array(self.basemirror.mirror_keys[0].keyids))
                )
        return mirror_keys
