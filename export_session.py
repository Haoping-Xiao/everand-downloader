from playwright.sync_api import sync_playwright


DEBUG_URL = "http://127.0.0.1:9222"


def main():
	with sync_playwright() as playwright:
		browser = playwright.chromium.connect_over_cdp(DEBUG_URL)
		if not browser.contexts:
			raise SystemExit("No browser context found in the running Chrome instance.")

		context = browser.contexts[0]
		page = context.new_page()
		page.goto("https://www.everand.com", wait_until="domcontentloaded")
		page.locator("div.user_row").wait_for(state="attached", timeout=120000)
		context.storage_state(path="session.json")
		page.close()
		browser.close()
		print("Saved session.json from the running Chrome instance.")


if __name__ == "__main__":
	main()
