import json
import subprocess
import tempfile
from pathlib import Path


class CodexCLIModel:
    def __init__(
        self,
        schema_path: str = "action_schema.json",
        model: str | None = None,
        sandbox: str = "read-only",
        timeout: int = 180,
    ):
        self.schema_path = str(Path(schema_path).resolve())
        self.model = model
        self.sandbox = sandbox
        self.timeout = timeout

    def complete_json(self, prompt: str) -> dict:
        """
        codex exec를 호출해서 JSON 결과를 받아온다.
        API key 없이, codex login 세션을 재사용한다.
        """
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "codex_output.json"

            cmd = [
                "codex",
                "exec",
                "--sandbox",
                self.sandbox,
                "--skip-git-repo-check",
                "--output-schema",
                self.schema_path,
                "-o",
                str(out_path),
            ]

            if self.model:
                cmd += ["-m", self.model]

            cmd.append(prompt)

            result = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=self.timeout,
            )

            if result.returncode != 0:
                raise RuntimeError(
                    "codex exec failed\n"
                    f"STDOUT:\n{result.stdout}\n\n"
                    f"STDERR:\n{result.stderr}"
                )

            if not out_path.exists():
                raise RuntimeError(
                    "codex did not create output file\n"
                    f"STDOUT:\n{result.stdout}\n\n"
                    f"STDERR:\n{result.stderr}"
                )

            raw = out_path.read_text(encoding="utf-8").strip()

            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON from codex:\n{raw}") from e
