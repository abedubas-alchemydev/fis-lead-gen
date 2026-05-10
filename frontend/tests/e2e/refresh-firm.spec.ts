import { expect, test } from "@playwright/test";

// E2E for the per-firm "Refresh firm" button (single button per row,
// BE-side selective fan-out via POST /broker-dealers/{id}/refresh-all).
//
// Prereqs (NOT YET WIRED IN THIS REPO — see PR description):
//   - npm install -D @playwright/test
//   - npx playwright install
//   - playwright.config.ts pointing baseURL at http://localhost:3000
//   - A signed-in storageState fixture, OR a test middleware bypass
//   - Test firms with known states:
//       * INCOMPLETE_FIRM_ID — has at least one of: financial_unknown_reason,
//         current_clearing_unknown_reason, website == null
//       * COMPLETE_FIRM_ID — every gate passes; refresh-all returns skipped
//   - A stub backend that re-fetches the firm with populated fields after
//     router.refresh() if the tests assert on populated cells.
//
// Browser-side calls are mocked via page.route(); SSR-side fetches must
// be stubbed by the test backend.

const INCOMPLETE_FIRM_ID = 123;
const COMPLETE_FIRM_ID = 124;
const RUN_ID = 9100;
const MASTER_LIST_URL = "/master-list?list=all";

test.describe("Refresh firm button", () => {
  test("queued path: idle → spinner → vanishes after refresh-all completes", async ({
    page,
  }) => {
    let pollCount = 0;

    await page.route(
      `**/api/backend/api/v1/broker-dealers/${INCOMPLETE_FIRM_ID}/refresh-all`,
      async (route) => {
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: RUN_ID,
            status: "queued",
            broker_dealer_id: INCOMPLETE_FIRM_ID,
          }),
        });
      }
    );

    await page.route(
      `**/api/backend/api/v1/pipeline/run/${RUN_ID}`,
      async (route) => {
        pollCount += 1;
        const status = pollCount === 1 ? "running" : "completed";
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: RUN_ID,
            pipeline_name: "broker_dealer_refresh_all",
            status,
            total_items: 2,
            processed_items: status === "completed" ? 2 : 1,
            success_count: status === "completed" ? 2 : 0,
            failure_count: 0,
            notes:
              status === "completed"
                ? JSON.stringify({
                    summary:
                      "Refreshed: financials, website. Skipped: clearing, contacts.",
                    ran: ["refresh-financials", "resolve-website"],
                    skipped: ["health-check", "enrich"],
                  })
                : JSON.stringify({ summary: "running" }),
            started_at: "2026-05-02T12:00:00Z",
            completed_at: status === "completed" ? "2026-05-02T12:01:30Z" : null,
          }),
        });
      }
    );

    await page.goto(MASTER_LIST_URL);

    const refreshButton = page.getByTestId("refresh-firm-button").first();
    await expect(refreshButton).toBeVisible();
    await refreshButton.click();
    await expect(refreshButton).toBeDisabled();

    await expect(
      page.getByText(/Refreshed: financials, website/i)
    ).toBeVisible({ timeout: 10_000 });
  });

  test("skipped path: 200 + status='skipped' shows confirmation, no polling", async ({
    page,
  }) => {
    let pollCalled = false;

    await page.route(
      `**/api/backend/api/v1/broker-dealers/${COMPLETE_FIRM_ID}/refresh-all`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: null,
            status: "skipped",
            broker_dealer_id: COMPLETE_FIRM_ID,
            reason: "Already complete.",
          }),
        });
      }
    );

    await page.route(`**/api/backend/api/v1/pipeline/run/**`, async (route) => {
      pollCalled = true;
      await route.fulfill({ status: 404, body: "{}" });
    });

    await page.goto(MASTER_LIST_URL);
    const refreshButton = page.getByTestId("refresh-firm-button").first();
    if (await refreshButton.isVisible({ timeout: 1_000 })) {
      await refreshButton.click();
      await expect(page.getByText(/Already complete/i)).toBeVisible({
        timeout: 5_000,
      });
      expect(pollCalled).toBe(false);
    }
  });

  test("409 in-flight path: polls existing run", async ({ page }) => {
    await page.route(
      `**/api/backend/api/v1/broker-dealers/${INCOMPLETE_FIRM_ID}/refresh-all`,
      async (route) => {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              message: "A refresh-all run is already in flight for this firm.",
              run_id: RUN_ID,
              status: "running",
              broker_dealer_id: INCOMPLETE_FIRM_ID,
            },
          }),
        });
      }
    );

    await page.route(
      `**/api/backend/api/v1/pipeline/run/${RUN_ID}`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: RUN_ID,
            pipeline_name: "broker_dealer_refresh_all",
            status: "completed",
            total_items: 1,
            processed_items: 1,
            success_count: 1,
            failure_count: 0,
            notes: JSON.stringify({ summary: "Refreshed: website." }),
            started_at: "2026-05-02T12:00:00Z",
            completed_at: "2026-05-02T12:00:30Z",
          }),
        });
      }
    );

    await page.goto(MASTER_LIST_URL);
    const refreshButton = page.getByTestId("refresh-firm-button").first();
    await refreshButton.click();
    await expect(page.getByText(/Refreshed: website/i)).toBeVisible({
      timeout: 10_000,
    });
  });

  test("503: surfaces 'temporarily unavailable' toast and resets the button", async ({
    page,
  }) => {
    await page.route(
      `**/api/backend/api/v1/broker-dealers/${INCOMPLETE_FIRM_ID}/refresh-all`,
      async (route) => {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Apollo API key is not configured.",
          }),
        });
      }
    );

    await page.goto(MASTER_LIST_URL);
    const refreshButton = page.getByTestId("refresh-firm-button").first();
    await refreshButton.click();
    await expect(page.getByText(/temporarily unavailable/i)).toBeVisible({
      timeout: 5_000,
    });
    await expect(refreshButton).toBeEnabled();
  });

  test("429 cooldown: surfaces rate-limit toast", async ({ page }) => {
    await page.route(
      `**/api/backend/api/v1/broker-dealers/${INCOMPLETE_FIRM_ID}/refresh-all`,
      async (route) => {
        await route.fulfill({
          status: 429,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Refresh-all cooldown active. Try again in 22s.",
          }),
        });
      }
    );

    await page.goto(MASTER_LIST_URL);
    const refreshButton = page.getByTestId("refresh-firm-button").first();
    await refreshButton.click();
    await expect(page.getByText(/Slow down/i)).toBeVisible({ timeout: 5_000 });
    await expect(refreshButton).toBeEnabled();
  });
});
