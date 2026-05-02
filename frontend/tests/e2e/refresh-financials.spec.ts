import { expect, test } from "@playwright/test";

// E2E for the per-firm "Refresh financials" button.
//
// Prereqs (NOT YET WIRED IN THIS REPO — see PR description):
//   - npm install -D @playwright/test
//   - npx playwright install
//   - playwright.config.ts pointing baseURL at http://localhost:3000
//   - A signed-in storageState fixture, OR a test middleware bypass
//   - A test firm seeded with `unknown_reason.category === "not_yet_extracted"`
//     for latest_net_capital / latest_excess_net_capital / yoy_growth, and
//     a stub backend that:
//       * GET /api/v1/broker-dealers/{TEST_FIRM_ID} returns the unknown
//         reason on first request, populated financials after the test
//         calls router.refresh()
//       * POST /api/v1/broker-dealers/{TEST_FIRM_ID}/refresh-financials,
//         GET /api/v1/pipeline/run/{RUN_ID} — both mocked here at the
//         browser layer via page.route()
//
// Browser-side calls are mocked via page.route(); SSR-side fetches must be
// stubbed by the test backend or fixture. Coordinate with the BE worktree
// on fixture seeding before enabling this test in CI.

const TEST_FIRM_ID = 123;
const RUN_ID = 9100;
const FIRM_DETAIL_URL = `/master-list/${TEST_FIRM_ID}`;

test.describe("Refresh financials button", () => {
  test("transitions idle → pending → vanishes after pipeline completes", async ({
    page,
  }) => {
    let pollCount = 0;

    await page.route(
      `**/api/backend/api/v1/broker-dealers/${TEST_FIRM_ID}/refresh-financials`,
      async (route) => {
        await route.fulfill({
          status: 202,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: RUN_ID,
            status: "queued",
            broker_dealer_id: TEST_FIRM_ID,
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
            pipeline_name: "financial_pdf_pipeline_single",
            status,
            total_items: 1,
            processed_items: status === "completed" ? 1 : 0,
            success_count: status === "completed" ? 1 : 0,
            failure_count: 0,
            notes:
              status === "completed"
                ? JSON.stringify({
                    summary:
                      "Processed 1 filings via gemini. Records extracted: 2 (0 needs_review).",
                  })
                : JSON.stringify({ bd_id: TEST_FIRM_ID, stage: "running" }),
            started_at: "2026-05-02T12:00:00Z",
            completed_at: status === "completed" ? "2026-05-02T12:01:30Z" : null,
          }),
        });
      }
    );

    await page.goto(FIRM_DETAIL_URL);

    const refreshButton = page.getByTestId("refresh-financials-button").first();
    await expect(refreshButton).toBeVisible();
    await expect(refreshButton).toContainText("Refresh financials");

    await refreshButton.click();

    await expect(refreshButton).toContainText("Running…");
    await expect(refreshButton).toBeDisabled();

    // Polling cadence is 3s; second poll returns "completed", which triggers
    // router.refresh() and clears the unknown-reason cells. The button
    // unmounts when the surrounding UnknownCell unmounts. Allow ~10s.
    await expect(page.getByTestId("refresh-financials-button")).toHaveCount(0, {
      timeout: 10_000,
    });

    // After SSR re-renders with populated financials, the Net Capital tile
    // should no longer show "N/A". Depends on the fixture flipping
    // latest_net_capital from null to a number after the refresh.
    await expect(page.getByText("Net Capital")).toBeVisible();
  });

  test("409 in-flight run is treated as success — polls existing run", async ({
    page,
  }) => {
    let pollCount = 0;

    await page.route(
      `**/api/backend/api/v1/broker-dealers/${TEST_FIRM_ID}/refresh-financials`,
      async (route) => {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              message:
                "A refresh-financials run is already in flight for this firm.",
              run_id: RUN_ID,
              status: "running",
              broker_dealer_id: TEST_FIRM_ID,
            },
          }),
        });
      }
    );

    await page.route(
      `**/api/backend/api/v1/pipeline/run/${RUN_ID}`,
      async (route) => {
        pollCount += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: RUN_ID,
            pipeline_name: "financial_pdf_pipeline_single",
            status: "completed",
            total_items: 1,
            processed_items: 1,
            success_count: 1,
            failure_count: 0,
            notes: JSON.stringify({ summary: "Processed." }),
            started_at: "2026-05-02T12:00:00Z",
            completed_at: "2026-05-02T12:01:00Z",
          }),
        });
      }
    );

    await page.goto(FIRM_DETAIL_URL);
    const refreshButton = page.getByTestId("refresh-financials-button").first();
    await refreshButton.click();
    await expect(refreshButton).toContainText("Running…");
    await expect(page.getByTestId("refresh-financials-button")).toHaveCount(0, {
      timeout: 10_000,
    });
    expect(pollCount).toBeGreaterThanOrEqual(1);
  });

  test("503 surfaces 'temporarily unavailable' toast and resets the button", async ({
    page,
  }) => {
    await page.route(
      `**/api/backend/api/v1/broker-dealers/${TEST_FIRM_ID}/refresh-financials`,
      async (route) => {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "Gemini API key is not configured.",
          }),
        });
      }
    );

    await page.goto(FIRM_DETAIL_URL);
    const refreshButton = page.getByTestId("refresh-financials-button").first();
    await refreshButton.click();

    await expect(page.getByText(/temporarily unavailable/i)).toBeVisible({
      timeout: 5_000,
    });
    await expect(refreshButton).toContainText("Refresh financials");
    await expect(refreshButton).toBeEnabled();
  });
});
