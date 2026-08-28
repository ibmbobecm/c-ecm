"""AI document intelligence router.

POST /files/{file_id}/ai/summarize  — return a text summary
POST /files/{file_id}/ai/classify   — suggest document class + metadata
POST /files/{file_id}/ai/ask        — Q&A against the document
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import ai_service, metadata_store
from ..access_helpers import to_http
from ..auth import CurrentSession, get_current_session
from ..storage_providers.base import ProviderError

router = APIRouter(prefix="/files", tags=["ai"])


class AiSummaryOut(BaseModel):
    summary: str


class AiClassifyOut(BaseModel):
    suggested_class_id: str | None = None
    suggested_values: dict = {}
    summary: str = ""


class AiAskRequest(BaseModel):
    question: str


class AiAskOut(BaseModel):
    answer: str


def _get_content_and_info(session: CurrentSession, file_id: str) -> tuple[bytes, str | None, str]:
    try:
        info = session.provider.get_file(session.creds, file_id)
        content = session.provider.get_content(session.creds, file_id)
    except ProviderError as exc:
        raise to_http(exc)
    return content, info.content_type, info.name


@router.post("/{file_id}/ai/summarize", response_model=AiSummaryOut)
def summarize(file_id: str, session: CurrentSession = Depends(get_current_session)):
    if not ai_service.is_enabled():
        raise HTTPException(status_code=503, detail="AI is not configured on this server (set FD_AI_BACKEND)")
    content, content_type, name = _get_content_and_info(session, file_id)
    text = ai_service.extract_text(content, content_type, name)
    summary = ai_service.summarize(text)
    return AiSummaryOut(summary=summary or "Could not generate a summary for this document.")


@router.post("/{file_id}/ai/classify", response_model=AiClassifyOut)
def classify(file_id: str, session: CurrentSession = Depends(get_current_session)):
    if not ai_service.is_enabled():
        raise HTTPException(status_code=503, detail="AI is not configured on this server (set FD_AI_BACKEND)")
    content, content_type, name = _get_content_and_info(session, file_id)
    text = ai_service.extract_text(content, content_type, name)

    # Try to match against known document classes
    classes = metadata_store.list_classes()
    best_class_id: str | None = None
    best_values: dict = {}

    if classes and text:
        # Use the first class with matching fields as a heuristic; a real
        # classifier would score all classes
        for cls in classes:
            if cls["fields"]:
                values = ai_service.suggest_metadata(text, cls["fields"])
                if values:
                    best_class_id = cls["id"]
                    best_values = values
                    break

    summary = ai_service.summarize(text)
    return AiClassifyOut(suggested_class_id=best_class_id, suggested_values=best_values, summary=summary)


@router.post("/{file_id}/ai/ask", response_model=AiAskOut)
def ask(file_id: str, req: AiAskRequest, session: CurrentSession = Depends(get_current_session)):
    if not ai_service.is_enabled():
        raise HTTPException(status_code=503, detail="AI is not configured on this server (set FD_AI_BACKEND)")
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    content, content_type, name = _get_content_and_info(session, file_id)
    text = ai_service.extract_text(content, content_type, name)
    answer = ai_service.answer_question(text, req.question)
    return AiAskOut(answer=answer)
