# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
"""A fake rex for tests: `python -P -m runtime --model M --jsonl [--local-tools]` speaking the JSONL protocol.

chat "env"   -> final with the argv, cwd and endpoint it was started with (JSON text)
chat "perm"  -> permission perm-N, waits for the reply, then tool_start/tool_end (with a diff) and final <decision>
chat "md"    -> final with a table, images and a mermaid block
chat "slow"  -> tokens until "stop" arrives, then error "interrupted"
chat other   -> tokens "echo: ", <text>, final, context
"""
import json
import os
import sys
import threading

out_lock = threading.Lock()
replies = {}
reply_ready = threading.Condition()
stopped = threading.Event()
counter = [0]


def emit(kind, /, **fields):
    with out_lock:
        sys.stdout.write(json.dumps(dict({"type": kind}, **fields)) + "\n")
        sys.stdout.flush()


def model_arg():
    a = sys.argv
    return a[a.index("--model") + 1] if "--model" in a else ""


def handle_chat(text):
    if text == "env":
        emit("final", text=json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                                       "endpoint": os.environ.get("RUNTIME_LITELLM__BASE_URL")}))
    elif text == "perm":
        with reply_ready:
            counter[0] += 1
            pid = "perm-%d" % counter[0]
        emit("permission", id=pid, tool="bash", kind="exec", summary="run ls", detail="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y",
             args={"command": "ls"}, rule=None, reason="")
        with reply_ready:
            reply_ready.wait_for(lambda: pid in replies, timeout=10)
            decision = replies.get(pid, "timeout")
        emit("tool_start", name="bash", args={"command": "ls"})
        emit("tool_end", ok=decision != "deny", error=None if decision != "deny" else "denied", diff="@@ -1 +1 @@\n-a\n+b")
        emit("final", text=decision)
    elif text == "md":
        emit("final", text="# Title\n\n| a | b |\n|---|--:|\n| 1 | 2 |\n\n![pic](pic.png) and ![out](../x.png)\n\n"
                           "```mermaid\ngraph TD; A-->B\n```\n\n- one\n- two <b>not bold</b>\n")
    elif text == "slow":
        stopped.clear()
        while not stopped.wait(0.05):
            emit("token", text=".")
        emit("error", message="interrupted")
    else:
        emit("token", text="echo: ")
        emit("token", text=text)
        emit("final", text="echo: " + text)
        emit("context", used=900, limit=1000, percent=90, model=model_arg(), source="default", warning=True)


def main():
    sys.stderr.write("fake rex starting\n")
    sys.stderr.flush()
    emit("ready", model=model_arg())
    work = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        kind = item.get("type")
        if kind == "permission_reply":
            with reply_ready:
                replies[item.get("id")] = item.get("decision")
                reply_ready.notify_all()
        elif kind == "stop":
            stopped.set()
        elif kind == "chat":
            t = threading.Thread(target=handle_chat, args=(item.get("text", ""),), daemon=True)
            t.start()
            work.append(t)
        elif kind == "reset":
            emit("reset_ok")
        elif kind == "model":
            emit("model_ok", model=item.get("model"))
        elif kind == "bye":
            emit("bye")
            return 0
    emit("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
