import argparse
import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import BrowserContext, Error as PlaywrightError
from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


CHATGPT_URL = "https://chatgpt.com/"
MAX_PARALLEL_PER_PROBLEM = 20
LINE = chr(10)

ASSISTANT_SELECTORS = [
    """[data-message-author-role="assistant"]""",
    """[data-testid^="conversation-turn-"] [data-message-author-role="assistant"]""",
    """article:has([data-message-author-role="assistant"])""",
]

SYSTEM_PROMPT = (
    "You are one of several independent expert solvers helping Codex solve a CTF challenge. "
    "Work only on authorized CTF or lab material described by the user. "
    "Be concrete: identify likely category, attack surface, next commands, scripts, and flag format. "
    "If you need files or command output that were not provided, say exactly what to ask Codex to run. "
    "Do not invent a flag. If you find a likely flag, put it on a line starting with FINAL_FLAG:."
)

STRATEGIES = [
    "Generalist solver: quickly triage the challenge and propose the highest yield path.",
    "Web solver: focus on HTTP, auth, injection, SSRF, XSS, deserialization, and logic bugs.",
    "Pwn solver: focus on binary exploitation, mitigations, memory corruption, and exploit scripts.",
    "Reverse solver: focus on static or dynamic reversing, algorithms, obfuscation, and patching.",
    "Crypto solver: focus on primitives, parameter mistakes, encodings, RNG, and math attacks.",
    "Forensics solver: focus on file formats, metadata, steganography, packet traces, and carving.",
    "Misc solver: focus on hidden hints, encodings, services, and puzzle structure.",
    "Exploit engineer: turn the most plausible theory into exact commands or Python code.",
    "Skeptical reviewer: find false assumptions and alternate interpretations.",
    "Flag hunter: prioritize extracting or verifying the final flag with minimal steps.",
]


@dataclass
class Challenge:
    challenge_id: str
    title: str
    prompt: str
    url: Optional[str] = None
    category: Optional[str] = None
    attachments: Optional[List[str]] = None


@dataclass
class SolverResult:
    challenge_id: str
    worker_id: int
    ok: bool
    response: str
    final_flag: Optional[str]
    url: str
    error: Optional[str] = None


def challenge_from_obj(obj: Dict[str, Any], default_id: str) -> Challenge:
    prompt = obj.get("prompt") or obj.get("description") or obj.get("body")
    if not prompt:
        raise SystemExit(f"Challenge {default_id} is missing prompt, description, or body.")
    return Challenge(
        challenge_id=str(obj.get("id") or obj.get("challenge_id") or default_id),
        title=str(obj.get("title") or obj.get("name") or f"challenge-{default_id}"),
        prompt=str(prompt),
        url=obj.get("url"),
        category=obj.get("category"),
        attachments=list(obj.get("attachments") or []),
    )


def read_challenges(path: Optional[Path], inline_prompt: Optional[str]) -> List[Challenge]:
    if inline_prompt:
        return [Challenge("inline", "Inline challenge", inline_prompt)]
    if not path:
        raise SystemExit("Provide --prompt or --challenges.")
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        challenges = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if line.strip():
                challenges.append(challenge_from_obj(json.loads(line), str(lineno)))
        return challenges
    if suffix == ".json":
        obj = json.loads(raw)
        items = obj if isinstance(obj, list) else obj.get("challenges", [])
        return [challenge_from_obj(item, str(index + 1)) for index, item in enumerate(items)]
    return [Challenge(path.stem, path.stem, raw.strip())]


def build_solver_prompt(challenge: Challenge, worker_id: int) -> str:
    strategy = STRATEGIES[(worker_id - 1) % len(STRATEGIES)]
    attachments = challenge.attachments or []
    attachment_lines = [f"- {item}" for item in attachments] or ["(none provided)"]
    lines = [
        SYSTEM_PROMPT,
        "",
        f"Independent attempt: {worker_id}",
        f"Specialization: {strategy}",
        "",
        "Challenge:",
        f"- id: {challenge.challenge_id}",
        f"- title: {challenge.title}",
        "- category: " + (challenge.category or "unknown"),
        "- url: " + (challenge.url or "not provided"),
        "",
        "Attachments or local paths:",
        LINE.join(attachment_lines),
        "",
        "Prompt:",
        challenge.prompt,
        "",
        "Return compact solver notes for Codex. Include exact commands or scripts when useful.",
    ]
    return LINE.join(lines)


async def find_prompt_box(page: Page) -> Locator:
    candidates = [
        page.locator("#prompt-textarea").first,
        page.locator("""[data-testid="prompt-textarea"]""").first,
        page.locator("""textarea[placeholder*="Message"]""").first,
        page.locator("textarea").first,
        page.locator("""[contenteditable="true"]""").last,
        page.get_by_role("textbox").last,
    ]
    for locator in candidates:
        try:
            await locator.wait_for(state="visible", timeout=2500)
            return locator
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError("Could not find the ChatGPT prompt box. Is this profile logged in?")


async def submit_prompt(page: Page, prompt: str) -> None:
    box = await find_prompt_box(page)
    await box.click(timeout=5000)
    tag_name = await box.evaluate("el => el.tagName.toLowerCase()")
    if tag_name == "textarea":
        await box.fill(prompt)
    else:
        await page.keyboard.insert_text(prompt)
    await page.keyboard.press("Enter")


async def has_stop_button(page: Page) -> bool:
    selectors = [
        """button[aria-label*="Stop"]""",
        """button[data-testid*="stop"]""",
        """button:has-text("Stop")""",
    ]
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=200):
                return True
        except PlaywrightError:
            continue
    return False


async def assistant_message_count(page: Page) -> int:
    for selector in ASSISTANT_SELECTORS:
        try:
            count = await page.locator(selector).count()
            if count:
                return count
        except PlaywrightError:
            continue
    return 0


async def extract_latest_assistant_text(page: Page, min_count: int = 1) -> str:
    for selector in ASSISTANT_SELECTORS:
        locator = page.locator(selector)
        try:
            count = await locator.count()
            if count < min_count:
                continue
            text = await locator.nth(count - 1).inner_text(timeout=1000)
            text = clean_response(text)
            if text:
                return text
        except PlaywrightError:
            continue
    return ""


def clean_response(text: str) -> str:
    text = text.strip()
    text = text.replace("ChatGPT can make mistakes. Check important info.", "")
    text = text.replace("ChatGPT can make mistakes.", "")
    while LINE + LINE + LINE in text:
        text = text.replace(LINE + LINE + LINE, LINE + LINE)
    return text.strip()


def extract_final_flag(text: str) -> Optional[str]:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("FINAL_FLAG:"):
            return line.split(":", 1)[1].strip().split()[0]
    for token in text.replace(LINE, " ").split():
        token = token.strip(" ,.;:()[]<>")
        if "{" in token and "}" in token and len(token) <= 240:
            return token
    return None


async def wait_for_generation(page: Page, timeout_ms: int, baseline_count: int) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    last_text = ""
    stable_count = 0
    while asyncio.get_running_loop().time() < deadline:
        await page.wait_for_timeout(1500)
        text = await extract_latest_assistant_text(page, min_count=baseline_count + 1)
        if not text:
            continue
        if text == last_text:
            stable_count += 1
        else:
            stable_count = 0
            last_text = text
        if stable_count >= 2 and not await has_stop_button(page):
            return
    raise TimeoutError(f"Timed out waiting for ChatGPT response after {timeout_ms} ms")


async def solve_once(context: BrowserContext, challenge: Challenge, worker_id: int, wait_timeout_ms: int) -> SolverResult:
    page = await context.new_page()
    try:
        await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        baseline_count = await assistant_message_count(page)
        await submit_prompt(page, build_solver_prompt(challenge, worker_id))
        await wait_for_generation(page, wait_timeout_ms, baseline_count)
        response = await extract_latest_assistant_text(page, min_count=baseline_count + 1)
        return SolverResult(challenge.challenge_id, worker_id, True, response, extract_final_flag(response), page.url)
    except Exception as exc:
        return SolverResult(challenge.challenge_id, worker_id, False, "", None, page.url, repr(exc))
    finally:
        await page.close()


def safe_name(value: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    chars = [char if char in allowed else "_" for char in value]
    return "".join(chars) or "challenge"


def write_results(output_dir: Path, challenge: Challenge, results: List[SolverResult]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{safe_name(challenge.challenge_id)}.{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as fp:
        for result in sorted(results, key=lambda item: item.worker_id):
            fp.write(json.dumps(result.__dict__, ensure_ascii=False) + LINE)
    return path


def summarize_flags(results: List[SolverResult]) -> None:
    flags: Dict[str, int] = {}
    for result in results:
        if result.final_flag:
            flags[result.final_flag] = flags.get(result.final_flag, 0) + 1
    if not flags:
        print("no explicit FINAL_FLAG candidates found")
        return
    print("flag candidates:")
    for flag, count in sorted(flags.items(), key=lambda item: (-item[1], item[0])):
        print(f"- {flag} ({count})")


async def solve_challenge(context: BrowserContext, challenge: Challenge, parallel: int, wait_timeout_ms: int, output_dir: Path) -> None:
    print(f"starting {parallel} parallel ChatGPT chats for {challenge.challenge_id}: {challenge.title}")
    tasks = [asyncio.create_task(solve_once(context, challenge, worker_id, wait_timeout_ms)) for worker_id in range(1, parallel + 1)]
    results = []
    for task in asyncio.as_completed(tasks):
        result = await task
        results.append(result)
        status = "ok" if result.ok else "error"
        flag = f" flag={result.final_flag}" if result.final_flag else ""
        print(f"[{challenge.challenge_id} #{result.worker_id}] {status}{flag}")
    path = write_results(output_dir, challenge, results)
    print(f"saved {len(results)} responses to {path}")
    summarize_flags(results)


async def run(args: argparse.Namespace) -> None:
    parallel = min(args.parallel, MAX_PARALLEL_PER_PROBLEM)
    if parallel < 1:
        raise SystemExit("--parallel must be at least 1")
    if args.parallel > MAX_PARALLEL_PER_PROBLEM:
        print(f"--parallel capped at {MAX_PARALLEL_PER_PROBLEM} per problem")
    challenges = [] if args.login_check else read_challenges(args.challenges, args.prompt)
    launch_kwargs: Dict[str, Any] = {"headless": args.headless, "viewport": {"width": args.width, "height": args.height}, "accept_downloads": True}
    if args.channel:
        launch_kwargs["channel"] = args.channel
    if args.slow_mo:
        launch_kwargs["slow_mo"] = args.slow_mo
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(user_data_dir=str(args.user_data_dir), **launch_kwargs)
        try:
            if args.login_check:
                page = await context.new_page()
                await page.goto(CHATGPT_URL, wait_until="domcontentloaded")
                print("ChatGPT opened. Log in with this profile, then stop the script with Ctrl-C.")
                while True:
                    await page.wait_for_timeout(60000)
            for challenge in challenges:
                await solve_challenge(context, challenge, parallel, args.wait_timeout_ms, args.output_dir)
                if args.jitter_seconds > 0:
                    await asyncio.sleep(random.uniform(0, args.jitter_seconds))
        finally:
            await context.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run up to 20 parallel authenticated ChatGPT web chats per CTF problem.")
    parser.add_argument("--user-data-dir", type=Path, required=True, help="Browser profile directory that is logged in to ChatGPT.")
    parser.add_argument("--prompt", help="Single inline CTF challenge prompt.")
    parser.add_argument("--challenges", type=Path, help="Challenge file: txt, json, or jsonl.")
    parser.add_argument("--parallel", type=int, default=MAX_PARALLEL_PER_PROBLEM, help="Parallel chats per problem. Hard capped at 20.")
    parser.add_argument("--output-dir", type=Path, default=Path("runs"))
    parser.add_argument("--wait-timeout-ms", type=int, default=180000)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--channel", default=None, help="Optional browser channel, for example chrome.")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--login-check", action="store_true", help="Open ChatGPT and keep the browser alive so you can log in.")
    parser.add_argument("--jitter-seconds", type=float, default=0.0)
    args = parser.parse_args()
    if args.login_check:
        if args.challenges:
            raise SystemExit("--login-check does not use --challenges.")
    elif bool(args.prompt) == bool(args.challenges):
        raise SystemExit("Provide exactly one of --prompt or --challenges.")
    return args


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
