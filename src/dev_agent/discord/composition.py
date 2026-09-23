"""Composition of the thin Discord adapter with existing Core boundaries.

The composition owns only the resources needed to inject Discord into the
existing control plane.  It does not run a queue, claim Tasks, or start a
second runtime loop; ``RuntimeCoordinator`` remains the execution owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..coordination.protocol import CoordinationConflict, MessageKind, PeerRecord, PeerStatus
from ..coordination.protocol_helpers import validate_identifier
from ..coordination.service import ProcessCoordinationService
from ..human import SQLiteHumanInteractionPort
from ..operation import OperationConfig, OperationService
from ..security.audit import AuditRecorder
from ..state.sqlite_store import SQLiteStateStore
from .adapter import DiscordIngressEvent
from .approval import DiscordApprovalAdapter
from .auth import DiscordAuthorizer
from .binding import SQLiteDiscordBindingStore
from .core import DiscordCoreAdapter
from .human import DiscordHumanAdapter


_COORDINATION_SUBJECT_LIMIT = 3_500


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
            read_status=self.read_status,
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

    def submit_request(self, content: str, event: DiscordIngressEvent):
        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        task = OperationService.submit(self.config, content)
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
        return self.coordination.send_message(
            self.peer,
            recipient_role=self.coordination_recipient_role,
            kind=MessageKind.NOTE,
            subject=_coordination_subject(kind, event),
            correlation_id=f"discord-{message_id}",
            idempotency_key=f"discord-{message_id}",
        )

    def build_approval_adapter(
        self,
        submit: Callable[[str, bool, str], Any],
    ) -> DiscordApprovalAdapter:
        """Return the existing proposal-only Approval adapter for a UI view."""

        return DiscordApprovalAdapter(authorizer=self.authorizer, submit=submit)

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
