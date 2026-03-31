from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
from PIL import Image, ImageChops

import base64
import html
import hashlib
import json
import os
import sys
import time
import shutil

SECURITY_CHALLENGE_TEXT = "We couldn’t load the security challenge"
CDP_URL = os.environ.get("CHROME_CDP_URL", "http://127.0.0.1:9222")
MAX_SPREADS = 2000
DEBUG_CAPTURE = os.environ.get("EVERAND_DEBUG_CAPTURE", "1") == "1"

book_url = sys.argv[1]
book_filename = book_url.split('/')[5]
cache_dir = os.path.join(os.getcwd(), book_filename)
debug_dir = os.path.join(cache_dir, "debug")
debug_log_path = os.path.join(debug_dir, "events.jsonl")


def ensure_cache_dir():
	try:
		os.mkdir(cache_dir)
	except FileExistsError:
		pass

	if DEBUG_CAPTURE:
		os.makedirs(debug_dir, exist_ok=True)


def cleanup_cache_dir():
	if DEBUG_CAPTURE:
		return

	if os.path.isdir(cache_dir):
		shutil.rmtree(cache_dir)


def log_debug(event_type, **payload):
	if not DEBUG_CAPTURE:
		return

	record = {
		"ts": round(time.time(), 3),
		"event": event_type,
		**payload,
	}
	with open(debug_log_path, "a", encoding="utf-8") as handle:
		handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def wait_for_login(page):
	page.goto('https://www.everand.com', wait_until='domcontentloaded')
	page.locator("div.user_row").wait_for(state='attached', timeout=120000)


def open_reader(page):
	page.goto(book_url.replace('book', 'read'), wait_until='domcontentloaded')

	if 'Browser limit exceeded' in page.content():
		raise RuntimeError(
			'You have tried to read this from too many computers or web browsers recently, '
			'and will need to wait up to 24 hours before returning to this book.'
		)

	if SECURITY_CHALLENGE_TEXT in page.content():
		raise RuntimeError('Everand requested a security challenge again in the reader page.')

	page.locator('#column_container').wait_for(state='visible', timeout=120000)
	page.wait_for_timeout(2000)


def wait_for_stable_column(locator, previous_hash=None, timeout=12):
	deadline = time.time() + timeout
	last_hash = None
	stable_rounds = 0
	last_payload = None

	while time.time() < deadline:
		try:
			locator.wait_for(state='attached', timeout=1500)
			html = locator.inner_html(timeout=1500)
			text = locator.inner_text(timeout=1500)
			style = locator.get_attribute('style') or ''
		except PlaywrightTimeoutError:
			time.sleep(0.5)
			continue

		payload = (html, text, style)
		payload_hash = hashlib.md5('||'.join(payload).encode('utf-8')).hexdigest()

		meaningful = bool(text.strip()) or 'img' in html or 'svg' in html
		if meaningful and payload_hash == last_hash and payload_hash != previous_hash:
			stable_rounds += 1
			if stable_rounds >= 1:
				return {
					'html': html,
					'text': text,
					'style': style,
					'hash': payload_hash,
				}
		else:
			stable_rounds = 0

		last_hash = payload_hash
		last_payload = payload
		time.sleep(0.7)

	if last_payload is None:
		return None

	html, text, style = last_payload
	if not text.strip() and 'img' not in html and 'svg' not in html:
		return None

	return {
		'html': html,
		'text': text,
		'style': style,
		'hash': hashlib.md5('||'.join(last_payload).encode('utf-8')).hexdigest(),
	}


def get_spread(page, previous_spread_hash=None):
	left_locator = page.locator('.reader_column.left_column [data-content-column]').first
	right_locator = page.locator('.reader_column.right_column [data-content-column]').first

	left = wait_for_stable_column(left_locator)
	right = wait_for_stable_column(right_locator)

	if left:
		left['side'] = 'left'
	if right:
		right['side'] = 'right'

	left_hash = left['hash'] if left else 'empty'
	right_hash = right['hash'] if right else 'empty'
	spread_hash = hashlib.md5(f'{left_hash}|{right_hash}'.encode('utf-8')).hexdigest()

	if previous_spread_hash and spread_hash == previous_spread_hash:
		return None, spread_hash

	return [column for column in (left, right) if column], spread_hash


def get_column_locator(page, side):
	return page.locator(f'.reader_column.{side}_column [data-content-column]').first


def wait_for_column_assets(locator, timeout=10000):
	locator.wait_for(state='visible', timeout=timeout)
	return locator.evaluate(
		"""async node => {
			if (document.fonts && document.fonts.ready) {
				try {
					await document.fonts.ready;
				} catch (error) {
				}
			}

			const images = Array.from(node.querySelectorAll('img'));
			const imageResults = await Promise.all(images.map(img => {
				if (img.complete && img.naturalWidth > 0) {
					return {
						src: img.currentSrc || img.src || '',
						ok: true,
						width: img.naturalWidth || 0,
						height: img.naturalHeight || 0
					};
				}

				return new Promise(resolve => {
					const finalize = ok => resolve({
						src: img.currentSrc || img.src || '',
						ok,
						width: img.naturalWidth || 0,
						height: img.naturalHeight || 0
					});
					img.addEventListener('load', () => finalize(true), { once: true });
					img.addEventListener('error', () => finalize(false), { once: true });
					setTimeout(() => finalize(img.complete && img.naturalWidth > 0), 4000);
				});
			}));

			return {
				imageCount: images.length,
				imagesOk: imageResults.filter(item => item.ok).length,
				imagesFailed: imageResults.filter(item => !item.ok).length,
				images: imageResults
			};
		}""",
		timeout=timeout,
	)


def image_looks_blank(image_path):
	with Image.open(image_path) as image:
		rgb_image = image.convert('RGB')
		inverted = ImageChops.invert(rgb_image)
		bounding_box = inverted.getbbox()
		if bounding_box is None:
			return True

		cropped = rgb_image.crop(bounding_box)
		minimum, maximum = cropped.convert('L').getextrema()
		return minimum >= 250 and maximum >= 250


def convert_image_to_pdf(image_path, pdf_path):
	with Image.open(image_path) as image:
		image.convert('RGB').save(pdf_path, 'PDF', resolution=144.0)


def normalize_text_for_export(text):
	lines = [line.rstrip() for line in text.replace('\xa0', ' ').splitlines()]
	return '\n'.join(lines).strip()


def get_image_dimensions(image_path):
	with Image.open(image_path) as image:
		return image.size


def build_text_overlay_pdf_html(text, page_no, width, height):
	escaped_text = html.escape(text)
	return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
	@page {{ size: {width}px {height}px; margin: 0; }}
	html, body {{
		margin: 0;
		padding: 0;
		width: {width}px;
		height: {height}px;
		background: transparent;
	}}
	.text-layer {{
		position: absolute;
		inset: 0;
		padding: 24px;
		box-sizing: border-box;
		white-space: pre-wrap;
		word-break: break-word;
		overflow-wrap: anywhere;
		font-family: Georgia, "Times New Roman", serif;
		font-size: 12px;
		line-height: 1.4;
		color: #000;
		user-select: text;
	}}
</style>
</head>
<body>
	<div class="text-layer" aria-label="Source page {page_no}">{escaped_text}</div>
</body>
</html>"""


def build_image_only_pdf_html(image_path, page_no):
	width, height = get_image_dimensions(image_path)
	with open(image_path, 'rb') as image_handle:
		image_base64 = base64.b64encode(image_handle.read()).decode('ascii')

	return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
	@page {{ size: {width}px {height}px; margin: 0; }}
	html, body {{
		margin: 0;
		padding: 0;
		width: {width}px;
		height: {height}px;
		background: #fff;
	}}
	.page {{
		position: relative;
		width: {width}px;
		height: {height}px;
	}}
	.page img {{
		display: block;
		width: {width}px;
		height: {height}px;
	}}
	.text-layer {{
		position: absolute;
		inset: 0;
		padding: 24px;
		box-sizing: border-box;
		white-space: pre-wrap;
		word-break: break-word;
		overflow-wrap: anywhere;
		font-family: Georgia, "Times New Roman", serif;
		font-size: 12px;
		line-height: 1.4;
		color: transparent;
		background: transparent;
		opacity: 0.01;
		pointer-events: none;
		user-select: text;
	}}
</style>
</head>
	<body>
	<img src="data:image/png;base64,{image_base64}" alt="Source page {page_no}">
</body>
</html>"""


def wait_for_render_page_assets(render_page, timeout=10000):
	render_page.wait_for_load_state('load', timeout=timeout)
	render_page.locator('img').evaluate(
		"""async image => {
			if (!image.complete) {
				await new Promise(resolve => {
					image.addEventListener('load', resolve, { once: true });
					image.addEventListener('error', resolve, { once: true });
					setTimeout(resolve, 4000);
				});
			}

			if (image.decode) {
				try {
					await image.decode();
				} catch (error) {
				}
			}

			await new Promise(resolve => requestAnimationFrame(() => resolve()));
		}""",
		timeout=timeout,
	)


def write_text_exports(text_pages):
	book_text_path = f"{book_filename}.txt"
	book_jsonl_path = f"{book_filename}.pages.jsonl"

	with open(book_text_path, 'w', encoding='utf-8') as text_handle:
		for index, page_text in enumerate(text_pages, 1):
			if index > 1:
				text_handle.write("\n\n")
			text_handle.write(f"===== PAGE {index} =====\n")
			text_handle.write(page_text)
			text_handle.write("\n")

	with open(book_jsonl_path, 'w', encoding='utf-8') as jsonl_handle:
		for index, page_text in enumerate(text_pages, 1):
			jsonl_handle.write(json.dumps({
				"page": index,
				"text": page_text,
			}, ensure_ascii=False) + "\n")


def merge_pdf_layers(image_pdf_path, text_pdf_path, output_pdf_path):
	if text_pdf_path:
		base_reader = PdfReader(text_pdf_path)
		base_page = base_reader.pages[0]
		image_reader = PdfReader(image_pdf_path)
		base_page.merge_page(image_reader.pages[0])
	else:
		base_reader = PdfReader(image_pdf_path)
		base_page = base_reader.pages[0]

	writer = PdfWriter()
	writer.add_page(base_page)
	with open(output_pdf_path, 'wb') as output_handle:
		writer.write(output_handle)


def render_column_pdf(page, render_page, column, page_no):
	locator = get_column_locator(page, column['side'])
	png_file = os.path.join(cache_dir, f'{page_no}.png')
	image_pdf_file = os.path.join(cache_dir, f'{page_no}.image.pdf')
	text_pdf_file = os.path.join(cache_dir, f'{page_no}.text.pdf')
	pdf_file = os.path.join(cache_dir, f'{page_no}.pdf')
	last_error = None

	for attempt in range(3):
		asset_status = wait_for_column_assets(locator)
		locator.screenshot(path=png_file, animations='disabled')
		is_blank = image_looks_blank(png_file)
		page_text = normalize_text_for_export(column['text'])
		log_debug(
			"page_capture_attempt",
			page_no=page_no,
			attempt=attempt + 1,
			side=column['side'],
			column_hash=column['hash'],
			text_len=len(column['text'].strip()),
			image_count=asset_status.get("imageCount", 0),
			images_ok=asset_status.get("imagesOk", 0),
			images_failed=asset_status.get("imagesFailed", 0),
			is_blank=is_blank,
			text_export_len=len(page_text),
			png_file=os.path.basename(png_file),
			images=asset_status.get("images", []),
		)
		if not is_blank:
			render_page.set_content(build_image_only_pdf_html(png_file, page_no), wait_until='load')
			wait_for_render_page_assets(render_page)
			render_page.pdf(path=image_pdf_file, prefer_css_page_size=True, print_background=True)

			if page_text:
				width, height = get_image_dimensions(png_file)
				render_page.set_content(build_text_overlay_pdf_html(page_text, page_no, width, height), wait_until='load')
				render_page.pdf(path=text_pdf_file, prefer_css_page_size=True, print_background=True)
				merge_pdf_layers(image_pdf_file, text_pdf_file, pdf_file)
			else:
				shutil.copyfile(image_pdf_file, pdf_file)

			return pdf_file, page_text

		if DEBUG_CAPTURE:
			failed_png_file = os.path.join(debug_dir, f'blank-page-{page_no}-attempt-{attempt + 1}.png')
			shutil.copyfile(png_file, failed_png_file)

		last_error = RuntimeError(
			f'Failed to capture renderable page content for page {page_no} on attempt {attempt + 1}.'
		)
		time.sleep(1.2)

	raise last_error


def advance_page(page):
	next_button = page.locator('button.next_btn').first
	if next_button.count() == 0:
		return False

	next_button.click()
	page.wait_for_timeout(1200)
	return True


def main():
	ensure_cache_dir()

	try:
		with sync_playwright() as playwright:
			browser = playwright.chromium.connect_over_cdp(CDP_URL)
			if not browser.contexts:
				raise RuntimeError('No browser context found in the running Chrome instance.')

			render_browser = playwright.chromium.launch(channel="chrome", headless=True)
			render_context = render_browser.new_context()
			render_page = render_context.new_page()

			context = browser.contexts[0]
			page = context.new_page()
			request_failures = []

			def handle_request_failed(request):
				failure = request.failure
				failure_text = failure.get("errorText") if isinstance(failure, dict) else str(failure)
				record = {
					"url": request.url,
					"method": request.method,
					"resource_type": request.resource_type,
					"error": failure_text,
				}
				request_failures.append(record)
				log_debug("request_failed", **record)

			def handle_response(response):
				status = response.status
				if status < 400:
					return

				record = {
					"url": response.url,
					"status": status,
					"resource_type": response.request.resource_type,
					"method": response.request.method,
				}
				log_debug("response_error", **record)

			page.on("requestfailed", handle_request_failed)
			page.on("response", handle_response)

			print('Checking login session...')
			wait_for_login(page)
			print('Logged in successfully.')

			print('Loading viewer...')
			open_reader(page)
			context.storage_state(path="session.json")

			try:
				page_no = 1
				previous_spread_hash = None
				seen_page_hashes = set()
				repeated_spreads = 0
				text_pages = []

				for spread_index in range(MAX_SPREADS):
					columns, spread_hash = get_spread(page, previous_spread_hash)
					if not columns:
						repeated_spreads += 1
						if repeated_spreads >= 2:
							break
					else:
						repeated_spreads = 0
						print(f'Downloading spread {spread_index + 1} ({len(columns)} page columns)')

						for column in columns:
							if column['hash'] in seen_page_hashes:
								log_debug(
									"page_skipped_duplicate",
									page_no=page_no,
									side=column['side'],
									column_hash=column['hash'],
								)
								continue

							_, page_text = render_column_pdf(page, render_page, column, page_no)
							seen_page_hashes.add(column['hash'])
							text_pages.append(page_text)
							page_no += 1

					previous_spread_hash = spread_hash
					if not advance_page(page):
						break

				if page_no == 1:
					raise RuntimeError('No readable page content was captured from the current Everand reader.')

				write_text_exports(text_pages)
			finally:
				log_debug(
					"run_summary",
					captured_pages=page_no - 1,
					failed_requests=len(request_failures),
				)
				render_context.close()
				render_browser.close()
				page.close()
				browser.close()

		print('Merging PDF pages...')
		merger = PdfMerger()
		for current_page in range(1, page_no):
			merger.append(os.path.join(cache_dir, f"{current_page}.pdf"))

		merger.write(f"{book_filename}.pdf")
		merger.close()

		cleanup_cache_dir()
		print('Download completed, enjoy your book!')
	except Exception as exc:
		cleanup_cache_dir()
		raise SystemExit(f'run.py failed: {exc}')


if __name__ == "__main__":
	main()
