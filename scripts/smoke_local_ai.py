"""Exercise a bundled server with a local model and a synthetic JSON request."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.upper().endswith("_API_KEY")}
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="paper-ai-smoke-") as temp:
        with (Path(temp) / "server.log").open("w+b") as log:
            process = subprocess.Popen(
                [str(args.runtime.resolve()), "--model", str(args.model.resolve()),
                 "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "8192"],
                cwd=temp, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 120
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"런타임 종료 코드: {process.returncode}")
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("모델 준비 시간이 초과되었습니다.")
                    time.sleep(0.5)
                payload = {
                    "model": "local", "stream": False, "temperature": 0,
                    "max_tokens": 128,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "messages": [{"role": "user", "content": "Return a JSON object with ok set to true."}],
                    "response_format": {"type": "json_object", "schema": {
                        "type": "object", "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"], "additionalProperties": False,
                    }},
                }
                request = Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
                )
                with opener.open(request, timeout=120) as response:
                    result = json.load(response)
                content = json.loads(result["choices"][0]["message"]["content"])
                if content != {"ok": True}:
                    raise ValueError("내장 AI의 JSON 응답 검증에 실패했습니다.")
                print("Local model health and JSON inference: OK", flush=True)
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read().decode("utf-8", errors="replace")[-6000:])
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
