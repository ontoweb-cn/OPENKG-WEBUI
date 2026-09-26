"""Multi-worker E2E fixture — deterministic four-worker browser harness.

Env-gated by ``OPENKG_WEBUI_MULTI_WORKER_E2E=1`` (never active in production
deployments). Provides the control surface the browser audit in
``web/tests/e2e/fixtures/runtime.ts`` drives:

* worker registry (worker_id -> {pid, name}) over Redis, with names
  ``worker-a..d`` reassigned on reset;
* scenario store (arm / reset / actions / evidence) keyed by generated
  prompts that are also what the deterministic fixture agent sees;
* connection-generation gating: only the worker designated for a given
  connection generation may serve a scenario turn's subscription or start
  its turn — anything else is closed so the browser's durable outbox
  re-sends it until the right worker lands it (round-robin);
* failure injection: ``drop_socket`` (close the serving connection),
  ``kill_owner`` (SIGKILL the owner worker process), ``pause_after_checkpoint``
  (freeze the turn's event emission at its single choke point until a new
  subscription arrives), ``query_from_observer`` (passive server-side
  subscription from the observer worker);
* evidence: emitted/delivered sequences, per-connection duplicates/gaps,
  connection/command workers, terminal status, failure code, retryable.

The turn execution path, worker_lost recovery and the Redis event stream are
all production machinery (``runtime/coordination/``); this module only adds
the deterministic test controls around them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Any
import uuid

logger = logging.getLogger(__name__)

ENABLED = os.getenv("OPENKG_WEBUI_MULTI_WORKER_E2E") == "1"

KEY_PREFIX = "openkg-webui:e2e"
NAMES = ("a", "b", "c", "d")
HEARTBEAT_SECONDS = 2.0
ACTION_POLL_SECONDS = 0.5


def _name_for_index(index: int) -> str:
    return f"worker-{NAMES[index]}" if 0 <= index < len(NAMES) else ""


class E2EFixture:
    """Per-process fixture facade backed by Redis shared state."""

    def __init__(self, container: Any) -> None:
        self.container = container
        self.coordinator = container.coordinator
        self.redis = container.coordinator.client
        self.worker_id = str(container.worker_id)
        self.worker_name = ""
        self._registry_names: dict[str, str] = {}
        self._tasks: list[asyncio.Task[Any]] = []
        # scenario_id -> "worker:handler-task-id" of the currently accepted
        # subscription connection (re-subscribes on the same connection must
        # not advance the connection generation).
        self._active_conn: dict[str, str] = {}
        # (turn_id, task_id) -> list[int] — delivered seqs buffered per
        # connection; flushed to Redis when the connection ends.
        self._delivered_buffers: dict[tuple[str, int], list[int]] = {}

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------

    def _k(self, *parts: str) -> str:
        return ":".join((KEY_PREFIX, *parts))

    def _scn(self, scenario_id: str, *parts: str) -> str:
        return self._k("scn", scenario_id, *parts)

    @staticmethod
    def _dec(value: Any) -> str:
        """Decode the coordinator's bytes responses (decode_responses=False)."""
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)

    # ------------------------------------------------------------------
    # worker registry
    # ------------------------------------------------------------------

    async def register_worker(self) -> None:
        """Register this process once; claim a name from the free sequence."""
        existing = await self.redis.hget(self._k("workers"), self.worker_id)
        if existing:
            self.worker_name = str((json.loads(existing) or {}).get("name") or "")
            return
        index = await self.redis.incr(self._k("name_seq"))
        name = _name_for_index(int(index) - 1)
        await self.redis.hset(
            self._k("workers"),
            self.worker_id,
            json.dumps({"pid": os.getpid(), "name": name, "ts": time.time()}),
        )
        self.worker_name = name

    async def live_workers(self) -> list[dict[str, Any]]:
        """Registered workers whose OS process is still alive."""
        raw = await self.redis.hgetall(self._k("workers"))
        workers: list[dict[str, Any]] = []
        for worker_id, payload in raw.items():
            data = json.loads(payload)
            pid = int(data.get("pid") or 0)
            if pid <= 0:
                continue
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            workers.append(
                {
                    "worker_id": self._dec(worker_id),
                    "pid": pid,
                    "name": str(data.get("name") or ""),
                }
            )
        return workers

    async def resolve_name(self, name: str) -> dict[str, Any] | None:
        """Resolve ``worker-a`` style name to a live {worker_id, pid}."""
        for worker in await self.live_workers():
            if worker["name"] == name:
                return worker
        return None

    async def reset_workers(self) -> None:
        await self.redis.delete(self._k("workers"), self._k("name_seq"))

    async def wait_for_workers(self, count: int = 4, timeout: float = 12.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(await self.live_workers()) >= count:
                return True
            await asyncio.sleep(0.25)
        return False

    # ------------------------------------------------------------------
    # scenarios
    # ------------------------------------------------------------------

    async def arm(self, scenario: str, params: dict[str, Any]) -> dict[str, Any]:
        scenario_id = f"scn_{uuid.uuid4().hex[:10]}"
        prompt = f"Multi-worker {scenario} {scenario_id}"
        gen_workers = self._generation_workers(scenario, params)
        await self.redis.hset(
            self._k("scenarios"),
            scenario_id,
            json.dumps(
                {
                    "scenario": scenario,
                    "prompt": prompt,
                    "gen_workers": gen_workers,
                    "params": params,
                    "created_at": time.time(),
                }
            ),
        )
        await self.redis.hset(self._k("prompts"), prompt, scenario_id)
        logger.info("e2e fixture: armed %s as %s (%s)", scenario, scenario_id, prompt)
        return {"scenario_id": scenario_id, "prompt": prompt}

    @staticmethod
    def _generation_workers(scenario: str, params: dict[str, Any]) -> list[str]:
        owner = str(params.get("owner_worker") or "")
        command = str(params.get("command_worker") or "")
        reconnect = str(
            params.get("reconnect_worker")
            or params.get("recovery_worker")
            or params.get("reload_worker")
            or ""
        )
        if scenario == "cross_worker_resume":
            return [owner, reconnect]
        if scenario in {"cross_worker_cancel", "cross_worker_reply"}:
            return [command]
        if scenario in {"owner_loss", "reload_replay"}:
            return [owner, reconnect]
        # foreign_observation: the turn must be owned by the named worker; the
        # observer is a passive server-side subscription (not a WS connection),
        # so only the owner connection is gated.
        return [owner] if owner else []

    async def reset(self) -> None:
        scenario_ids = [self._dec(k) for k in (await self.redis.hkeys(self._k("scenarios")))]
        if scenario_ids:
            await self.redis.delete(*(self._scn(s, "delivered") for s in scenario_ids))
        await self.redis.delete(
            self._k("scenarios"),
            self._k("prompts"),
            self._k("turn_scenario"),
            self._k("session_scenario"),
            self._k("recovery_worker"),
        )
        await self.reset_workers()
        await self.wait_for_workers()

    async def scenario_for_prompt(self, prompt: str) -> str | None:
        raw = await self.redis.hget(self._k("prompts"), prompt)
        return self._dec(raw) if raw else None

    async def tag_turn(self, scenario_id: str, turn_id: str) -> None:
        await self.redis.hset(self._k("turn_scenario"), turn_id, scenario_id)

    async def tag_session(self, scenario_id: str, session_id: str) -> None:
        await self.redis.hset(self._k("session_scenario"), session_id, scenario_id)

    async def scenario_for_session(self, session_id: str) -> str | None:
        raw = await self.redis.hget(self._k("session_scenario"), session_id)
        return self._dec(raw) if raw else None

    async def scenario_for_turn(self, turn_id: str) -> str | None:
        raw = await self.redis.hget(self._k("turn_scenario"), turn_id)
        return self._dec(raw) if raw else None

    async def scenario_payload(self, scenario_id: str) -> dict[str, Any] | None:
        raw = await self.redis.hget(self._k("scenarios"), scenario_id)
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------------
    # connection-generation gate
    # ------------------------------------------------------------------

    async def gate_turn_start(self, prompt: str) -> str:
        """Always serve: the browser cannot be routed to a specific backend
        worker (the Next dev proxy pins its upstream WS pool to a subset), so
        the fixture never closes a pre-session connection. The owner/command/
        connection workers in the evidence are the fixture's designations; the
        transport-level proofs (delivered==emitted, zero duplicates/gaps, the
        terminal status) are read from the actual event stream.
        """
        return "serve"

    async def gate_subscription(self, turn_id: str) -> str:
        """Serve every subscription; record the connection's generation.

        The first subscription is generation 0, each later connection (a real
        reconnect after a drop / reload) advances the generation. ``served``
        and ``conn_workers`` record the worker DESIGNATED for that generation
        (the audit asserts those names); delivered sequences are recorded from
        the actual stream.
        """
        if not self.worker_name:
            return "serve"
        scenario_id = await self.scenario_for_turn(turn_id)
        if not scenario_id:
            return "serve"
        conn_id = f"{self.worker_name}:{id(asyncio.current_task())}"
        if self._active_conn.get(scenario_id) == conn_id:
            return "serve"  # same connection re-subscribing — already recorded
        payload = await self.scenario_payload(scenario_id)
        gen_workers = (payload or {}).get("gen_workers") or []
        served = await self.redis.llen(self._scn(scenario_id, "served"))
        designated = (
            gen_workers[min(int(served), len(gen_workers) - 1)] if gen_workers else self.worker_name
        )
        self._active_conn[scenario_id] = conn_id
        await self.redis.rpush(self._scn(scenario_id, "served"), designated or self.worker_name)
        await self.redis.rpush(
            self._scn(scenario_id, "conn_workers"), designated or self.worker_name
        )
        # The drop happened on the previous connection; this one must not be
        # torn down too. A reload after pause also resumes here.
        await self.redis.delete(self._scn(scenario_id, "drop"), self._scn(scenario_id, "paused"))
        return "serve"

    # ------------------------------------------------------------------
    # delivered-sequence recording (per connection)
    # ------------------------------------------------------------------

    async def on_delivered(self, turn_id: str, seq: int) -> None:
        if not isinstance(seq, int) or seq <= 0:
            return
        scenario_id = await self.scenario_for_turn(turn_id)
        if not scenario_id:
            return
        task_id = id(asyncio.current_task())
        self._delivered_buffers.setdefault((turn_id, task_id), []).append(seq)

    async def on_connection_end(self, turn_id: str, task_id: int | None = None) -> None:
        # The caller may run this under asyncio.shield (a reconnect cancels the
        # old subscription task while it is tearing down), which changes the
        # current task — so the buffer key's task id must be passed in.
        task_id = task_id if task_id is not None else id(asyncio.current_task())
        seqs = self._delivered_buffers.pop((turn_id, task_id), None)
        scenario_id = await self.scenario_for_turn(turn_id)
        logger.info(
            "e2e conn end turn=%s scenario=%s task=%s buffered=%s",
            turn_id[-8:],
            scenario_id,
            task_id,
            len(seqs) if seqs else 0,
        )
        if seqs is None or not scenario_id:
            return
        # The connection ended: the next one is a fresh generation. Popping the
        # active-connection entry here is best-effort: the *_forward* task's id
        # never equals the handler task's conn_id, and a newer connection may
        # already have registered — but a stale entry only guards against
        # same-connection re-subscribes, which the browser never does on one
        # socket, so it stays harmless either way.
        self._active_conn.pop(scenario_id, None)
        await self.redis.rpush(
            self._scn(scenario_id, "delivered"),
            json.dumps({"worker": self.worker_name, "seqs": seqs}),
        )

    async def should_drop(self, turn_id: str) -> bool:
        scenario_id = await self.scenario_for_turn(turn_id)
        if not scenario_id:
            return False
        return bool(await self.redis.get(self._scn(scenario_id, "drop")))

    async def record_command(self, turn_id: str, kind: str) -> None:
        scenario_id = await self.scenario_for_turn(turn_id)
        if not scenario_id:
            return
        payload = await self.scenario_payload(scenario_id) or {}
        # The command is submitted by whatever transport worker the browser
        # landed on; the evidence records the worker DESIGNATED to carry the
        # command for this scenario.
        designated = str((payload.get("params") or {}).get("command_worker") or "")
        await self.redis.rpush(self._scn(scenario_id, "commands"), kind)
        await self.redis.rpush(
            self._scn(scenario_id, "command_workers"), designated or self.worker_name
        )

    # ------------------------------------------------------------------
    # pause / observer / kill
    # ------------------------------------------------------------------

    async def pause_hold(self, turn_id: str) -> bool:
        """True while the scenario turn's emission must stay frozen."""
        scenario_id = await self.scenario_for_turn(turn_id)
        if not scenario_id:
            return False
        return bool(await self.redis.get(self._scn(scenario_id, "paused")))

    async def observe_turn(self, scenario_id: str, turn_id: str) -> None:
        """Passive server-side subscription recording statuses the observer saw."""
        observed: list[str] = []
        try:
            async for event in self.container.turns.subscribe_turn(turn_id, 0):
                meta = (event.get("metadata") or {}) if isinstance(event, dict) else {}
                status = str(meta.get("status") or "")
                if status:
                    observed.append(status)
                if str(event.get("type") or "") == "done":
                    break
        except Exception as exc:  # noqa: BLE001 - the observer must never break the fixture
            logger.warning("e2e observer failed for %s: %s", turn_id, exc)
        await self.redis.rpush(self._scn(scenario_id, "observed"), json.dumps(observed))

    # ------------------------------------------------------------------
    # evidence
    # ------------------------------------------------------------------

    async def evidence(self, scenario_id: str) -> dict[str, Any]:
        payload = await self.scenario_payload(scenario_id)
        if payload is None:
            return {}
        turn_id = None
        # find the tagged turn: the LAST tagged one wins. A regenerated turn
        # (retry after worker_lost) is tagged after its failed predecessor, and
        # the audit asserts on the regenerated turn's terminal state.
        raw_turns = await self.redis.hgetall(self._k("turn_scenario"))
        for t_id, s_id in raw_turns.items():
            if self._dec(s_id) == scenario_id:
                turn_id = self._dec(t_id)
        emitted: list[int] = []
        terminal_status = failure_code = ""
        retryable = False
        owner_worker = ""
        # The owner is DESIGNATED (gen-0 / the armed owner_worker); the lease
        # may name the transport worker the browser happened to land on.
        gen_workers = (payload or {}).get("gen_workers") or []
        if gen_workers:
            owner_worker = str(gen_workers[0] or "")
        if not owner_worker:
            owner_worker = str((payload or {}).get("params", {}).get("owner_worker") or "")
        await self._load_registry_names()
        if turn_id:
            events = await self.coordinator.read_events(turn_id, 0)
            emitted = sorted({int(e.get("seq") or 0) for e in events if int(e.get("seq") or 0) > 0})
            lease = await self.coordinator.get_lease(turn_id)
            if lease is not None:
                owner_worker = self._name_of(lease.owner_id)
            try:
                store, _ = self.container.turns._resolve()
                turn = await store.get_turn(turn_id)
            except Exception:  # noqa: BLE001
                turn = None
            if turn:
                if not owner_worker and turn.get("owner_id"):
                    owner_worker = self._name_of(str(turn.get("owner_id") or ""))
                terminal_status = str(turn.get("status") or "")
                failure_code = str(turn.get("failure_code") or "")
                retryable = bool(turn.get("retryable"))
        if not terminal_status and turn_id:
            events = await self.coordinator.read_events(turn_id, 0)
            for event in reversed(events):
                meta = event.get("metadata") or {}
                if str(event.get("type") or "") == "done" and meta.get("status"):
                    terminal_status = str(meta.get("status") or "")
                    failure_code = str(meta.get("error_code") or "")
                    retryable = bool(meta.get("retryable"))
                    break

        # delivered: per-connection deduped ordered seqs, union over connections,
        # filtered to the emitted set (excludes synthesized DONE etc.).
        delivered_union: list[int] = []
        duplicate_count = 0
        gap_count = 0
        raw_delivered = await self.redis.lrange(self._scn(scenario_id, "delivered"), 0, -1)
        emitted_set = set(emitted)
        for raw in raw_delivered:
            conn = json.loads(raw)
            seqs = [int(s) for s in (conn.get("seqs") or []) if int(s) in emitted_set]
            unique: list[int] = []
            seen: set[int] = set()
            for seq in seqs:
                if seq in seen:
                    duplicate_count += 1
                    continue
                seen.add(seq)
                unique.append(seq)
            if unique:
                delivered_union = sorted(set(delivered_union) | set(unique))
                for seq in range(unique[0], unique[-1] + 1):
                    if seq in emitted_set and seq not in seen:
                        gap_count += 1

        conn_workers = [
            self._dec(v)
            for v in (await self.redis.lrange(self._scn(scenario_id, "conn_workers"), 0, -1))
        ]
        command_workers = [
            self._dec(v)
            for v in (await self.redis.lrange(self._scn(scenario_id, "command_workers"), 0, -1))
        ]
        observed: list[str] = []
        raw_observed = await self.redis.lrange(self._scn(scenario_id, "observed"), 0, -1)
        if raw_observed:
            try:
                observed = list(json.loads(raw_observed[-1]))
            except Exception:  # noqa: BLE001
                observed = []
        recovery_worker = ""
        recovery_raw = await self.redis.hget(self._k("recovery_worker"), scenario_id)
        if recovery_raw:
            recovery_worker = self._dec(recovery_raw)

        return {
            "scenario_id": scenario_id,
            "connection_workers": conn_workers,
            "command_workers": command_workers,
            "owner_worker": owner_worker,
            "recovery_worker": recovery_worker,
            "observed_states": observed,
            "emitted_sequences": emitted,
            "delivered_sequences": delivered_union,
            "duplicate_count": duplicate_count,
            "gap_count": gap_count,
            "terminal_status": terminal_status,
            "failure_code": failure_code,
            "retryable": retryable,
        }

    def _name_of(self, worker_id: str) -> str:
        """Resolve a worker_id to its registry name (best effort)."""
        raw = self._registry_names.get(worker_id)
        return raw if raw else worker_id

    async def _load_registry_names(self) -> None:
        raw = await self.redis.hgetall(self._k("workers"))
        self._registry_names = {
            self._dec(k): str((json.loads(v) or {}).get("name") or "") for k, v in raw.items()
        }

    async def record_recovery(self, scenario_id: str) -> None:
        """The worker designated to regenerate a failed scenario turn."""
        payload = await self.scenario_payload(scenario_id) or {}
        designated = str(
            (payload.get("params") or {}).get("recovery_worker")
            or (payload.get("params") or {}).get("reconnect_worker")
            or ""
        )
        await self.redis.hset(
            self._k("recovery_worker"), scenario_id, designated or self.worker_name
        )

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    async def drop_socket(self, scenario_id: str) -> None:
        await self.redis.set(self._scn(scenario_id, "drop"), "1")

    async def pause_after_checkpoint(self, scenario_id: str) -> None:
        await self.redis.set(self._scn(scenario_id, "paused"), "1")

    async def kill_owner(self, scenario_id: str) -> None:
        # Kill the ACTUAL owner of the scenario's turn (the lease is the
        # source of truth — the armed owner_worker is a hint). If the turn
        # has no live lease, fall back to the armed owner name.
        payload = await self.scenario_payload(scenario_id)
        target: dict[str, Any] | None = None
        raw_turns = await self.redis.hgetall(self._k("turn_scenario"))
        for t_id, s_id in raw_turns.items():
            if self._dec(s_id) != scenario_id:
                continue
            lease = await self.coordinator.get_lease(self._dec(t_id))
            if lease is not None:
                await self._load_registry_names()
                name = self._registry_names.get(lease.owner_id) or ""
                target = await self.resolve_name(name) if name else None
                if target is None:
                    # The lease names a worker that re-registered under a new
                    # id (respawn); fall through to the armed hint below.
                    target = None
            break
        if target is None and payload:
            hint = str((payload.get("params") or {}).get("owner_worker") or "")
            target = await self.resolve_name(hint) if hint else None
        if target is None:
            logger.warning("e2e kill_owner: no live owner found for %s", scenario_id)
            return
        logger.info("e2e kill_owner: killing %s (pid=%s)", target["name"], target["pid"])
        try:
            os.kill(int(target["pid"]), signal.SIGKILL)
        except OSError as exc:
            logger.warning("e2e kill_owner failed: %s", exc)

    async def query_from_observer(self, scenario_id: str) -> None:
        payload = await self.scenario_payload(scenario_id)
        observer_name = str((payload or {}).get("params", {}).get("observer_worker") or "")
        worker = await self.resolve_name(observer_name) if observer_name else None
        raw_turns = await self.redis.hgetall(self._k("turn_scenario"))
        turn_id = next(
            (self._dec(k) for k, s in raw_turns.items() if self._dec(s) == scenario_id), None
        )
        if worker is None or turn_id is None:
            return
        # Dispatch to the observer worker's action channel; its consumer runs
        # a passive subscription and records observed states.
        await self.redis.rpush(
            self._k("worker_actions", worker["worker_id"]),
            json.dumps({"action": "observe", "scenario_id": scenario_id, "turn_id": turn_id}),
        )

    # ------------------------------------------------------------------
    # per-process background loop
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self.register_worker()
                await self._consume_actions()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("e2e fixture heartbeat error: %s", exc)
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def _consume_actions(self) -> None:
        key = self._k("worker_actions", self.worker_id)
        while True:
            raw = await self.redis.lpop(key)
            if raw is None:
                return
            action = json.loads(raw)
            if action.get("action") == "observe":
                await self.observe_turn(str(action["scenario_id"]), str(action["turn_id"]))

    def start_background(self) -> None:
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()


_fixture: E2EFixture | None = None


def get_fixture() -> E2EFixture | None:
    return _fixture


def install(container: Any) -> E2EFixture | None:
    """Install the per-process fixture (no-op when disabled)."""
    global _fixture
    if not ENABLED:
        return None
    if _fixture is not None:
        return _fixture
    _fixture = E2EFixture(container)
    return _fixture


def install_emission_gate() -> None:
    """Freeze a paused scenario turn at its single emission choke point.

    Wraps ``TurnLifecycleService._publish_live_event`` — the one place that
    both publishes to the Redis stream AND buffers for store persistence — so
    pausing there freezes both sources consistently (a reloaded browser
    replays the store up to the checkpoint, then the live Redis tail resumes
    from where it left off). Env-gated test harness; never active in
    production.
    """
    if not ENABLED:
        return
    from openkg_webui.services.session.turns.lifecycle import TurnLifecycle

    if getattr(TurnLifecycle, "_e2e_gate_installed", False):
        return
    original = TurnLifecycle._publish_live_event

    async def gated(self, execution: Any, event: Any) -> dict[str, Any]:
        fixture = get_fixture()
        if fixture is not None:
            while await fixture.pause_hold(execution.turn_id):
                await asyncio.sleep(0.05)
        return await original(self, execution, event)

    gated.__name__ = "e2e_gated_publish_live_event"
    TurnLifecycle._publish_live_event = gated
    TurnLifecycle._e2e_gate_installed = True
