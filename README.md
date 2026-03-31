# everand-downloader

Download Everand eBooks as PDFs for personal offline use.

Only Everand eBooks are supported. Scribd documents and other formats are not covered by this workflow.

## Requirements

- Python 3
- Google Chrome installed locally
- Playwright Python dependencies from `requirements.txt`
- If you use batch mode: MongoDB running locally on `127.0.0.1:27017`

Install dependencies:

```bash
pip install -r requirements.txt
```

Or with `uv`:

```bash
uv sync
```

Install the Playwright browser runtime if you have not done that yet:

```bash
uv run playwright install chromium
```

If Playwright reports `Executable doesn't exist ...`, this step was skipped or did not finish.

## How It Works

This repo has two entry points:

- [run.py](/Users/hopkinx/ai%20for%2060+/everand-downloader/run.py): export a single book from a direct Everand book URL.
- [main.py](/Users/hopkinx/ai%20for%2060+/everand-downloader/main.py): batch mode. It reads book URLs from MongoDB and calls `run.py` for each entry.

`run.py` does not launch a fresh login browser anymore. It connects to an already running local Chrome instance through the Chrome DevTools Protocol at `http://127.0.0.1:9222`.

## Single Book Usage

1. Start Chrome with remote debugging enabled:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/everand-chrome
```

2. In that Chrome window, open [Everand](https://www.everand.com) and log in manually.

3. Confirm the browser is really logged in before running the script. The exporter waits for the `div.user_row` element on the Everand home page. If you are not logged in yet, `run.py` will time out after 120 seconds.

4. Run the exporter with a direct book URL:

```bash
uv run run.py 'https://www.everand.com/book/860803292/AI-for-60'
```

You can also use plain Python:

```bash
python3 run.py 'https://www.everand.com/book/860803292/AI-for-60'
```

Generated output:

- `<book-slug>.pdf`
- `<book-slug>.txt`
- `<book-slug>.pages.jsonl`
- `<book-slug>/` directory with intermediate page assets and debug files

Example for the URL above:

- `AI-for-60.pdf`
- `AI-for-60.txt`
- `AI-for-60.pages.jsonl`
- `AI-for-60/`

By default the repo keeps debug artifacts. Set `EVERAND_DEBUG_CAPTURE=0` if you want the cache directory removed after a successful run.

## Optional: Export Session State

If you want to save the login session from the running Chrome instance:

```bash
uv run export_session.py
```

This writes [session.json](/Users/hopkinx/ai%20for%2060+/everand-downloader/session.json).

Important: `session.json` is not a separate login mechanism. It is only written after the Chrome window is already logged in successfully.

## Batch Usage With MongoDB

Use this only if you want to process many books from a local MongoDB collection.

[main.py](/Users/hopkinx/ai%20for%2060+/everand-downloader/main.py) expects:

- MongoDB running locally on `127.0.0.1:27017`
- database: `bookUrlList`
- collection: `books`

Each document must include:

- `id`
- `title`
- `url`

Example insert:

```javascript
use bookUrlList
db.books.insertOne({
  id: 860803292,
  title: "AI for 60+",
  url: "https://www.everand.com/book/860803292/AI-for-60"
})
```

Then run:

```bash
uv run main.py
```

Or:

```bash
python3 main.py
```

To scrape Everand book URLs into MongoDB, you can use the related project:

["everand-book-url-downloader"](https://github.com/CrazyCoder76/everand-book-url-scraper)

## Common Pitfalls

### 1. `pymongo.errors.ServerSelectionTimeoutError: 127.0.0.1:27017 connection refused`

Cause: MongoDB is not running, but `main.py` requires it.

Fix:

```bash
brew services start mongodb-community
```

Then verify the collection contains at least one document before running batch mode.

### 2. `BrowserType.launch: Executable doesn't exist`

Cause: the Playwright browser runtime was not installed.

Fix:

```bash
uv run playwright install chromium
```

### 3. `This browser or app may not be secure`

Cause: the login flow was opened in an environment that Google or Everand rejected.

What this means for this repo:

- `run.py` can only continue after the browser is already logged in.
- `session.json` is only saved after login succeeds.
- If the login challenge is blocked, the script cannot create a valid session on its own.

Recommended approach:

- Start a normal local Chrome instance with `--remote-debugging-port=9222`
- Log in manually in that Chrome window
- Only then run `run.py` or `export_session.py`

### 4. `We couldn't load the security challenge` or `Error code: 600010`

Cause: the site's login challenge did not load correctly.

Typical things to check:

- Use regular Chrome, not a stale Playwright-only browser profile
- Disable VPN, proxy, ad blockers, and script-blocking extensions
- Make sure JavaScript and third-party cookies are allowed
- Clear site data for `everand.com`, `scribd.com`, and related login pages if the browser is stuck in a broken challenge state

This is an account/browser challenge issue, not a MongoDB issue.

### 5. `Browser limit exceeded`

Cause: Everand refused the reader session because too many browsers or computers were used recently.

Current behavior:

- [run.py](/Users/hopkinx/ai%20for%2060+/everand-downloader/run.py) stops with a clear error
- [main.py](/Users/hopkinx/ai%20for%2060+/everand-downloader/main.py) reports the failure for that book

### 6. `failed download ...` from `main.py`

Cause: `main.py` only shows the final status of each `run.py` subprocess. The real error is usually inside the subprocess.

Fix:

- First test the exact URL with `python3 run.py '<book-url>'`
- Once single-book mode succeeds, use `main.py` for batch mode

## Notes

- `main.py` is set to `concurrent_limit = 1` in the current codebase to reduce overlapping browser issues during debugging.
- `run.py` writes debug logs to `<book-slug>/debug/events.jsonl` when `EVERAND_DEBUG_CAPTURE=1`.
