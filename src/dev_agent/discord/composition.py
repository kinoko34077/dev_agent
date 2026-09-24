"""Composition of the thin Discord adapter with existing Core boundaries.

The composition owns only the resources needed to inject Discord into the
existing control plane.  It does not run a queue, claim Tasks, or start a
second runtime loop; ``RuntimeCoordinator`` remains the execution owner.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Callable

from ..coordination.protocol import CoordinationConflict, MessageKind, PeerRecord, PeerStatus
from ..coordination.protocol_helpers import validate_identifier
from ..coordination.service import ProcessCoordinationService
from ..domain.protocol import TaskStatus
from ..human import SQLiteHumanInteractionPort
from ..operation import OperationConfig, OperationError, OperationService
from ..security.audit import AuditRecorder
from ..state.sqlite_store import SQLiteStateStore
from .adapter import DiscordIngressEvent, DiscordMessageKind
from .approval import DiscordApprovalAdapter
from .auth import DiscordAuthorizer
from .binding import SQLiteDiscordBindingStore
from .core import DiscordCoreAdapter
from .human import DiscordHumanAdapter
from .intent import IntentProposal
from .conversation_archive import search_archive
from .history import DiscordHistoryMessage


_COORDINATION_SUBJECT_LIMIT = 3_500
_TERMINAL_TASK_STATES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
_ARCHIVE_REFERENCE_MARKERS = ("前に", "以前", "先週", "この前", "あの時")
_FINAL_MESSAGE_KINDS = frozenset({"FINAL", "TASK_FINAL", "FINAL_RESPONSE"})
_DECISION_MESSAGE_MARKERS = ("HUMAN_REQUEST", "HUMAN_RESPONSE", "APPROVAL")
_PROGRESS_MESSAGE_MARKERS = ("PROGRESS", "STATUS", "COMPLETED")
_ACK_MESSAGE_MARKERS = ("ACK", "CHAT_REPLY", "READ_QUERY_REPLY")


def _coordination_subject(kind: str, event: DiscordIngressEvent) -> str:
    """Build a bounded, secret-sanitized Core mailbox subject.

    The Discord-specific SQLite tables remain pointer/idempotency-only.  The
    existing coordination mailbox receives the operator's bounded, sanitized
    intervention text so the existing runtime can consume it without a
    second Discord queue or conversation store.
    """

    safe = AuditRecorder.sanitize_payload({"content": event.message.content})
    content = safe.get("content", "")
    normalized = " ".join(content.split()) if isinstance(content, str) else ""
    prefix = f"discord:{kind}:{event.message.message_id}"
    if not normalized:
        return prefix
    return f"{prefix}:{normalized}"[:_COORDINATION_SUBJECT_LIMIT]


def _interrupt_objective(event: DiscordIngressEvent) -> str:
    """Return a bounded, secret-sanitized objective for the Core artifact."""

    safe = AuditRecorder.sanitize_payload({"content": event.message.content})
    content = safe.get("content", "")
    normalized = " ".join(content.split()) if isinstance(content, str) else ""
    lowered = normalized.casefold()
    for prefix in ("/interrupt", "割り込み"):
        if lowered.startswith(prefix.casefold()):
            normalized = normalized[len(prefix):].strip()
            break
    return normalized[:_COORDINATION_SUBJECT_LIMIT] or "Discordからの割り込み確認"


class DiscordRuntimeComposition:
    """Inject Discord into existing durable Operation and coordination APIs."""

    def __init__(
        self,
        config: OperationConfig,
        *,
        store: SQLiteStateStore,
        bindings: SQLiteDiscordBindingStore,
        authorizer: DiscordAuthorizer,
        human: DiscordHumanAdapter,
        coordination: ProcessCoordinationService,
        peer: PeerRecord,
        coordination_recipient_role: str,
    ) -> None:
        self.config = config
        self.store = store
        self.bindings = bindings
        self.authorizer = authorizer
        self.human = human
        self.coordination = coordination
        self.peer = peer
        self.coordination_recipient_role = coordination_recipient_role
        self._closed = False
        self.core = DiscordCoreAdapter(
            submit_request=self.submit_request,
            submit_coordination=self.submit_coordination,
            submit_wait=self.submit_wait,
            read_status=self.read_status,
            chat_response=self.chat_response,
        )

    @classmethod
    def open(
        cls,
        config: OperationConfig,
        *,
        authorizer: DiscordAuthorizer,
        coordination_recipient_role: str = "agent",
        revision: str = "working-tree",
        instance_id: str = "discord-ui",
        lease_seconds: int | float = 60.0,
    ) -> "DiscordRuntimeComposition":
        if not isinstance(config, OperationConfig):
            raise TypeError("config must be OperationConfig")
        if not isinstance(authorizer, DiscordAuthorizer):
            raise TypeError("authorizer must be DiscordAuthorizer")
        coordination_recipient_role = validate_identifier(
            coordination_recipient_role,
            "coordination_recipient_role",
        )
        store = SQLiteStateStore(config.state_path)
        coordination = ProcessCoordinationService(data_dir=config.data_dir)
        try:
            peer = coordination.attach_peer(
                "discord-ui",
                revision=revision,
                capabilities=("discord-human-ui", "operation-ingress", "coordination-ingress"),
                instance_id=instance_id,
                lease_seconds=lease_seconds,
            )
            peer = coordination.set_peer_status(peer, PeerStatus.READY)
            bindings = SQLiteDiscordBindingStore(store)
            human = DiscordHumanAdapter(
                SQLiteHumanInteractionPort(store),
                authorizer=authorizer,
                bindings=bindings,
            )
            return cls(
                config,
                store=store,
                bindings=bindings,
                authorizer=authorizer,
                human=human,
                coordination=coordination,
                peer=peer,
                coordination_recipient_role=coordination_recipient_role,
            )
        except BaseException:
            coordination.close()
            store.close()
            raise

    def _heartbeat(self) -> PeerRecord:
        self.peer = self.coordination.heartbeat(self.peer, lease_seconds=60.0)
        return self.peer

    def binding_is_active(self, key) -> bool:
        """Observe Core task state for context-sensitive Discord routing."""
        binding = self.bindings.lookup(key)
        if binding is None:
            return False
        task = self.store.load_task(binding.run_id)
        return task is not None and task.status not in _TERMINAL_TASK_STATES

    def _structured_inputs(self, event: DiscordIngressEvent) -> dict[str, Any]:
        """Build bounded structured hints without constructing a Planner prompt."""

        scope = self.bindings.get_scope(event.binding_key)
        inputs: dict[str, Any] = {}
        if scope is not None:
            scope_hint = {
                "directory": scope.directory_scope,
                "files": list(scope.selected_files),
            }
            if scope_hint["directory"] or scope_hint["files"]:
                inputs["discord_scope"] = scope_hint
        if event.history_context:
            inputs["discord_context"] = {
                "messages": [item.to_dict(include_metadata=True) for item in event.history_context],
            }
        binding = self.bindings.lookup(event.binding_key)
        if (
            binding is not None
            and event.intent_proposal is not None
            and event.intent_proposal.kind.value == "FOLLOW_UP"
        ):
            inputs["discord_follow_up"] = {
                "previous_root_id": binding.root_id,
                "previous_run_id": binding.run_id,
            }
        return inputs

    def chat_response(self, event: DiscordIngressEvent) -> dict[str, str]:
        """Answer read-only chat from bounded context without creating work."""

        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        assistant_items = [
            item for item in event.history_context
            if item.role == "assistant" and item.content.strip()
        ]
        if any(marker in event.message.content for marker in _ARCHIVE_REFERENCE_MARKERS):
            safe = AuditRecorder.sanitize_payload({"content": event.message.content}).get("content", "")
            tokens = re.findall(
                r"[A-Za-z0-9_./-]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}",
                safe if isinstance(safe, str) else "",
            )
            query = max(tokens, key=len) if tokens else " ".join(str(safe).split())[:64]
            if query:
                try:
                    archived = search_archive(
                        self.config.data_dir / "discord-archive",
                        "|".join((event.binding_key.guild_id, event.binding_key.channel_id, event.binding_key.thread_id)),
                        query,
                        limit=5,
                    )
                except (OSError, ValueError, TypeError):
                    archived = ()
                if archived:
                    assistant_items.extend(
                        DiscordHistoryMessage(
                            role="assistant",
                            content=item.content.strip(),
                            message_id=item.message_id,
                            created_at=item.created_at,
                            speaker_id=item.speaker_id,
                            speaker_name=item.speaker_name,
                            reply_to_message_id=item.reply_to_message_id,
                            message_kind=item.message_kind,
                            root_id=item.root_id,
                            run_id=item.run_id,
                        )
                        for item in archived
                        if item.content.strip()
                    )
        def _kind(item: Any) -> str:
            return str(getattr(item, "message_kind", "") or "").upper()

        final_lines = [item.content.strip() for item in assistant_items if _kind(item) in _FINAL_MESSAGE_KINDS]
        decision_lines = [
            item.content.strip()
            for item in assistant_items
            if any(marker in _kind(item) for marker in _DECISION_MESSAGE_MARKERS)
        ]
        progress_lines = [
            item.content.strip()
            for item in assistant_items
            if any(marker in _kind(item) for marker in _PROGRESS_MESSAGE_MARKERS)
        ]
        ack_lines = [
            item.content.strip()
            for item in assistant_items
            if any(marker in _kind(item) for marker in _ACK_MESSAGE_MARKERS)
        ]
        selected = final_lines or decision_lines or progress_lines or ack_lines
        if selected:
            return {
                "state": "CHAT",
                "text": f"会話の記録では、{selected[-1][:800]}",
            }
        binding = self.bindings.lookup(event.binding_key)
        if binding is not None:
            task = self.store.load_task(binding.run_id)
            if task is not None:
                return {
                    "state": "CHAT",
                    "text": f"現在のTaskは「{task.objective[:240]}」で、状態は{task.status.value}です。",
                }
        return {
            "state": "CHAT",
            "text": "この会話内に参照できる完了記録はまだありません。",
        }

    def _discord_context_artifact_refs(self, event: DiscordIngressEvent):
        """Persist bounded Discord context as a Core-owned immutable reference."""

        if not event.history_context:
            return ()
        reference = self.coordination.artifacts.put_json(
            {
                "source": "discord",
                "source_message_id": event.message.message_id,
                "messages": [item.to_dict(include_metadata=True) for item in event.history_context],
            },
            kind="discord_context",
            revision="discord-ui",
        )
        return (reference,)

    def submit_request(self, content: str, event: DiscordIngressEvent):
        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        task = OperationService.submit(
            self.config,
            content,
            inputs=self._structured_inputs(event),
        )
        self.bindings.bind(
            event.binding_key,
            root_id=task.root_task_id or task.task_id,
            run_id=task.task_id,
        )
        return task

    def submit_wait(self, content: str, event: DiscordIngressEvent, proposal: IntentProposal):
        """Create a durable user-delay Task through Operation only."""

        if not isinstance(proposal, IntentProposal) or proposal.delay_seconds is None:
            raise ValueError("WAIT requires a bounded delay proposal")
        binding = self.bindings.lookup(event.binding_key)
        if binding is not None and self.binding_is_active(event.binding_key):
            try:
                return OperationService.request_user_delay(
                    self.config,
                    binding.run_id,
                    proposal.delay_seconds,
                    source=content,
                )
            except (OperationError, KeyError):
                return {
                    "state": "WAIT_DEFERRED",
                    "delay_seconds": proposal.delay_seconds,
                    "reason": "active_run_requires_safe_checkpoint",
                }
            except ValueError:
                return {
                    "state": "WAIT_FAILED",
                    "delay_seconds": proposal.delay_seconds,
                    "reason": "active_task_wait_could_not_be_scheduled",
                }
        task = OperationService.submit_delayed(
            self.config,
            content,
            proposal.delay_seconds,
            inputs=self._structured_inputs(event),
        )
        self.bindings.bind(
            event.binding_key,
            root_id=task.root_task_id or task.task_id,
            run_id=task.task_id,
        )
        return task

    def read_status(self, event: DiscordIngressEvent) -> Mapping[str, Any]:
        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        binding = self.bindings.lookup(event.binding_key)
        if binding is None:
            return {
                "state": "NO_BINDING",
                "next_action": "新しい依頼を送信してください",
            }
        return OperationService.read_status(self.config, binding.root_id)

    def submit_coordination(
        self,
        kind: str,
        _content: str,
        event: DiscordIngressEvent,
    ):
        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        self._heartbeat()
        message_id = event.message.message_id
        binding = self.bindings.lookup(event.binding_key)
        if event.kind is DiscordMessageKind.CANCEL:
            if binding is None:
                return {"state": "NO_BINDING", "next_action": "キャンセル対象がありません"}
            return OperationService.cancel_task_only(self.config, binding.run_id)
        if event.kind is DiscordMessageKind.PARALLEL and binding is not None:
            objective = event.message.content.strip()
            if objective.casefold().startswith("/parallel"):
                objective = objective[len("/parallel") :].strip()
            if not objective:
                objective = "Discordからの並行確認"
            return OperationService.submit_child(
                self.config,
                binding.run_id,
                objective,
                inputs=self._structured_inputs(event),
            )
        if event.kind is DiscordMessageKind.INTERRUPT:
            if binding is None:
                return {"state": "NO_BINDING", "next_action": "割り込み対象がありません"}
            reference = self.coordination.artifacts.put_json(
                {
                    "task_id": binding.run_id,
                    "objective": _interrupt_objective(event),
                    "source": "discord",
                },
                kind="task_interrupt_request",
                revision="discord",
            )
            return self.coordination.send_message(
                self.peer,
                recipient_role=self.coordination_recipient_role,
                kind=MessageKind.INTERRUPT,
                subject=_coordination_subject(kind, event),
                artifact_refs=(reference,),
                correlation_id=f"discord-{message_id}",
                idempotency_key=f"discord-{message_id}",
            )
        mailbox_kind = {
            DiscordMessageKind.NOTE: MessageKind.NOTE,
            DiscordMessageKind.PARALLEL: MessageKind.PARALLEL,
            DiscordMessageKind.INTERRUPT: MessageKind.INTERRUPT,
        }.get(event.kind, MessageKind.NOTE)
        artifact_refs = (
            self._discord_context_artifact_refs(event)
            if event.kind is DiscordMessageKind.NOTE
            else ()
        )
        return self.coordination.send_message(
            self.peer,
            recipient_role=self.coordination_recipient_role,
            kind=mailbox_kind,
            subject=_coordination_subject(kind, event),
            artifact_refs=artifact_refs,
            correlation_id=f"discord-{message_id}",
            idempotency_key=f"discord-{message_id}",
        )

    def build_approval_adapter(
        self,
        submit: Callable[[str, bool, str], Any],
    ) -> DiscordApprovalAdapter:
        """Return the existing proposal-only Approval adapter for a UI view."""

        return DiscordApprovalAdapter(authorizer=self.authorizer, submit=submit)

    def submit_approval(self, approval_id: str, approved: bool, actor: str) -> dict[str, Any]:
        """Delegate a Discord decision to the existing Core approval authority."""

        return OperationService.submit_approval(self.config, approval_id, approved, actor)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self.coordination.detach_peer(self.peer)
            except CoordinationConflict:
                # A newer generation owns the stable UI identity.  Never
                # mutate that newer peer while this process closes.
                pass
        finally:
            self.coordination.close()
            self.store.close()

    def __enter__(self) -> "DiscordRuntimeComposition":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["DiscordRuntimeComposition"]
