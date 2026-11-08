"""Skills: listar, ativar e desativar (mover o link; nunca apagar)."""
import os
import re

from . import config

NOME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def frontmatter(texto):
    """Lê o bloco --- do SKILL.md (chave: valor, valores entre aspas e blocos > ou |)."""
    m = re.match(r"^---\s*\n(.*?)\n---", texto, re.S)
    if not m:
        return {}
    saida, chave, modo, buf = {}, None, None, []

    def fecha():
        if chave is not None:
            saida[chave] = (" " if modo == ">" else "\n").join(x.strip() for x in buf).strip() if modo else " ".join(buf).strip()

    for linha in m.group(1).split("\n"):
        k = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", linha)
        if k and not linha.startswith((" ", "\t")):
            fecha()
            chave, v = k.group(1), k.group(2).strip()
            if v in (">", "|", ">-", "|-", ">+", "|+"):
                modo, buf = v[0], []
            else:
                modo, buf = None, [v.strip("'\"")]
        elif chave is not None:
            buf.append(linha)
    fecha()
    return saida


def _primeira_frase(txt):
    txt = " ".join(txt.split())
    m = re.match(r"^(.{20,220}?[.!?])(\s|$)", txt)
    return (m.group(1) if m else txt[:200]).rstrip()


def _gatilho(txt):
    txt = " ".join(txt.split())
    m = re.search(r"\b(Use|Usar|Usa|Whenever|When|Quando|Trigger)\b.*", txt)
    return (m.group(0) if m else txt)[:300]


def efeitos(descricao):
    o_que = _primeira_frase(descricao) if descricao else "sem descrição"
    gat = _gatilho(descricao) if descricao else "sem gatilho declarado"
    return (
        "Ativa: dispara quando — %s" % gat,
        "Desativada: deixa de acontecer — %s Nada mais a carrega sozinho; só voltando com `orq skill ativar`." % o_que,
    )


def _ler(pasta, nome, ativa):
    caminho = os.path.join(pasta, nome)
    md = os.path.join(caminho, "SKILL.md")
    desc, fm = "", {}
    try:
        with open(md, encoding="utf-8", errors="replace") as f:
            fm = frontmatter(f.read(20000))
        desc = fm.get("description", "")
    except OSError:
        pass
    a, d = efeitos(desc)
    return {"nome": nome, "ativa": ativa, "descricao": desc, "se_ativa": a, "se_desativada": d,
            "link": os.readlink(caminho) if os.path.islink(caminho) else None}


def listar():
    saida = []
    for pasta, ativa in ((config.skills_ativas(), True), (config.skills_desativadas(), False)):
        if not os.path.isdir(pasta):
            continue
        for n in sorted(os.listdir(pasta)):
            if n.startswith(".") or not NOME_OK.match(n):
                continue
            if os.path.isdir(os.path.join(pasta, n)):
                saida.append(_ler(pasta, n, ativa))
    return sorted(saida, key=lambda s: s["nome"])


def _mover(nome, de, para):
    if not NOME_OK.match(nome or ""):
        raise ValueError("nome de skill inválido")
    origem, destino = os.path.join(de, nome), os.path.join(para, nome)
    if not os.path.lexists(origem):
        raise KeyError("skill %r não está em %s" % (nome, de))
    if os.path.lexists(destino):
        raise ValueError("já existe %s" % destino)
    os.makedirs(para, exist_ok=True)
    os.rename(origem, destino)  # move o link (ou pasta); nunca apaga


def desativar(nome):
    _mover(nome, config.skills_ativas(), config.skills_desativadas())


def ativar(nome):
    _mover(nome, config.skills_desativadas(), config.skills_ativas())
