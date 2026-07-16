import copy
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Message:
    """Represents a conversation message. Immutable by design."""
    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentState:
    """Immutable representation of the complete agent state at a specific instant."""
    messages: List[Message] = field(default_factory=list)
    current_task: Optional[str] = None
    execution_plan: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: int = 0


class StateStore:
    """
    Thread-safe store managing the lifecycle of an Agent's state.
    Ensures state transitions are atomic, versioned, and reversible.
    """
    def __init__(self, initial_state: Optional[AgentState] = None) -> None:
        self._lock = threading.Lock()
        self._current_state = initial_state or AgentState()
        self._history: List[AgentState] = [self._current_state]

    def get_state(self) -> AgentState:
        """Retrieves the current immutable snapshot of the agent state."""
        with self._lock:
            return self._current_state

    def update(self, **kwargs: Any) -> AgentState:
        """
        Applies a functional update to transition the state.
        
        Args:
            **kwargs: State attributes to update. Lists and dicts are deep-copied.
            
        Returns:
            The new transitioned AgentState.
        """
        with self._lock:
            # Extract current state fields
            messages = self._current_state.messages
            current_task = self._current_state.current_task
            execution_plan = self._current_state.execution_plan
            metadata = self._current_state.metadata

            # Process updates with copy-on-write pattern to prevent pointer leaks
            if "messages" in kwargs:
                raw_messages = kwargs["messages"]
                messages = [
                    m if isinstance(m, Message) else Message(**m)
                    for m in raw_messages
                ]
            if "current_task" in kwargs:
                current_task = kwargs["current_task"]
            if "execution_plan" in kwargs:
                execution_plan = copy.deepcopy(kwargs["execution_plan"])
            if "metadata" in kwargs:
                # Merge metadata updates rather than replacing entirely
                merged_meta = copy.deepcopy(metadata)
                merged_meta.update(copy.deepcopy(kwargs["metadata"]))
                metadata = merged_meta

            new_version = self._current_state.version + 1
            new_state = AgentState(
                messages=messages,
                current_task=current_task,
                execution_plan=execution_plan,
                metadata=metadata,
                version=new_version
            )

            # Update pointer and append to history log for audit and rollback capabilities
            self._current_state = new_state
            self._history.append(new_state)
            return new_state

    def rollback(self, target_version: int) -> AgentState:
        """
        Reverts the agent state to a previous version.
        Rollback is recorded as a new state transition version to preserve audit trail.
        
        Args:
            target_version: The version number to roll back to.
            
        Returns:
            The new transitioned state (with state matching target_version but incremented version number).
        """
        with self._lock:
            if target_version < 0 or target_version >= len(self._history):
                raise ValueError(
                    f"Invalid target version {target_version}. History contains "
                    f"versions 0 to {len(self._history) - 1}."
                )

            historical_state = self._history[target_version]
            new_version = self._current_state.version + 1

            new_state = AgentState(
                messages=list(historical_state.messages),
                current_task=historical_state.current_task,
                execution_plan=list(historical_state.execution_plan),
                metadata=copy.deepcopy(historical_state.metadata),
                version=new_version
            )

            self._current_state = new_state
            self._history.append(new_state)
            return new_state

    def get_history(self) -> List[AgentState]:
        """Returns a snapshot copy of the full transaction log history."""
        with self._lock:
            return list(self._history)
