#!/bin/bash
# Hook do Claude Code: registra pulso (sessão, projeto, modelo, ferramenta, hora). Silencioso; sempre sai com 0.
f="$0"
while [ -L "$f" ]; do d="$(cd "$(dirname "$f")" && pwd)"; f="$(readlink "$f")"; [[ "$f" != /* ]] && f="$d/$f"; done
raiz="$(cd "$(dirname "$f")/.." && pwd)"
PYTHONPATH="$raiz" /usr/bin/python3 -m orquestra.hook >/dev/null 2>&1 || true
exit 0
