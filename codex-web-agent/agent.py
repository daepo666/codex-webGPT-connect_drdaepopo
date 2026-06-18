import argparse
import asyncio
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from codex_model import CodexCLIModel


async def get_observation(page, max_elements: int = 60) -> dict:
    """
    현재 페이지의 조작 가능한 요소를 간단히 수집한다.
    각 요소에 data-agent-id를 박아서 나중에 클릭/입력 대상으로 쓴다.
    """
    elements = await page.evaluate(
        """
        (maxElements) => {
          function isVisible(el) {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return (
              style &&
              style.visibility !== "hidden" &&
              style.display !== "none" &&
              rect.width > 0 &&
              rect.height > 0
            );
          }

          function cleanText(s) {
            return (s || "").replace(/\\s+/g, " ").trim().slice(0, 160);
          }

          const selector = [
            "a",
            "button",
            "input",
            "textarea",
            "select",
            "[role=button]",
            "[role=link]",
            "[contenteditable=true]"
          ].join(",");

          const nodes = Array.from(document.querySelectorAll(selector))
            .filter(isVisible)
            .slice(0, maxElements);

          return nodes.map((el, idx) => {
            el.setAttribute("data-agent-id", String(idx));

            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute("role") || "";
            const type = el.getAttribute("type") || "";
            const name = el.getAttribute("name") || "";
            const aria = el.getAttribute("aria-label") || "";
            const placeholder = el.getAttribute("placeholder") || "";
            const value = tag === "input" || tag === "textarea" ? el.value || "" : "";
            const href = el.href || "";
            const text = cleanText(
              el.innerText ||
              el.textContent ||
              aria ||
              placeholder ||
              name ||
              value
            );

            return {
              element_id: idx,
              tag,
              role,
              type,
              name,
              aria_label: aria,
              placeholder,
              value,
              text,
              href: href.slice(0, 200)
            };
          });
        }
        """,
        max_elements,
    )

    title = await page.title()
    url = page.url

    body_text = await page.evaluate(
        """
        () => document.body ? document.body.innerText.replace(/\\s+/g, " ").trim().slice(0, 2000) : ""
        """
    )

    return {
        "url": url,
        "title": title,
        "body_text_preview": body_text,
        "elements": elements,
    }


def build_prompt(goal: str, observation: dict) -> str:
    elements_lines = []
    for el in observation["elements"]:
        label = (
            el.get("text")
            or el.get("aria_label")
            or el.get("placeholder")
            or el.get("name")
            or ""
        )
        elements_lines.append(
            f'[{el["element_id"]}] '
            f'value="{el.get("value", "")}" '
            f'tag={el["tag"]} role={el["role"]} type={el["type"]} '
            f'label="{label}" href="{el.get("href", "")}"'
        )

    elements_text = (
        "\n".join(elements_lines) if elements_lines else "(no interactive elements found)"
    )

    return f"""
너는 브라우저 조작 에이전트의 의사결정 모듈이다.

중요 규칙:
- 반드시 action_schema.json 형식의 JSON 하나만 최종 출력한다.
- 한 번에 action 하나만 선택한다.
- element_id는 아래 Observation에 존재하는 번호만 사용한다.
- 확실하지 않으면 wait 또는 done을 선택한다.

목표:
{goal}

현재 페이지:
URL: {observation["url"]}
TITLE: {observation["title"]}

페이지 본문 미리보기:
{observation["body_text_preview"]}

조작 가능한 요소:
{elements_text}

가능한 action:
- click: element_id 클릭
- type: element_id에 text 입력
- press: 키 입력. 예: Enter, Tab
- scroll: 아래로 스크롤
- wait: 잠깐 기다림
- done: 목표 완료

다음 행동 하나를 JSON으로만 출력해라.
""".strip()


async def execute_action(page, action: dict):
    kind = action["action"]
    element_id = action["element_id"]
    text = action["text"]

    if kind == "done":
        print("[DONE]", action["reason"])
        return True

    if kind == "wait":
        print("[WAIT]", action["reason"])
        await page.wait_for_timeout(1500)
        return False

    if kind == "scroll":
        print("[SCROLL]", action["reason"])
        await page.mouse.wheel(0, 900)
        await page.wait_for_timeout(1000)
        return False

    if kind == "press":
        print(f"[PRESS] {text!r} | {action['reason']}")
        await page.keyboard.press(text or "Enter")
        await page.wait_for_timeout(1000)
        return False

    if element_id is None:
        print("[WARN] action needs element_id, but got null. waiting.")
        await page.wait_for_timeout(1000)
        return False

    locator = page.locator(f'[data-agent-id="{element_id}"]').first

    if kind == "click":
        print(f"[CLICK] element_id={element_id} | {action['reason']}")
        await locator.click(timeout=5000)
        await page.wait_for_timeout(1500)
        return False

    if kind == "type":
        print(f"[TYPE] element_id={element_id}, text={text!r} | {action['reason']}")
        await locator.fill(text, timeout=5000)
        await page.wait_for_timeout(500)
        return False

    print(f"[WARN] unknown action: {kind}")
    await page.wait_for_timeout(1000)
    return False


async def run_agent(
    url: str, goal: str, max_steps: int, model_name: str | None, headless: bool
):
    model = CodexCLIModel(
        schema_path="action_schema.json",
        model=model_name,
        sandbox="read-only",
        timeout=180,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        await page.goto(url, wait_until="domcontentloaded")

        for step in range(1, max_steps + 1):
            current_url = page.url
            print(f"\n===== STEP {step}/{max_steps} =====")
            print("URL:", current_url)

            observation = await get_observation(page)
            prompt = build_prompt(goal, observation)

            try:
                action = model.complete_json(prompt)
            except Exception as e:
                print("[ERROR] Codex failed:", e)
                break

            print("[ACTION]", action)

            try:
                done = await execute_action(page, action)
            except PlaywrightTimeoutError as e:
                print("[WARN] Playwright timeout:", e)
                await page.wait_for_timeout(1000)
                done = False
            except Exception as e:
                print("[WARN] action failed:", e)
                await page.wait_for_timeout(1000)
                done = False

            if done:
                break

        await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--model", default=None)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    asyncio.run(
        run_agent(
            url=args.url,
            goal=args.goal,
            max_steps=args.max_steps,
            model_name=args.model,
            headless=args.headless,
        )
    )


if __name__ == "__main__":
    main()
