"""Hook do Claude Code (PostToolUse/Stop): lê o JSON do stdin e registra um pulso. Silencioso; nunca falha a ferramenta."""
import json
import os
import re
import sys


def _modelo(transcript):
    try:
        with open(transcript, "rb") as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 60000))
            achados = re.findall(rb'"model"\s*:\s*"([^"<]+)"', f.read())
        return achados[-1].decode() if achados else "?"
    except Exception:
        return "?"


def processar(texto):
    d = json.loads(texto)
    sid = str(d.get("session_id", ""))[:8] or "?"
    projeto = os.path.basename((d.get("cwd") or "").rstrip("/")) or "?"
    ferramenta = d.get("tool_name") or d.get("hook_event_name") or "?"
    modelo = _modelo(d.get("transcript_path") or "")
    if d.get("hook_event_name") == "Stop":
        ferramenta = "Stop (turno terminou)"
    from . import db
    db.pulso("claude:" + sid, "%s · %s · %s" % (projeto, modelo, ferramenta))
    return sid, projeto, modelo, ferramenta


def main():
    os.environ.setdefault("ORQUESTRA_DB_TIMEOUT", "1")  # nunca trava o Claude esperando o banco
    try:
        processar(sys.stdin.read(200000))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
