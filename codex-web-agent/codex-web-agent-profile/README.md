# codex-web-agent-profile

Codex CLI를 LLM 판단기로 쓰고, Playwright가 브라우저를 조작하는 미니 웹 에이전트 예제입니다.

이 버전은 `--profile .browser-profile` 옵션을 지원합니다. 이 옵션을 쓰면 Playwright가 지정한 폴더를 브라우저 프로필로 사용하고, 사람이 직접 로그인한 세션이 유지됩니다.

## 1. 설치

```bash
codex login status
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 2. 기본 테스트

```bash
python agent.py \
  --url "https://example.com" \
  --goal "이 페이지가 무엇인지 확인하고 목표를 완료했다고 판단하면 done을 선택해라." \
  --max-steps 3 \
  --headless
```

## 3. 전용 로그인 프로필 만들기

처음에는 headless를 빼야 브라우저 창이 뜹니다.

```bash
python agent.py \
  --url "https://gemini.google.com/app" \
  --goal "사용자가 직접 로그인할 수 있게 아무 입력도 하지 말고 wait만 해라. Gemini 채팅 화면이 보이면 done을 선택해라." \
  --profile .browser-profile \
  --max-steps 100
```

뜨는 브라우저에서 사용자가 직접 로그인합니다. 비밀번호/2FA는 사람이 직접 처리합니다.

## 4. 로그인된 프로필 재사용

로그인 완료 후 같은 프로필 폴더를 지정하면 세션이 유지됩니다.

```bash
python agent.py \
  --url "https://gemini.google.com/app" \
  --goal "Gemini 채팅 입력창에 '테스트'라고 입력하고 Enter를 눌러라." \
  --profile .browser-profile \
  --max-steps 5
```

## 5. 주의

- 실제 Chrome 기본 프로필을 쓰지 마세요.
- `.browser-profile` 같은 전용 폴더만 쓰세요.
- 민감한 작업, 결제, 계정 삭제, 비밀번호 변경, 스팸/대량 발송, 보안장치 우회는 하지 마세요.
- ChatGPT/OpenAI 웹 응답 자동 추출용으로 쓰지 마세요.
