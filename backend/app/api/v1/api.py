from fastapi import APIRouter

from app.api.v1.endpoints import (
    alerts,
    auth,
    broker_dealers,
    chatbot,
    clearing_memberships_admin,
    contacts,
    email_extractor,
    favorite_lists,
    favorites,
    health,
    institutional_investors,
    investment_advisors,
    investors,
    outreach,
    pipeline,
    settings,
    stats,
    users_admin,
    vault,
    vault_files,
    visits,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(broker_dealers.router, tags=["broker-dealers"])
api_router.include_router(investment_advisors.router, tags=["investment-advisors"])
api_router.include_router(
    institutional_investors.router, tags=["institutional-investors"]
)
api_router.include_router(alerts.router, tags=["alerts"])
api_router.include_router(investors.router, tags=["investors"])
api_router.include_router(pipeline.router, tags=["pipeline"])
api_router.include_router(pipeline.scheduled_router, tags=["pipeline"])
api_router.include_router(pipeline.admin_destructive_router, tags=["pipeline"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(stats.router, tags=["stats"])
api_router.include_router(email_extractor.router, tags=["email-extractor"])
api_router.include_router(favorites.router, tags=["favorites"])
api_router.include_router(favorite_lists.router, tags=["favorite-lists"])
api_router.include_router(visits.router, tags=["visits"])
api_router.include_router(vault.router, tags=["vault"])
api_router.include_router(vault_files.router, tags=["vault-files"])
api_router.include_router(outreach.router, tags=["outreach"])
api_router.include_router(contacts.router, tags=["contacts"])
api_router.include_router(users_admin.router, tags=["users-admin"])
api_router.include_router(clearing_memberships_admin.router)
api_router.include_router(chatbot.router, tags=["chatbot"])
