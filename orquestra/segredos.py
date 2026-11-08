"""Redação de segredos em textos curtos exibidos no painel."""
import re

SEGREDO = re.compile(r"(sk-[A-Za-z0-9_-]{10,}|gh[pousr]_\w{10,}|Bearer\s+\S+|"
                     r"(?i:token|secret|senha|password|passwd|api[_-]?key|authorization)\s*[=:]\s*(?:Bearer\s+)?\S+)")


MD = re.compile(r"\[([^\]]*)\]\([^)]*\)|\*\*|__|`")   # link [texto](url) vira texto; negrito e crase somem


def ocultar(txt, n=160):
    txt = SEGREDO.sub("[oculto]", " ".join((txt or "").split()))     # segredo antes: a URL do link some depois
    return MD.sub(lambda m: m.group(1) or "", txt)[:n]
