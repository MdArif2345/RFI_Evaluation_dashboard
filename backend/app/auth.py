from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, get_settings

security = HTTPBasic()


def require_basic_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.app_username.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.app_password.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="CMC Dashboard"'},
        )
    return credentials.username


def require_prompt_editor(
    settings: Annotated[Settings, Depends(get_settings)],
    x_editor_password: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.prompt_editor_password
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Prompt editing is disabled. Set PROMPT_EDITOR_PASSWORD on the server.",
        )
    if not x_editor_password or not secrets.compare_digest(
        x_editor_password.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wrong editor password.",
        )
