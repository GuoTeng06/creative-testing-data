# -*- coding: utf-8 -*-
"""
换图 RPA v2.2 — CDP 连接已登录 Chrome + 多图 + 只填空位 + 实时进度
通过 CDP 连到已有 Chrome（免开新窗口），直接在当前 tab 操作

用法：
  1. 正常用 Chrome 登录拼多多商家后台
  2. 关闭所有 Chrome 窗口
  3. PowerShell: chrome.exe --remote-debugging-port=9222
  4. 在新开的 Chrome 里登录 PDD（如果掉了的话）
  5. 从看板触发换图
"""
import json
import sys
import os
import time
import argparse
from datetime import datetime
from urllib.parse import urlparse

if os.name == "nt":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(SCRIPT_DIR, "swap_downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

MMS_BASE = "https://mms.pinduoduo.com"
GOODS_EDIT = MMS_BASE + "/goods/goods_edit"

PROGRESS_FILE = None


def set_progress_file(path):
    global PROGRESS_FILE
    PROGRESS_FILE = path


def write_progress(data):
    if PROGRESS_FILE:
        try:
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)


def sanitize_filename(url):
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    if not name or len(name) < 5:
        name = "image_%s.jpg" % int(time.time())
    if "?" in name:
        name = name.split("?")[0]
    return name


def download_images(source_images):
    downloaded = []
    import urllib.request
    for img in source_images:
        url = img.get("image_url", "")
        img_type = img.get("image_type", "")
        if not url:
            continue
        try:
            filename = sanitize_filename(url)
            path = os.path.join(DOWNLOADS_DIR,
                                "src_%s_%s" % (int(time.time()), filename))
            urllib.request.urlretrieve(url, path)
            size = os.path.getsize(path)
            log("  download: %s (%d bytes, %s)" % (filename[:40], size, img_type))
            downloaded.append({"path": path, "type": img_type})
        except Exception as e:
            log("  download failed %s: %s" % (url[:60], e))
    return downloaded


async def find_empty_slot_indices(page):
    empty_indices = await page.evaluate('''() => {
        var selectors = [
            '[class*="image-item"]', '[class*="img-item"]',
            '[class*="image-upload"]', '[class*="upload-item"]',
            '.image-list > div', '[class*="picture"]',
        ];
        var containers = [];
        for (var s = 0; s < selectors.length; s++) {
            var found = document.querySelectorAll(selectors[s]);
            if (found.length >= 5) { containers = Array.from(found); break; }
        }
        if (containers.length === 0) {
            var inputs = document.querySelectorAll('input[type="file"]');
            containers = Array.from(inputs).map(function(el) { return el.closest('div, li, section') || el.parentElement; });
        }
        var empty = [];
        containers.forEach(function(el, i) {
            var img = el.querySelector('img:not([src=""])');
            if (!img) empty.push(i);
        });
        return empty;
    }''')
    return empty_indices


async def try_connect_cdp(port=9222):
    """Try connecting to already-running Chrome via CDP"""
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    try:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:%d" % port)
        log("CDP connected on port %d" % port)
        return p, browser
    except Exception as e:
        await p.stop()
        log("CDP failed on port %d: %s" % (port, e))
        return None, None


async def launch_chrome_with_profile(profile_path):
    """Launch Chrome with persistent profile as fallback"""
    from playwright.async_api import async_playwright
    p = await async_playwright().start()
    try:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            channel="chrome",
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1400, "height": 900},
        )
        log("Chrome launched with profile")
        return p, context, None
    except Exception as e:
        log("Profile locked, trying temp copy: %s" % e)
        import shutil
        tmp = os.path.join(os.environ.get("TEMP", os.path.dirname(profile_path)), "chrome-swap-tmp")
        if os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        try:
            shutil.copytree(profile_path, tmp, symlinks=True, ignore_dangling_symlinks=True)
            context = await p.chromium.launch_persistent_context(
                user_data_dir=tmp,
                channel="chrome",
                headless=False,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                viewport={"width": 1400, "height": 900},
            )
            log("Chrome launched with temp profile")
            return p, context, tmp
        except Exception as e2:
            await p.stop()
            log("Chrome launch failed: %s" % e2)
            return None, None, None


async def ensure_pdd_logged_in(page):
    """Check if PDD is logged in, return True if ok"""
    await page.goto(MMS_BASE + "/goods/goods_list", wait_until="domcontentloaded", timeout=15000)
    await page.wait_for_timeout(2000)
    url = page.url
    if "login" in url.lower() or "passport" in url.lower():
        return False
    return True


async def swap_images(command_json):
    cmd = json.loads(command_json) if isinstance(command_json, str) else command_json
    source = cmd["source"]
    targets = cmd["targets"]

    source_pid = source["product_id"]
    source_images = source["images"]

    log("=" * 50)
    log("swap v2.2 start")
    log("source: %s | images: %d" % (source_pid, len(source_images)))
    log("targets: %d products" % len(targets))

    total_phases = len(targets)
    write_progress({"phase": "downloading", "source": source_pid, "total_targets": total_phases, "completed": 0, "status": "pending"})

    # 1. download
    downloaded = download_images(source_images)
    if not downloaded:
        msg = "All source image downloads failed"
        write_progress({"phase": "failed", "error": msg, "status": "failed"})
        log("FATAL: " + msg)
        return [{"product_id": source_pid, "success": False, "error": msg}]
    log("downloaded %d images" % len(downloaded))

    # 2. connect to Chrome — try CDP first, then profile launch
    p = None
    browser = None
    context = None
    tmp_profile = None

    # try CDP (user's already-running Chrome)
    # try standard ports
    cdp_ports = [9222, 9223, 9224, 9225]
    page = None
    for port in cdp_ports:
        p, browser = await try_connect_cdp(port)
        if browser:
            break

    if browser:
        # CDP connected — use existing tabs
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
    else:
        # fallback: launch Chrome with profile
        profile_path = os.path.join(
            os.environ.get("CHROME_PROFILE", r"C:\Users\s\AppData\Local\Google\Chrome\User Data"),
            os.environ.get("CHROME_PROFILE_DIR", "Default"))
        p, context, tmp_profile = await launch_chrome_with_profile(profile_path)
        if not context:
            msg = "Cannot launch Chrome"
            write_progress({"phase": "failed", "error": msg, "status": "failed"})
            return [{"product_id": source_pid, "success": False, "error": msg}]
        page = await context.new_page()

    if not page:
        msg = "No browser page available"
        write_progress({"phase": "failed", "error": msg, "status": "failed"})
        return [{"product_id": source_pid, "success": False, "error": msg}]

    # 3. check PDD login
    logged_in = await ensure_pdd_logged_in(page)
    if not logged_in:
        msg = "PDD login expired. Close all Chrome, run: chrome.exe --remote-debugging-port=9222, login to PDD, then retry."
        log(msg)
        write_progress({"phase": "failed", "error": msg, "status": "failed"})
        if browser:
            await browser.close()
        elif context:
            await context.close()
        if p:
            await p.stop()
        return [{"product_id": source_pid, "success": False, "error": "PDD login expired"}]
    log("PDD session: OK")

    # 4. process targets
    results = []
    write_progress({"phase": "swapping", "total_targets": total_phases, "completed": 0, "status": "running"})

    for i, target in enumerate(targets):
        target_pid = target["product_id"]
        backend_empty = target.get("empty_slots", 0)
        max_fill = min(len(downloaded), backend_empty)

        log("\n[%d/%d] target: %s (empty_slots=%d max_fill=%d)" % (
            i + 1, total_phases, target_pid, backend_empty, max_fill))

        write_progress({
            "phase": "swapping",
            "total_targets": total_phases,
            "completed": i,
            "current": target_pid,
            "status": "running",
        })

        if max_fill <= 0:
            log("  skip: no empty slots")
            results.append({"product_id": target_pid, "success": True, "message": "No empty slots", "filled": 0})
            continue

        try:
            edit_url = "%s?goods_id=%s" % (GOODS_EDIT, target_pid)
            await page.goto(edit_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            current_url = page.url
            if "login" in current_url.lower() or "passport" in current_url.lower():
                log("  PDD session expired mid-run")
                results.append({"product_id": target_pid, "success": False, "error": "PDD session expired"})
                continue

            empty_indices = await find_empty_slot_indices(page)
            log("  empty slots: %d (indices: %s)" % (len(empty_indices), empty_indices[:10]))

            file_inputs = await page.query_selector_all("input[type='file']")
            log("  file inputs: %d" % len(file_inputs))

            if not file_inputs:
                dbg = os.path.join(DOWNLOADS_DIR, "debug_%s.png" % target_pid)
                await page.screenshot(path=dbg)
                log("  no upload control, screenshot: %s" % dbg)
                results.append({"product_id": target_pid, "success": False, "error": "No upload control found"})
                continue

            filled = 0
            used = set()

            for slot_idx in empty_indices:
                if filled >= max_fill:
                    break
                if slot_idx < len(file_inputs) and slot_idx not in used:
                    try:
                        await file_inputs[slot_idx].set_input_files(downloaded[filled]["path"])
                        log("  OK slot[%d] <- %s" % (slot_idx, downloaded[filled]["type"]))
                        used.add(slot_idx)
                        filled += 1
                        await page.wait_for_timeout(1500)
                    except Exception as e:
                        log("  FAIL slot[%d]: %s" % (slot_idx, e))

            for fi_idx in range(len(file_inputs)):
                if filled >= max_fill:
                    break
                if fi_idx not in used:
                    try:
                        await file_inputs[fi_idx].set_input_files(downloaded[filled]["path"])
                        log("  OK input#%d <- %s (fallback)" % (fi_idx, os.path.basename(downloaded[filled]["path"])[:30]))
                        used.add(fi_idx)
                        filled += 1
                        await page.wait_for_timeout(1500)
                    except Exception as e:
                        log("  FAIL input#%d: %s" % (fi_idx, e))

            if filled == 0:
                results.append({"product_id": target_pid, "success": False, "error": "Could not upload any image"})
                continue

            saved = False
            for sel in ["button:has-text('save')", "button:has-text('submit')", "button:has-text('publish')", "[class*='save']", "[class*='submit']"]:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        log("  saved")
                        saved = True
                        await page.wait_for_timeout(3000)
                        break
                except Exception:
                    continue
            if not saved:
                log("  no save button (may auto-save)")

            results.append({"product_id": target_pid, "success": True, "message": "Filled %d images" % filled, "filled": filled})

        except Exception as e:
            import traceback
            log("  EXCEPTION: %s" % e)
            traceback.print_exc()
            results.append({"product_id": target_pid, "success": False, "error": str(e)})

    # cleanup
    if browser:
        await browser.close()
    elif context:
        await context.close()
    if p:
        await p.stop()

    # summary
    ok = sum(1 for r in results if r.get("success"))
    fail = len(results) - ok
    total_filled = sum(r.get("filled", 0) for r in results if r.get("success"))
    log("\n" + "=" * 50)
    log("DONE: %d success (%d filled) / %d failed" % (ok, total_filled, fail))

    write_progress({
        "phase": "done",
        "total_targets": total_phases,
        "completed": total_phases,
        "ok": ok,
        "fail": fail,
        "total_filled": total_filled,
        "status": "done",
        "results": results,
    })

    result_file = os.path.join(DOWNLOADS_DIR,
        "swap_result_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({"source": source_pid, "source_images": len(source_images),
                   "target_count": total_phases, "ok": ok, "fail": fail,
                   "total_filled": total_filled, "results": results,
                   }, f, ensure_ascii=False, indent=2)
    log("result: %s" % result_file)

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True, help="JSON swap command")
    parser.add_argument("--progress", default="", help="Progress file path")
    args = parser.parse_args()
    if args.progress:
        set_progress_file(args.progress)
    asyncio.run(swap_images(args.command))


if __name__ == "__main__":
    main()
