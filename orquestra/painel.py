"""Faixas do topo da Visão geral: aviso de passagem, "Esperando você" e resumo do dia em 5 linhas."""
import time
from datetime import datetime

from . import custos, db, executores, trabalhos

LINK_APP = trabalhos.LINK_APP
_cache = {"t": 0.0, "v": None}


def _sessoes_claude(itens):
    return [i for i in itens if i.get("tipo") in ("claude", "orquestrador") and i.get("id", "").startswith("claude:") and i.get("vivo", True)]


def alertas_contexto(itens=None):
    """Aviso de passagem em 3 estados (criando, passou, não passou), lido do estado do hook passar-vez e das sessões do app.
    Sem botão de copiar: o próprio chat cria a sessão nova; só o "não passou" pede o dono. `itens` fica pela assinatura antiga."""
    return trabalhos.passagens()


def esperando(itens, status=None):
    """Sessões em ESPERANDO_DONO, da mais velha (parada há mais tempo) para a mais nova. Some sozinha quando a sessão volta a rodar."""
    ws = [i for i in _sessoes_claude(itens) if i.get("estado") == "esperando_dono"]
    if status is not None:       # só pergunta real (resumo do turno com ask/blocked); relatório de fim de turno não conta
        reais = {t["cli"] for t in status["trabalhos"] if t["estado"] == "esperando_dono" and not t.get("passou_vez")}   # quem passou a vez não espera o dono
        ws = [i for i in ws if i.get("id_completo") in reais]
    ws.sort(key=lambda i: -(i.get("ultimo_movimento_s") or 0))
    return [{"id": i["id"], "titulo": i["nome"], "parada_s": i.get("ultimo_movimento_s") or 0, "frase": i.get("ultima_frase") or i.get("etapa") or "",
             "link": (LINK_APP + i["app_id"]) if i.get("app_id") else None, "projeto": i.get("detalhe", "")} for i in ws]


def _dur(s):
    return "%d min" % (s // 60) if s < 3600 else "%d h %02d min" % (s // 3600, s % 3600 // 60) if s < 86400 else "%d d" % (s // 86400)


def resumo_dia(itens=None, cotas=None, agora=None, max_idade=45, status=None):
    """5 linhas: gasto por papel, tarefas feitas, gate de 1ª, o que espera o dono e as cotas."""
    if not itens and _cache["v"] and time.time() - _cache["t"] < max_idade and agora is None:
        return _cache["v"]
    agora_dt = datetime.fromtimestamp(agora) if agora else datetime.now()
    dia = agora_dt.strftime("%Y-%m-%d")
    rows = custos.linhas({dia})
    papel = executores.resumo_papel(rows)
    ts = db.tarefas()
    d = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d") if t else None
    feitas = [t for t in ts if t["estado"] in ("feita", "aguardando") and d(t["concluido"]) == dia]
    auto = [t for t in feitas if t.get("auto")]
    rev = [t for t in feitas if t["entrega"]]
    gate = "%d%% (%d de %d revisadas)" % (round(100 * sum(1 for t in rev if t["entrega"] == "primeira") / len(rev)), sum(1 for t in rev if t["entrega"] == "primeira"), len(rev)) if rev else "sem tarefa revisada hoje"
    espera = esperando(itens or [], status)
    cs = cotas if cotas is not None else db.cotas()
    pedaco = lambda c: "%s %s%%%s" % ({"claude-5h": "5 h", "claude-semanal": "semanal", "codex": "Codex"}.get(c["agente"], c["agente"].replace("claude-semanal-", "semanal ")),
                                       round(c["usado_pct"]), " ⚠" if c["estoura"] else "")
    linhas = [
        "Gasto equivalente hoje: R$ %.2f — orquestrador R$ %.2f (%d sessão(ões)) · executores R$ %.2f (%d)" % (
            papel["orquestrador"]["brl"] + papel["executores"]["brl"], papel["orquestrador"]["brl"], papel["orquestrador"]["sessoes"],
            papel["executores"]["brl"], papel["executores"]["sessoes"]),
        "Tarefas feitas hoje: %d (%d automáticas dos executores, %d do quadro)" % (len(feitas), len(auto), len(feitas) - len(auto)),
        "Gate verde de 1ª: " + gate,
        ("Esperando você: %d sessão(ões), a mais antiga há %s" % (len(espera), _dur(espera[0]["parada_s"]))) if espera else "Esperando você: ninguém",
        "Cotas: " + (" · ".join(pedaco(c) for c in cs) if cs else "sem leitura")]
    r = {"linhas": linhas, "papel": papel, "feitas": len(feitas), "automaticas": len(auto), "dia": dia}
    if agora is None:
        _cache.update(t=time.time(), v=r)
    return r
