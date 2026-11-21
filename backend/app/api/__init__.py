from fastapi import APIRouter

from app.api.routes import audit, auth, bid_case_reference, bid_catalog, bid_graphic, bid_parse, files, health, invoice, maps, models, ppt, projects, settings, word_format
from app.api.routes import collaboration, invoice_batch, multi_depot, advanced_dispatch, tender_search
from app.modules.knowledge.router import router as knowledge_router

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(models.router, tags=["models"])
api_router.include_router(files.router, tags=["files"])
api_router.include_router(projects.router, tags=["projects"])
api_router.include_router(bid_parse.router, tags=["bid-parse"])
api_router.include_router(bid_case_reference.router, tags=["bid-case-reference"])
api_router.include_router(bid_catalog.router, tags=["bid-catalog"])
api_router.include_router(collaboration.router)
api_router.include_router(bid_graphic.router, tags=["bid-graphic"])
api_router.include_router(ppt.router, tags=["ppt"])
api_router.include_router(audit.router, tags=["audit"])
api_router.include_router(invoice.router, tags=["invoice"])
api_router.include_router(invoice_batch.router, tags=["invoice-batches"])
api_router.include_router(maps.router, tags=["maps"])
api_router.include_router(multi_depot.router, tags=["maps"])
api_router.include_router(advanced_dispatch.router, tags=["maps"])
api_router.include_router(word_format.router, tags=["word-format"])
api_router.include_router(knowledge_router, tags=["knowledge"])

api_router.include_router(tender_search.router, tags=["tender-search"])
