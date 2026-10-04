"""Task routing and lifecycle tracking for JARVIS' existing tool runtime.

Agent roles are labels over capabilities already provided by the Live session,
action registry, and plugins. This module does not create extra model workers.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from memory.memory_manager import redact_sensitive_text


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    keywords: tuple[str, ...]


AGENTS = (
    AgentSpec("coding", "Coding Agent", "Code generation, project edits, and code review.",
              ("edit_code", "run_tests"), ("dev_agent", "code_helper", "file_processor"),
              ("code", "coding", "python", "program", "debug", "error", "bug", "fix",
               "unit test", "test suite", "run tests")),
    AgentSpec("research", "Research Agent", "Web search and source-backed information retrieval.",
              ("web_search",), ("web_search", "weather_report", "flight_finder", "youtube_video"),
              ("research", "search", "look up", "find information", "latest", "compare")),
    AgentSpec("file", "File Agent", "File inspection, document extraction, and supported file operations.",
              ("read_file", "process_document"), ("file_controller", "file_processor", "desktop_control"),
              ("file", "folder", "document", "pdf", "spreadsheet", "notes", "organize")),
    AgentSpec("vision", "Vision Agent", "Explicit user-requested screen or camera capture.",
              ("capture_screen", "capture_camera"), ("screen_process", "close_camera"),
              ("screen", "camera", "screenshot", "what do you see", "look at")),
    AgentSpec("voice", "Voice Agent", "Live microphone input and assistant audio output.",
              ("live_audio",), (), ("voice", "microphone", "speak", "say aloud", "read aloud")),
    AgentSpec("automation", "Automation Agent", "Supported desktop, device, and reminder actions.",
              ("desktop_actions",), (
                  "computer_control", "computer_settings", "open_app", "reminder",
                  "send_message", "android_control", "phone_control",
              ), ("automate", "automation", "click", "open app", "set reminder", "send")),
    AgentSpec("memory", "Memory Agent", "User-approved persistent memory and local recall.",
              ("approved_memory",), ("save_memory", "recall_memory"),
              ("remember", "memory", "recall", "forget")),
    AgentSpec("system", "System Agent", "Live system metrics and supported monitoring controls.",
              ("system_metrics",), ("system_status", "manage_monitor", "system_monitor"),
              ("system", "cpu", "ram", "memory usage", "performance", "monitor")),
    AgentSpec("security", "Security Agent", "Security review capabilities when available.",
              ("security_review",), ("security_review", "security_scan"),
              ("security", "vulnerability", "audit", "threat")),
    AgentSpec("study", "Study Agent", "Document-based learning and quiz preparation.",
              ("document_study",), ("file_processor", "study_agent", "quiz"),
              ("quiz", "study", "teach", "flashcard", "test me")),
)

_BY_ID = {agent.id: agent for agent in AGENTS}
_PREFERRED_ORDER = ("file", "research", "coding", "vision", "automation",
                    "memory", "system", "study", "voice", "security")
def safe_task_label(text: str, limit: int = 160) -> str:
    """Return a short display label with credential-like values removed."""
    return " ".join(redact_sensitive_text(text).split())[:limit]


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in terms)


def route_agents(text: str, available_tools: set[str]) -> list[str]:
    """Choose supported roles from command wording, preserving workflow order."""
    text = (text or "").strip()
    if not text:
        return []
    available = set(available_tools)
    matched = set()
    for spec in AGENTS:
        supported = spec.id == "voice" or bool(available.intersection(spec.tools))
        if supported and _matches(text, spec.keywords):
            matched.add(spec.id)

    # A document-to-quiz request is a real two-stage path only when the
    # existing document processor is installed; JARVIS creates the quiz from
    # the processor result in the same Live response.
    lowered = text.casefold()
    if ({"pdf", "document", "file"} & set(re.findall(r"[a-z]+", lowered))
            and _matches(text, ("quiz", "flashcard", "test me"))
            and "file_processor" in available):
        matched.update(("file", "study"))

    # Avoid presenting the broad "code" match as separate file work unless
    # the user actually asked to inspect or manipulate files.
    if "coding" in matched and not _matches(
        text, ("file", "folder", "document", "pdf", "spreadsheet")
    ):
        matched.discard("file")

    return [agent_id for agent_id in _PREFERRED_ORDER if agent_id in matched]


@dataclass
class Task:
    id: str
    label: str
    agents: list[str]
    created_at: float = field(default_factory=time.time)
    status: str = "ASSIGNED"
    completed_agents: list[str] = field(default_factory=list)
    failed_agents: list[str] = field(default_factory=list)
    started_agents: list[str] = field(default_factory=list)
    current_agent: str = ""


class AgentOrchestrator:
    """Tracks assigned roles and only reports work observed in real tool calls."""

    def __init__(self, on_event: Callable[[dict], None] | None = None):
        self._on_event = on_event or (lambda _event: None)
        self._tools: set[str] = set()
        self._agents: dict[str, dict] = {}
        self._tasks: list[Task] = []
        self._active: Task | None = None
        self._cancelled_task_ids: set[str] = set()

    def configure(self, tools: set[str]) -> None:
        self._tools = set(tools)
        self._agents = {}
        for spec in AGENTS:
            ready = spec.id == "voice" or bool(self._tools.intersection(spec.tools))
            self._agents[spec.id] = {
                "id": spec.id,
                "name": spec.name,
                "description": spec.description,
                "capabilities": list(spec.capabilities),
                "status": "READY" if ready else "WAITING",
                "available": ready,
                "current_task": "",
                "progress": 0 if ready else 0,
                "last_action": "Ready" if ready else "No matching backend capability",
            }

    def plan(self, text: str) -> list[str]:
        return route_agents(text, self._tools)

    @property
    def has_active_task(self) -> bool:
        return bool(self._active and self._active.status in {
            "ASSIGNED", "WORKING", "WAITING", "CANCELLING",
        })

    @property
    def is_cancelling(self) -> bool:
        return bool(self._active and self._active.status == "CANCELLING")

    def is_terminal(self, task_id: str) -> bool:
        task = next((item for item in self._tasks if item.id == task_id), None)
        return bool(task and task.status in {"COMPLETED", "FAILED", "CANCELLED"})

    @property
    def active_task_id(self) -> str:
        return self._active.id if self._active else ""

    @property
    def awaiting_confirmation(self) -> bool:
        return bool(self._active and self._active.status == "WAITING")
    def begin(self, text: str) -> Task | None:
        agents = self.plan(text)
        if not agents:
            return None
        if self.has_active_task:
            return None
        task = Task(uuid.uuid4().hex[:12], safe_task_label(text), agents)
        self._tasks.append(task)
        self._tasks = self._tasks[-40:]
        self._active = task
        for agent_id in agents:
            state = self._agents[agent_id]
            state.update(status="ASSIGNED", current_task=task.label, progress=0,
                         last_action=f"Assigned task {task.id}")
        self._emit("task.created", task=task)
        for agent_id in agents:
            self._emit("agent.assigned", task=task, agent_id=agent_id)
        return task

    def start_tool(self, tool_name: str, task_id: str | None = None) -> str | None:
        task = self._active
        if (task is None or (task_id and task.id != task_id)
                or task.status not in {"ASSIGNED", "WORKING", "WAITING"}):
            return None
        candidates = [
            agent.id for agent in AGENTS
            if tool_name in agent.tools and agent.id in task.agents
            and self._agents.get(agent.id, {}).get("available")
        ]
        if not candidates:
            return None
        # Prefer a concrete file-processing role when a document is both
        # extracted and used by the study role; study-only tasks still map to
        # the existing processor capability.
        agent_id = next((candidate for candidate in candidates if candidate != "study"),
                        candidates[0])
        state = self._agents[agent_id]
        task.status = "WORKING"
        task.current_agent = agent_id
        if agent_id not in task.started_agents:
            task.started_agents.append(agent_id)
            self._emit("agent.started", task=task, agent_id=agent_id,
                       message=f"Running existing capability: {tool_name}")
        else:
            self._emit("agent.progress", task=task, agent_id=agent_id,
                       message=f"Running existing capability: {tool_name}")
        state.update(status="WORKING", progress=None, last_action=tool_name)
        return agent_id

    def wait_for_confirmation(self, tool_name: str) -> str | None:
        task = self._active
        if task is None or task.status not in {"ASSIGNED", "WORKING", "WAITING"}:
            return None
        candidates = [
            agent.id for agent in AGENTS
            if tool_name in agent.tools and agent.id in task.agents
            and self._agents.get(agent.id, {}).get("available")
        ]
        if not candidates:
            return None
        agent_id = next((candidate for candidate in candidates if candidate != "study"),
                        candidates[0])
        task.status = "WAITING"
        state = self._agents[agent_id]
        state.update(status="WAITING", current_task=task.label, progress=None,
                     last_action="Awaiting explicit user confirmation")
        self._emit("agent.progress", task=task, agent_id=agent_id,
                   message=f"Awaiting user confirmation before {tool_name}.")
        self._emit("task.waiting", task=task, agent_id=agent_id,
                   message="Waiting for explicit user confirmation.")
        return agent_id

    def cancel_active(self) -> bool:
        task = self._active
        if task is None:
            return False
        self._cancelled_task_ids.add(task.id)
        if len(self._cancelled_task_ids) > 40:
            self._cancelled_task_ids.pop()
        task.status = "CANCELLING"
        self._emit("task.cancelling", task=task,
                   message="Stopping further orchestration.")
        current = self._agents.get(task.current_agent, {})
        if current.get("status") != "WORKING":
            self._complete_cancellation(task)
        return True

    def _complete_cancellation(self, task: Task) -> None:
        task.status = "CANCELLED"
        for agent_id in task.agents:
            state = self._agents[agent_id]
            if state["status"] == "ASSIGNED":
                state.update(status="WAITING",
                             last_action="Not started because the task was cancelled")
        self._emit("task.cancelled", task=task,
                   message="Task cancelled; an already-running action may have finished.")
        if self._active is task:
            self._active = None

    def finish_tool(self, tool_name: str, succeeded: bool,
                    task_id: str | None = None) -> None:
        task = self._active
        if task is None or (task_id and task.id != task_id):
            return
        agent_id = next(
            (candidate for candidate in task.started_agents
             if tool_name in _BY_ID[candidate].tools
             and self._agents.get(candidate, {}).get("status") == "WORKING"),
            None,
        )
        state = self._agents.get(agent_id or "")
        if state is None:
            return
        if succeeded:
            if agent_id not in task.completed_agents:
                task.completed_agents.append(agent_id)
            state.update(status="COMPLETED", progress=100,
                         current_task=task.label, last_action=f"Completed {tool_name}")
            self._emit("agent.completed", task=task, agent_id=agent_id,
                       message=f"Capability completed: {tool_name}")
            if task.status == "CANCELLING":
                self._complete_cancellation(task)
                return
        else:
            if agent_id not in task.failed_agents:
                task.failed_agents.append(agent_id)
            state.update(status="FAILED", current_task=task.label,
                         last_action=f"Failed {tool_name}")
            task.status = "FAILED"
            self._emit("agent.failed", task=task, agent_id=agent_id,
                       message=f"Capability failed: {tool_name}")
            for pending_id in task.agents:
                pending = self._agents[pending_id]
                if pending["status"] == "ASSIGNED":
                    pending.update(status="WAITING",
                                   last_action="Not started because a prior step failed")
            self._emit("task.failed", task=task,
                       message=f"Capability failed: {tool_name}")
            self._active = None

    def finish(self, succeeded: bool, message: str = "",
               task_id: str | None = None) -> None:
        task = self._active
        if (task is None or (task_id and task.id != task_id)
                or task.status not in {"ASSIGNED", "WORKING", "WAITING"}):
            return
        if (succeeded and "study" in task.agents
                and ("file" in task.completed_agents or task.agents == ["study"])
                and "study" not in task.started_agents):
            task.started_agents.append("study")
            self._agents["study"].update(
                status="WORKING", progress=None, current_task=task.label,
                last_action="JARVIS is preparing study material from processed content",
            )
            self._emit(
                "agent.started", task=task, agent_id="study",
                message="JARVIS is preparing study material from processed content.",
            )
            task.completed_agents.append("study")
            self._agents["study"].update(
                status="COMPLETED", progress=100,
                last_action="Study material returned by JARVIS",
            )
            self._emit(
                "agent.completed", task=task, agent_id="study",
                message="Study material returned by the existing JARVIS Live session.",
            )
        if not task.started_agents:
            succeeded = False
            message = (
                "No routed backend tool was invoked."
                + (f" {message}" if message else "")
            )
        elif task.failed_agents:
            succeeded = False
        elif set(task.agents) - set(task.completed_agents):
            succeeded = False
            missing = [
                _BY_ID[agent_id].name
                for agent_id in task.agents
                if agent_id not in task.completed_agents
            ]
            message = (
                "Not all assigned capabilities ran; no completion is claimed for: "
                + ", ".join(missing)
            )
        task.status = "COMPLETED" if succeeded else "FAILED"
        event = "task.completed" if succeeded else "task.failed"
        for agent_id in task.agents:
            state = self._agents[agent_id]
            if state["status"] == "ASSIGNED":
                state.update(status="WAITING", last_action="No matching tool call was made")
        self._emit(event, task=task, message=message)
        self._active = None

    def fail_active(self, message: str, task_id: str | None = None) -> None:
        self.finish(False, message, task_id)

    def snapshot(self) -> dict:
        now = time.time()
        tasks = []
        for task in reversed(self._tasks):
            tasks.append({
                "id": task.id,
                "task": task.label,
                "agents": list(task.agents),
                "agent": _BY_ID[task.current_agent].name if task.current_agent else (
                    ", ".join(_BY_ID[a].name for a in task.agents)
                ),
                "status": task.status,
                "progress": min(100, len(task.completed_agents) * 100
                                // max(1, len(task.agents))),
                "start_time": task.created_at,
                "duration": max(0.0, now - task.created_at),
            })
        return {"agents": [dict(agent) for agent in self._agents.values()],
                "tasks": tasks}

    def _emit(self, event_type: str, task: Task, agent_id: str = "",
              message: str = "") -> None:
        event = {
            "type": event_type,
            "task_id": task.id,
            "task": task.label,
            "agent_id": agent_id,
            "agent": _BY_ID[agent_id].name if agent_id in _BY_ID else "",
            "status": task.status,
            "message": safe_task_label(message, 240),
            "ts": time.time(),
        }
        self._on_event(event)
