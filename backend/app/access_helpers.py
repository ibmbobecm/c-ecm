import logging

from fastapi import HTTPException

from .storage_providers.base import ProviderError

logger = logging.getLogger("access_helpers")


def to_http(exc: ProviderError) -> HTTPException:
    # The short message (str(exc)) is what reaches the browser — a raw Java
    # stack trace or SOAP fault dump has no business in the UI. The full
    # detail (which can be exactly that) goes to the server log instead,
    # for whoever's actually debugging it.
    message = str(exc)
    if exc.detail and exc.detail != message:
        logger.error("%s\n%s", message, exc.detail)
    return HTTPException(status_code=exc.status_code, detail=message)
