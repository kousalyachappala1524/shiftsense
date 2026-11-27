"""Open the public demo in a real browser and wake it if Streamlit Community Cloud put it to sleep.

Community Cloud sleeps an app after a period with no visitors, and the next visitor (often a
recruiter clicking a link) lands on a "This app has gone to sleep" page instead of the app.
.github/workflows/keep-awake.yml runs this every few hours so the demo stays ready.

Usage: python scripts/keep_awake.py [app_url]
"""

from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

APP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://shiftsense-demo.streamlit.app/"
WAKE_BUTTON = "Yes, get this app back up!"
READY_TEXT = "ShiftSense"
TIMEOUT_S = 300


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("PW_CHANNEL") or None)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(8)

        button = page.get_by_role("button", name=WAKE_BUTTON)
        if button.count():
            print("App was asleep: clicking the wake button")
            button.click()
        else:
            print("App was awake")

        start = time.time()
        while time.time() - start < TIMEOUT_S:
            for frame in page.frames:
                try:
                    if frame.get_by_role("heading", name=READY_TEXT).count():
                        print(f"App is up ({time.time() - start:.0f}s)")
                        time.sleep(20)  # stay connected briefly so the visit counts as activity
                        browser.close()
                        return 0
                except Exception:  # noqa: BLE001 - frames can detach while the app restarts
                    pass
            time.sleep(5)

        page.screenshot(path="keep_awake_failure.png", full_page=True)
        print("App did not come up in time", file=sys.stderr)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
