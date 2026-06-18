# ChatGPT CTF Parallel Solver

로그인된 ChatGPT 브라우저 프로필을 Playwright로 열고, CTF 문제 하나당 최대 20개의 독립 채팅을 병렬로 돌리는 도구입니다. 각 채팅은 서로 다른 풀이 관점으로 문제를 분석하고, 결과는 JSONL 파일로 저장됩니다.

## 포함 파일

- `chatgpt_ctf_orchestrator.py`: 실행 스크립트
- `requirements.txt`: Python 의존성
- `README.md`: 이 문서

브라우저 프로필, ChatGPT 로그인 세션, 실행 결과, `.venv`는 압축본에 넣지 않습니다. 각자 자기 계정으로 로그인해서 사용하세요.

## 설치

```bash
cd codex-web-agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

## ChatGPT 로그인 프로필 만들기

```bash
.venv/bin/python chatgpt_ctf_orchestrator.py \
  --user-data-dir ./chatgpt-profile \
  --login-check
```

브라우저가 열리면 ChatGPT에 로그인합니다. 로그인 후 ChatGPT 입력창이 보이면 터미널에서 `Ctrl-C`로 종료합니다. 로그인 세션은 `./chatgpt-profile`에 저장됩니다.

이미 사용 중인 일반 Chrome 프로필을 그대로 쓰면 브라우저 잠금이 걸릴 수 있습니다. 자동화 전용 프로필 디렉터리를 따로 만들어 로그인하는 방식을 권장합니다.

## 문제 하나 실행

```bash
.venv/bin/python chatgpt_ctf_orchestrator.py \
  --user-data-dir ./chatgpt-profile \
  --prompt "여기에 CTF 문제 설명 붙여넣기" \
  --parallel 20
```

`--parallel`은 문제당 병렬 ChatGPT 채팅 수입니다. 20을 넘게 줘도 코드에서 20으로 제한합니다.

## 여러 문제 실행

`challenges.jsonl` 파일을 만들고 문제를 한 줄에 하나씩 넣습니다.

```json
{"id":"web-1","title":"login bypass","category":"web","prompt":"문제 설명","url":"https://target.example"}
```

실행:

```bash
.venv/bin/python chatgpt_ctf_orchestrator.py \
  --user-data-dir ./chatgpt-profile \
  --challenges challenges.jsonl \
  --parallel 20 \
  --output-dir runs
```

## 결과 확인

결과는 `runs/*.jsonl`에 저장됩니다. 각 줄은 한 채팅의 결과이며, `response`에 ChatGPT 답변이 들어갑니다. 답변 안에 `FINAL_FLAG:`가 있거나 플래그처럼 보이는 문자열이 있으면 `final_flag`에도 추출됩니다.

## 주의

- ChatGPT 웹 UI 구조가 바뀌면 입력창이나 응답 추출 selector를 고쳐야 할 수 있습니다.
- 한 계정에서 20개 병렬 채팅을 열면 속도 제한이나 일시적 오류가 날 수 있습니다. 그때는 `--parallel 5`처럼 낮춰서 실행하세요.
- 실제 대회나 허가된 CTF 문제에만 사용하세요.
