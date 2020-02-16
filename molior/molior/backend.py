import asyncio
import importlib

from molior.app import logger
from .configuration import Configuration


class Backend:
    backend = None

    def get_backend(self):
        return Backend.backend

    def init(self, backend_queue):
        cfg = Configuration()
        try:
            plugin = cfg.backend
        except Exception as exc:
            logger.error("please define 'backend' in config")
            logger.exception(exc)
            return None

        logger.info("loading backend: %s", plugin)
        try:
            module = importlib.import_module(".backends.%s" % plugin, package="molior")
            loop = asyncio.get_event_loop()
            Backend.backend = module.backend(backend_queue, loop)
        except Exception as exc:
            logger.error("error loading backend plugin '%s'", plugin)
            logger.exception(exc)
            return None
        return Backend.backend
