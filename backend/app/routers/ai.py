"""AI document intelligence router.

GET  /ai/status                            — is AI enabled, and with which backend
POST /files/{file_id}/ai/summarize         — return a text summary
POST /files/{file_id}/ai/classify          — suggest document class + metadata
POST /files/{file_id}/ai/ask               — Q&A against the document
POST /files/{file_id}/ai/suggest_workflow  — AI workflow routing suggestion
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import ai_service, metadata_store, workflows_store
from ..access_helpers import to_http
from ..auth import CurrentSession, CurrentUser, get_current_session, get_current_user
from ..storage_providers.base import ProviderError

router = APIRouter(prefix="/files", tags=["ai"])

# Separate, unprefixed router: /ai/status isn't file-scoped, and any
# authenticated user (not just admins/managers) needs it to know whether to
# show the Watson/OpenAI/Ollama branding in the AI panel -- it reveals only
# the active backend NAME, never credentials, so it doesn't need the
# require_feature("manage_admin_settings") gate the way /admin/settings does.
status_router = APIRouter(prefix="/ai", tags=["ai"])


class AiStatusOut(BaseModel):
    enabled: bool
    backend: str


@status_router.get("/status", response_model=AiStatusOut)
def ai_status(_user: CurrentUser = Depends(get_current_user)):
    return AiStatusOut(enabled=ai_service.is_enabled(), backend=ai_service.get_backend_name())


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


class AiSuggestWorkflowOut(BaseModel):
    suggested_workflow_id: str | None = None
    suggested_workflow_name: str | None = None
    confidence: str = "low"  # "high" (NLU) | "medium" (keyword) | "low" (none)


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


@router.post("/{file_id}/ai/suggest_workflow", response_model=AiSuggestWorkflowOut)
def suggest_workflow(file_id: str, session: CurrentSession = Depends(get_current_session)):
    """AI-powered workflow routing: analyse the document and return the most
    appropriate approval workflow definition, or null if none matches.

    The suggestion is purely advisory — the user confirms or overrides before
    any workflow instance is created.  Creating the instance is a separate
    POST /workflows/instances call, unchanged from the existing workflow API.
    """
    content, content_type, name = _get_content_and_info(session, file_id)
    text = ai_service.extract_text(content, content_type, name)

    definitions = workflows_store.list_definitions()
    if not definitions:
        return AiSuggestWorkflowOut()

    workflow_names = [d["name"] for d in definitions]
    suggestion = ai_service.suggest_workflow_with_source(text, None, workflow_names)

    if suggestion.name is None:
        return AiSuggestWorkflowOut()

    # Find the matching definition
    matched = next((d for d in definitions if d["name"] == suggestion.name), None)
    if matched is None:
        return AiSuggestWorkflowOut()

    # "high" only when Watson NLU actually produced this specific match, not
    # merely when NLU credentials happen to be configured -- NLU can be
    # configured but inconclusive for a given document, in which case the
    # keyword fallback is what really found it, and that should read as
    # "medium", not overstate itself as "high".
    confidence = "high" if suggestion.source == "nlu" else "medium"

    return AiSuggestWorkflowOut(
        suggested_workflow_id=matched["id"],
        suggested_workflow_name=matched["name"],
        confidence=confidence,
    )
