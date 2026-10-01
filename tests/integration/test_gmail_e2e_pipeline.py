"""Integration tests for the complete Gmail pipeline, Task Extraction, and Policy Engine compatibility."""

from datetime import datetime, timezone
import json
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.config.settings import Settings
from core.email_analysis.analyzer import EmailAnalyzer
from core.email_analysis.task_extractor import TaskExtractor
from core.llm.mock_adapter import MockLLMAdapter
from core.llm.schemas import LLMResponse
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall
from core.notifications.email_notifier import EmailNotificationService
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType
from integrations.gmail.client import GmailClient
from integrations.gmail.models import GmailAccountProfile
from integrations.gmail.pipeline import GmailProcessingPipeline
from integrations.gmail.storage import EmailStorage
from integrations.gmail.sync import GmailSyncService
from integrations.macos.client import MacBridgeClient


@pytest.fixture
def memory_storage() -> EmailStorage:
    storage = EmailStorage(db_path=":memory:")
    yield storage
    storage.close()


@pytest.mark.asyncio
async def test_full_gmail_e2e_pipeline_and_task_boundary(memory_storage: EmailStorage) -> None:
    # 1. Setup Mock Gmail Client
    mock_gmail_client = AsyncMock(spec=GmailClient)
    mock_gmail_client.get_profile.return_value = GmailAccountProfile(
        email_address="fatih@jarvis.local",
        messages_total=50,
        threads_total=25,
        history_id="hist_100",
    )
    mock_gmail_client.list_messages.return_value = (
        [{"id": "msg_univ_reg", "threadId": "t_reg_1"}],
        None,
    )
    mock_gmail_client.get_message.return_value = {
        "id": "msg_univ_reg",
        "threadId": "t_reg_1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Üniversite Kayıt Yenileme Bildirimi"},
                {"name": "From", "value": "Öğrenci İşleri <ogrenci@univ.edu.tr>"},
                {"name": "To", "value": "fatih@jarvis.local"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 09:00:00 +0300"},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": "w5xuaXZlcnNpdGUga2F5xLF0IGnFn2xlbWxlcmluaXppbiA4IEVraW0gdGFyaWhpbmUga2FkYXIgdGFtYW1sYW5tYXPEsSBnZXJla21la3RlZGlyLg==",
            },
        },
    }

    # 2. Setup Deterministic LLM Analysis
    llm_analysis_json = {
        "category": "education",
        "importance": "HIGH",
        "requires_response": False,
        "contains_task": True,
        "has_deadline": True,
        "has_meeting": False,
        "security_or_payment": False,
        "summary": "Üniversite kayıt işlemlerinin 8 Ekim tarihine kadar tamamlanması gerekiyor.",
        "proposed_action": "Kayıt yenileme için hatırlatıcı oluştur",
        "extracted_task_title": "Üniversite kaydını tamamla",
        "extracted_task_description": "Öğrenci işleri sisteminden 8 Ekim'e kadar kayıt yenileme formunu onayla.",
        "raw_deadline_text": "8 Ekim",
        "parsed_deadline": "2026-10-08T23:59:59+03:00",
        "deadline_confidence": "inferred",
    }
    llm = MockLLMAdapter([
        LLMResponse(content=json.dumps(llm_analysis_json), tool_calls=[], finish_reason="stop", model="mock-qwen")
    ])

    # 3. Assemble Pipeline Components
    sync_service = GmailSyncService(client=mock_gmail_client, storage=memory_storage)
    analyzer = EmailAnalyzer(llm_adapter=llm)
    task_extractor = TaskExtractor()
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    notifier = EmailNotificationService(storage=memory_storage, bridge_client=mock_bridge)

    pipeline = GmailProcessingPipeline(
        sync_service=sync_service,
        analyzer=analyzer,
        task_extractor=task_extractor,
        notifier=notifier,
        storage=memory_storage,
    )

    # 4. Execute Pipeline
    result = await pipeline.run(account="fatih@jarvis.local")

    # 5. Assertions on Execution
    assert result.sync_result.new_count == 1
    assert result.analyzed_count == 1
    assert result.tasks_extracted == 1
    assert result.notifications_sent == 1
    assert len(result.errors) == 0

    # 6. Verify Persistent Storage
    stored_email = await memory_storage.get_email("msg_univ_reg")
    assert stored_email is not None
    assert stored_email.subject == "Üniversite Kayıt Yenileme Bildirimi"

    stored_tasks = await memory_storage.get_task_proposals(status="proposed")
    assert len(stored_tasks) == 1
    task = stored_tasks[0]
    assert task["source_message_id"] == "msg_univ_reg"
    assert task["title"] == "Üniversite kaydını tamamla"
    assert task["priority"] == "HIGH"
    assert task["status"] == "proposed"  # STRICT: remains proposed, not automatically approved
    assert task["deadline"] == "2026-10-08T23:59:59+03:00"

    # 7. Verify Notification Was Dispatched
    assert mock_bridge.call.call_count == 1
    call_name, call_payload = mock_bridge.call.call_args[0]
    assert call_name == "notifications.show"
    assert "Üniversite Kayıt Yenileme Bildirimi" in call_payload["title"]

    # 8. Boundary Verification: Zero Automatic Calendar / Reminder Mutations
    # Milestone 4.1 must NOT automatically call calendar.create_event or reminders.create
    # Verify no mock calls to calendar or reminder endpoints
    for call in mock_bridge.call.call_args_list:
        endpoint = call[0][0]
        assert not endpoint.startswith("calendar.")
        assert not endpoint.startswith("reminders.")

    # 9. Policy Engine Compatibility: If an action is later proposed, it routes through R2 approval
    policy_engine = PolicyEngine()
    simulated_reminder_tool = ToolCall(
        id="call_reminder_from_task",
        name="reminders.create",
        arguments={"title": task["title"], "due_date": task["deadline"]},
    )
    from core.models.tools import ToolDefinition
    from core.policy.risk import PolicyRequest

    reminder_def = ToolDefinition(
        name="reminders.create",
        description="Create a reminder",
        risk_level=RiskLevel.R2_WRITE,
        input_schema={},
        requires_approval=True,
    )
    policy_req = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=reminder_def,
        tool_call=simulated_reminder_tool,
    )
    decision = policy_engine.evaluate(policy_req)
    assert decision.decision == PolicyDecisionType.REQUIRE_APPROVAL
