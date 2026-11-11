from fastapi import Header, HTTPException, status

from itmo_queue.core.config import get_settings


async def get_current_admin(x_admin_token: str = Header(None, description="Admin token")):
    settings = get_settings()
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied. Admin rights required."
        )
    return True
