"""Cotas por IA e por janela: rótulos, alertas por previsão e entrada em lote (`orq cotas-informar`)."""
import json
import re
import unicodedata

from . import db

JANELA_5H = re.compile(r"5\s*-?\s*hour|5\s*h\b|5\s*horas?", re.I)
SEMANAL = re.compile(r"^weekly\s*(?:[·•|:\-–—]\s*(.+))?$", re.I)


def _slug(txt):
    t = unicodedata.normalize("NFD", txt.lower())
    return re.sub(r"[^a-z0-9]+", "-", "".join(c for c in t if unicodedata.category(c) != "Mn")).strip("-")


def agente_da_label(label):
    """'5-hour limit' → claude-5h · 'Weekly · all models' → claude-semanal · 'Weekly · Fable 5.1' → claude-semanal-fable."""
    l = " ".join((label or "").split())
    if JANELA_5H.search(l):
        return "claude-5h", 5.0
    m = SEMANAL.match(l)
    if m:
        modelo = (m.group(1) or "").strip()
        if not modelo or re.match(r"all\s+models?", modelo, re.I) or modelo.lower() in ("todos os modelos", "all"):
            return "claude-semanal", 168.0
        return "claude-semanal-" + (_slug(modelo).split("-")[0] or "modelo"), 168.0
    raise ValueError("label desconhecida: %r (esperava '5-hour limit', 'Weekly · all models' ou 'Weekly · <modelo>')" % label)


def janelas_do_json(dados):
    """Aceita {plan:{windows:[…]}}, {windows:[…]} ou a lista direta. Devolve [(agente, usado, resetsAt, janela_h)]."""
    if isinstance(dados, dict):
        dados = (dados.get("plan") or dados).get("windows", dados.get("windows"))
    if not isinstance(dados, list) or not dados:
        raise ValueError("formato esperado: {\"plan\":{\"windows\":[{\"label\":…,\"percentUsed\":…,\"resetsAt\":…}]}}")
    saida = []
    for w in dados:
        ag, jh = agente_da_label(w.get("label"))
        pct = w.get("percentUsed", w.get("percent_used"))
        if pct is None:
            raise ValueError("janela %r sem percentUsed" % w.get("label"))
        saida.append((ag, float(pct), w.get("resetsAt", w.get("resets_at")), jh))
    return saida


def informar(dados, fonte="claude:informado"):
    """Grava as janelas informadas pela IA (lote). Devolve a lista de agentes atualizados."""
    itens = janelas_do_json(dados)
    for ag, pct, renova, jh in itens:
        db.cota_registrar(ag, pct, renova, fonte, jh)
    return [i[0] for i in itens]


NOMES = {"claude-5h": "Claude · janela de 5 h", "claude-semanal": "Claude · semanal (todos os modelos)", "codex": "Codex · semanal"}


def nome(agente):
    if agente in NOMES:
        return NOMES[agente]
    if agente.startswith("claude-semanal-"):
        return "Claude · semanal · " + agente[len("claude-semanal-"):].capitalize()
    return agente


CURTO = {"claude-5h": "janela de 5 h", "claude-semanal": "semanal", "codex": "semanal"}


def curto(agente):
    if agente in CURTO:
        return CURTO[agente]
    return "semanal · " + agente[len("claude-semanal-"):].capitalize() if agente.startswith("claude-semanal-") else agente


def ia_de(agente):
    return "Claude" if agente.startswith("claude") else "Codex" if agente == "codex" else agente


def idade_texto(s):
    s = int(s)
    return "%d min" % max(s // 60, 0) if s < 3600 else "%d h %02d min" % (s // 3600, s % 3600 // 60)


def alertas(cs=None, codex_procs=0):
    """Faixa vermelha só para risco real de estouro, calculado sobre o uso ESTIMADO quando houver. Uma faixa por IA (alertas
    repetidos do mesmo agente viram uma só). Leitura velha NÃO é alerta: vira etiqueta cinza no cartão."""
    por_ia = {}
    for c in (cs if cs is not None else db.cotas()):
        ia = ia_de(c["agente"])
        partes = por_ia.setdefault(ia, [])
        est = " (estimado)" if c.get("origem") == "estimado" else ""
        if c["estoura"]:
            if c["janela_h"] <= 24:
                partes.append("%s%s: no ritmo da última hora (%.1f pts/h) chega a %d%% antes de renovar" % (curto(c["agente"]), est, c["ritmo_atual"], c["previsao_fim_pct"]))
            else:
                partes.append("%s%s: no ritmo das últimas 24 h (%.1f pts/dia) chega a %d%% antes de renovar" % (curto(c["agente"]), est, c["ritmo_atual"], c["previsao_fim_pct"]))
        if c["agente"] == "codex" and c["restante"] < 40:
            extra = "; %d Codex rodando agora" % codex_procs if codex_procs > 1 else ""
            partes.append("sobra %.0f%% (abaixo de 40%%: no máximo 1 por vez, tarefas pequenas%s)" % (c["restante"], extra))
    return ["%s — %s." % (ia, " · ".join(p)) for ia, p in por_ia.items() if p]
