"""Deterministic turn-fixture ACP agent for the critical-turns browser audit.

Runs over stdio via the ACP SDK so the wire dialect is exactly what the
``intellect acp`` transport speaks. Behavior is scripted from the user
prompt (the backend passes it through verbatim), which is what makes the
browser suite deterministic:

* ``long turn`` in the prompt        -> a long-running turn that keeps
  streaming activity for a while (the browser drops its connection and
  reconnects mid-turn).
* ``cancellable turn`` in the prompt -> a turn that keeps streaming until
  the backend cancels it, then stops.
* anything else (the ask-user case)  -> stream activity, ask the user a
  question (ACP elicitation), then continue once the answer arrives and
  finish with a clean ``end_turn``.

The wrapper script ``turn_e2e_fixture_runner.sh`` execs this file; the
ACP transport's injected ``acp`` argv marker is ignored.
"""

import asyncio

import acp
import acp.schema as schema


class TurnFixtureAgent:
    def __init__(self) -> None:
        self.conn = None
        self.session_id = "turn-fixture-session"
        self.cancelled = False
        self.elicitation_answer = ""

    # -- ACP handshake -------------------------------------------------------

    def on_connect(self, conn) -> None:
        # The SDK calls this synchronously (not awaited) with the
        # AgentSideConnection, so it must be a plain method.
        self.conn = conn

    async def initialize(self, *args, **kwargs):
        return schema.InitializeResponse(
            protocol_version=1,
            agent_capabilities=schema.AgentCapabilities(load_session=True),
        )

    async def new_session(self, *args, **kwargs):
        return schema.NewSessionResponse(session_id=self.session_id)

    async def load_session(self, *args, **kwargs):
        return schema.LoadSessionResponse(session_id=self.session_id)

    async def set_config_option(self, *args, **kwargs):
        return schema.SetSessionConfigOptionResponse(config_options=[])

    # -- the turn -------------------------------------------------------------

    async def prompt(self, session_id, prompt, **kwargs):
        blocks = prompt if isinstance(prompt, list) else [prompt]
        # The user message is the LAST block (grounding blocks precede it);
        # match the script across the whole prompt so per-turn context blocks
        # never change the scenario.
        head = " ".join(str(getattr(block, "text", "") or "") for block in blocks if block)
        lowered = head.lower()
        if "multi-worker" in lowered:
            return await self._multi_worker_turn(session_id, head)
        scenario = (
            "long"
            if "long turn" in lowered
            else ("cancel" if "cancellable turn" in lowered else "ask_user")
        )

        # Every scenario opens with an activity signal: the frontend reads a
        # thinking chunk as the canonical "OPENKG-WebUI Exploring…" surface.
        await self.conn.session_update(
            session_id, acp.update_agent_thought_text("Checking the turn lifecycle…")
        )

        if scenario == "long":
            # Keep streaming for a while so the audit can drop the connection
            # and reconnect while the turn is still live on the backend.
            for tick in range(1, 26):
                if self.cancelled:
                    break
                await self.conn.session_update(
                    session_id,
                    acp.update_agent_thought_text(f"Long turn progress {tick}…"),
                )
                await asyncio.sleep(1)
            await self.conn.session_update(
                session_id, acp.update_agent_message_text("Long turn complete.")
            )
            return schema.PromptResponse(stop_reason="end_turn")

        if scenario == "cancel":
            # Stream until the backend's cancel lands on us.
            tick = 0
            while not self.cancelled:
                tick += 1
                await self.conn.session_update(
                    session_id,
                    acp.update_agent_thought_text(f"Cancellable turn progress {tick}…"),
                )
                await asyncio.sleep(0.5)
            return schema.PromptResponse(stop_reason="end_turn")

        # ask_user: show the question card, then continue on the answer.
        await self.conn.session_update(
            session_id, acp.update_agent_message_text("I'll verify the turn lifecycle.")
        )
        # Hold the "Exploring" activity surface for a moment before the card
        # arrives: the browser audit asserts the canonical status BEFORE the
        # ask_user card flips the header to "Tool Calling".
        await asyncio.sleep(2.5)
        mode = schema.ElicitationFormSessionMode(
            session_id=session_id,
            requested_schema=schema.ElicitationSchema(
                type="object",
                properties={
                    "answer": schema.ElicitationStringPropertySchema(
                        type="string",
                        title="Your answer",
                    )
                },
                required=["answer"],
            ),
        )
        response = await self.conn.create_elicitation(
            message="The agent needs more information to continue.",
            mode=mode,
        )
        content = getattr(response, "content", None)
        self.elicitation_answer = (
            str(content.get("answer") or "") if isinstance(content, dict) else ""
        )

        # Keep the resumed round's status visible before the answer settles.
        await self.conn.session_update(
            session_id,
            acp.update_agent_thought_text(
                f"Continuing with your answer: {self.elicitation_answer}"
            ),
        )
        await asyncio.sleep(2)
        await self.conn.session_update(
            session_id, acp.update_agent_message_text("The lifecycle checks out.")
        )
        return schema.PromptResponse(
            stop_reason="end_turn",
            usage=schema.Usage(input_tokens=3, output_tokens=5, total_tokens=8),
        )

    # -- multi-worker scenarios ------------------------------------------------

    async def _multi_worker_turn(self, session_id, head: str):
        """Deterministic script for the four-worker browser audit.

        ``reply`` scenarios pause on a question mid-turn (the audit answers
        through the ask_user card); every other scenario streams a long turn
        so the fixture can drop the socket, pause at a checkpoint, kill the
        owner, or cancel — all while the turn is still live.
        """
        await self.conn.session_update(
            session_id, acp.update_agent_thought_text("Opening the multi-worker turn…")
        )
        if "reply" in head.lower():
            # Short pre-stream so the activity surface is visible, then the
            # question card the audit answers from worker-d.
            for tick in range(1, 6):
                if self.cancelled:
                    break
                await self.conn.session_update(
                    session_id,
                    acp.update_agent_thought_text(f"Multi-worker progress {tick}…"),
                )
                await asyncio.sleep(0.25)
            if self.cancelled:
                return schema.PromptResponse(stop_reason="end_turn")
            mode = schema.ElicitationFormSessionMode(
                session_id=session_id,
                requested_schema=schema.ElicitationSchema(
                    type="object",
                    properties={
                        "answer": schema.ElicitationStringPropertySchema(
                            type="string",
                            title="Your answer",
                        )
                    },
                    required=["answer"],
                ),
            )
            response = await self.conn.create_elicitation(
                message="The multi-worker turn needs more information to continue.",
                mode=mode,
            )
            content = getattr(response, "content", None)
            answer = str(content.get("answer") or "") if isinstance(content, dict) else ""
            await self.conn.session_update(
                session_id,
                acp.update_agent_thought_text(f"Continuing with: {answer}"),
            )
            await self.conn.session_update(
                session_id, acp.update_agent_message_text("Multi-worker reply complete.")
            )
            return schema.PromptResponse(stop_reason="end_turn")

        # Long stream: ~10s of activity so any fixture injection lands mid-turn.
        for tick in range(1, 41):
            if self.cancelled:
                break
            await self.conn.session_update(
                session_id,
                acp.update_agent_thought_text(f"Multi-worker progress {tick}…"),
            )
            await asyncio.sleep(0.25)
        await self.conn.session_update(
            session_id, acp.update_agent_message_text("Multi-worker turn complete.")
        )
        return schema.PromptResponse(stop_reason="end_turn")

    async def cancel(self, *args, **kwargs) -> None:
        self.cancelled = True


if __name__ == "__main__":
    asyncio.run(acp.run_agent(TurnFixtureAgent()))
