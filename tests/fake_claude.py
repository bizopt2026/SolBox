"""테스트용 가짜 claude CLI. FAKE_CLAUDE_CONTROL 이 가리키는 JSON 파일대로 동작한다."""
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    control_path = Path(os.environ["FAKE_CLAUDE_CONTROL"])
    control = json.loads(control_path.read_text(encoding="utf-8"))
    stdin = sys.stdin.read()

    with open(str(control_path) + ".calls.jsonl", "a", encoding="utf-8") as f:
        entry = {"stdin": stdin, "argv": sys.argv[1:], "cwd": os.getcwd()}
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    result = control.get("result", "")
    if isinstance(result, list):
        count_path = Path(str(control_path) + ".count")
        n = int(count_path.read_text(encoding="utf-8")) if count_path.exists() else 0
        count_path.write_text(str(n + 1), encoding="utf-8")
        result = result[min(n, len(result) - 1)] if result else ""

    mode = control.get("mode", "ok")
    if mode == "sleep":
        time.sleep(control.get("sleep_s", 5))
    elif mode == "garbage":
        print("this is not json")
        return control.get("exit_code", 0)
    elif mode == "error":
        sys.stderr.write(control.get("stderr", ""))
        return control.get("exit_code", 1)
    elif mode == "is_error":
        print(json.dumps({"type": "result", "is_error": True, "result": result}, ensure_ascii=False))
        return control.get("exit_code", 0)

    print(json.dumps({"type": "result", "is_error": False, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
