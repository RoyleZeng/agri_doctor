from fastapi import APIRouter

from app.api.v1 import admin, ingredients, issues, lookup, recommend, search
from app.api.v1 import planning
from app.api.v1 import weather
from app.api.v1 import updates

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(planning.router)
api_router.include_router(weather.router)
api_router.include_router(updates.router)
api_router.include_router(lookup.router)
api_router.include_router(search.router)
api_router.include_router(recommend.router)
api_router.include_router(ingredients.router)
api_router.include_router(issues.router)
api_router.include_router(admin.router)

from app.api.v1 import consult, registrations
api_router.include_router(consult.router)
api_router.include_router(registrations.router)

from app.api.v1 import farm_logs
api_router.include_router(farm_logs.router)

from app.api.v1 import precision
api_router.include_router(precision.router)

from app.api.v1 import data_sources
api_router.include_router(data_sources.router)

from app.api.v1 import region
api_router.include_router(region.router)

from app.api.v1 import economics
api_router.include_router(economics.router)

from app.api.v1 import agri_data
api_router.include_router(agri_data.router)

from app.api.v1 import vendors
api_router.include_router(vendors.router)

from app.api.v1 import farm_content
api_router.include_router(farm_content.router)
