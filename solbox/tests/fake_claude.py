"""테스트용 가짜 claude CLI. FAKE_CLAUDE_CONTROL 이 가리키는 JSON 파일대로 동작한다."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def emit(obj) -> None:
    print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False), flush=True)


def run_stream_mode(control: dict, result: str) -> int:
    if control.get("orphan_s"):  # 새 세션에서 표준 출력을 쥔 채 오래 사는 손자 프로세스 (프로세스 그룹 kill 을 피한다)
        subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({control['orphan_s']})"],
                         start_new_session=True, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chunks = control.get("chunks", [])
    fail_after = control.get("stream_fail_after")
    if control.get("noise_lines"):
        emit("not json at all")
        emit({"type": "system", "subtype": "init"})
        emit({"type": "stream_event", "event": {"type": "message_start"}})
    for i, text in enumerate(chunks):
        if fail_after is not None and i >= fail_after:
            sys.stderr.write(control.get("stderr", ""))
            sys.stderr.flush()
            return control.get("exit_code", 1)
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                                 "delta": {"type": "text_delta", "text": text}}})
        time.sleep(control.get("chunk_delay_s", 0))
    if fail_after is not None:
        sys.stderr.write(control.get("stderr", ""))
        sys.stderr.flush()
        return control.get("exit_code", 1)
    if not control.get("no_result"):
        final = control["result"] if "result" in control else "".join(chunks)
        emit({"type": "result", "subtype": "success", "is_error": bool(control.get("result_is_error")),
              "result": final})
    return control.get("exit_code", 0)


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
        print(control.get("garbage", "this is not json"))
        if control.get("stderr"):
            sys.stderr.write(control["stderr"])
        return control.get("exit_code", 0)
    elif mode == "error":
        sys.stderr.write(control.get("stderr", ""))
        return control.get("exit_code", 1)
    elif mode == "stream":
        argv = sys.argv[1:]
        plain_json = "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
        if plain_json and "json_result" in control:  # 스트리밍이 아닌 실행(대체 경로)이 받는 답
            print(json.dumps({"type": "result", "is_error": False, "result": control["json_result"]}, ensure_ascii=False))
            return 0
        return run_stream_mode(control, result)
    elif mode == "is_error":
        print(json.dumps({"type": "result", "is_error": True, "result": result}, ensure_ascii=False))
        return control.get("exit_code", 0)

    print(json.dumps({"type": "result", "is_error": False, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
