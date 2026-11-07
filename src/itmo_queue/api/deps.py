from fastapi import Header, HTTPException, status

# В реальности тут будет проверка JWT-токена или сессии
async def get_current_admin(x_admin_token: str = Header(None, description="Секретный токен администратора")):
    # В учебных целях: если передан неверный токен, отдаем 403
    if x_admin_token != "super_secret_admin_token":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin rights required."
        )
    return True