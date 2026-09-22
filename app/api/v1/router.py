from fastapi import APIRouter

from app.api.v1 import downloads, files

router = APIRouter(prefix="/v1")
router.include_router(files.router)
router.include_router(downloads.router)
