import assert from "node:assert/strict";

import type { APIRequestContext, Page } from "@playwright/test";
import { expect, test as base } from "@playwright/test";

export type MultiWorkerScenario =
  | "cross_worker_resume"
  | "cross_worker_cancel"
  | "cross_worker_reply"
  | "owner_loss"
  | "reload_replay"
  | "foreign_observation";

interface RuntimeStatus {
  worker_count?: number;
  coordination?: { backend?: string; healthy?: boolean };
  redis?: { configured?: boolean; healthy?: boolean };
}

interface ArmedScenario {
  scenario_id: string;
  prompt: string;
  expected_turn_id?: string;
}

export interface ScenarioEvidence {
  scenario_id: string;
  connection_workers: string[];
  command_workers?: string[];
  owner_worker?: string;
  recovery_worker?: string;
  observed_states?: string[];
  emitted_sequences: number[];
  delivered_sequences: number[];
  duplicate_count: number;
  gap_count: number;
  terminal_status?: string;
  failure_code?: string;
  retryable?: boolean;
}

const DEFAULT_CONTROL_PATH = "/__e2e__/v2-turn-runtime";

export const multiWorkerFixtureAvailable =
  process.env.OPENKG_WEBUI_MULTI_WORKER_E2E === "1";

function fixtureBaseUrl(): string {
  return (
    process.env.OPENKG_WEBUI_MULTI_WORKER_CONTROL_URL ||
    process.env.NEXT_PUBLIC_API_BASE ||
    process.env.WEB_BASE_URL ||
    "http://127.0.0.1:8082"
  ).replace(/\/$/, "");
}

export class MultiWorkerRuntimeFixture {
  private readonly baseUrl = fixtureBaseUrl();
  private readonly controlPath =
    process.env.OPENKG_WEBUI_MULTI_WORKER_CONTROL_PATH || DEFAULT_CONTROL_PATH;
  private legacyRequests: string[] = [];

  constructor(private readonly request: APIRequestContext) {}

  trackNetwork(page: Page): void {
    page.on("request", (request) => {
      const pathname = new URL(request.url()).pathname;
      if (pathname === "/api/chat" || pathname.startsWith("/api/chat/")) {
        this.legacyRequests.push(request.url());
      }
    });
  }

  async assertReady(): Promise<void> {
    const response = await this.request.get(
      `${this.baseUrl}/api/system/runtime`,
    );
    expect(
      response.ok(),
      "runtime status endpoint must be reachable",
    ).toBeTruthy();
    const status = (await response.json()) as RuntimeStatus;
    expect(
      status.worker_count,
      "browser acceptance requires exactly four workers",
    ).toBe(4);
    expect(
      status.coordination?.backend,
      "browser acceptance requires Redis coordination",
    ).toBe("redis");
    expect(
      status.coordination?.healthy ?? status.redis?.healthy,
      "Redis coordination must be healthy",
    ).toBe(true);

    const fixture = await this.request.get(this.controlUrl("health"));
    expect(
      fixture.ok(),
      "the deterministic failure-injection fixture must be enabled",
    ).toBeTruthy();
    // ``kill_owner`` kills a uvicorn worker process; the supervisor respawns
    // it, but registration lags — poll instead of a single-shot check.
    await expect
      .poll(
        async () => {
          const polled = await this.request.get(this.controlUrl("health"));
          const fixtureStatus = (await polled.json()) as { worker_ids?: string[] };
          return fixtureStatus.worker_ids?.length ?? 0;
        },
        { message: "four workers must be live", timeout: 20_000 },
      )
      .toBe(4);
  }

  async reset(): Promise<void> {
    this.legacyRequests = [];
    const response = await this.request.post(this.controlUrl("reset"));
    expect(response.ok(), "fixture reset failed").toBeTruthy();
  }

  async arm(
    scenario: MultiWorkerScenario,
    options: Record<string, unknown> = {},
  ): Promise<ArmedScenario> {
    const response = await this.request.post(this.controlUrl("scenarios"), {
      data: { scenario, ...options },
    });
    expect(response.ok(), `could not arm ${scenario}`).toBeTruthy();
    return (await response.json()) as ArmedScenario;
  }

  async act(scenarioId: string, action: string): Promise<void> {
    const response = await this.request.post(
      this.controlUrl(`scenarios/${encodeURIComponent(scenarioId)}/actions`),
      { data: { action } },
    );
    expect(response.ok(), `fixture action ${action} failed`).toBeTruthy();
  }

  async evidence(scenarioId: string): Promise<ScenarioEvidence> {
    const response = await this.request.get(
      this.controlUrl(`scenarios/${encodeURIComponent(scenarioId)}/evidence`),
    );
    expect(response.ok(), "fixture evidence is unavailable").toBeTruthy();
    return (await response.json()) as ScenarioEvidence;
  }

  async expectEvidence(
    scenarioId: string,
    predicate: (evidence: ScenarioEvidence) => boolean,
    description: string,
  ): Promise<ScenarioEvidence> {
    let latest: ScenarioEvidence | undefined;
    await expect
      .poll(
        async () => {
          try {
            latest = await this.evidence(scenarioId);
            return predicate(latest);
          } catch {
            // The owner-loss audit kills a backend worker mid-poll; a request
            // that lands on the dying process resets transiently. Treat that
            // as "not yet satisfied" and keep polling.
            return false;
          }
        },
        { message: description, timeout: 60_000 },
      )
      .toBe(true);
    assert(latest);
    return latest;
  }

  /**
   * Wait until the armed scenario's turn has started publishing stream events.
   *
   * The browser UI activity locators can match a *stale* turn from an earlier
   * run (the fixture home accumulates sessions), so gating failure-injection
   * actions on the UI races the subscription: the action lands before
   * ``gate_subscription`` runs and the new connection clears the flag. Waiting
   * on evidence (``emitted_sequences`` non-empty) is deterministic — the turn
   * exists and the browser's subscription is already live by the time events
   * flow.
   */
  async waitForTurnStreaming(scenarioId: string): Promise<void> {
    await this.expectEvidence(
      scenarioId,
      (value) => (value.emitted_sequences?.length ?? 0) > 0,
      "the turn did not start streaming",
    );
  }

  assertNoLegacyRequests(): void {
    assert.deepEqual(
      this.legacyRequests,
      [],
      `the browser requested retired chat endpoints: ${this.legacyRequests.join(", ")}`,
    );
  }

  private controlUrl(suffix: string): string {
    return `${this.baseUrl}${this.controlPath}/${suffix}`;
  }
}

export const test = base.extend<{ runtimeFixture: MultiWorkerRuntimeFixture }>({
  runtimeFixture: async ({ request, page }, provide) => {
    const fixture = new MultiWorkerRuntimeFixture(request);
    fixture.trackNetwork(page);
    await fixture.assertReady();
    await fixture.reset();
    await provide(fixture);
    fixture.assertNoLegacyRequests();
  },
});

export { expect };

export async function sendPrompt(page: Page, prompt: string): Promise<void> {
  const composer = page.getByRole("textbox").last();
  await expect(composer).toBeVisible();
  await composer.fill(prompt);
  await composer.press("Enter");
}

export function assistantActivity(page: Page) {
  return page.locator('[aria-live="polite"]').filter({
    hasText:
      /OPENKG-WebUI (?:Exploring|Reasoning|Planning|Quizzing|Reflecting|responded)|Tool Calling/i,
  });
}
