"""Detector de desperdício: sessão de nível 3 gastando modelo caro em leitura/teste em série."""
import json
import re
import time
from datetime import datetime

from . import coletores, db

JANELA_S = 600
LIMITE = 15
LEITURA_TOOLS = {"Read", "Grep", "Glob", "LS", "WebFetch", "WebSearch", "NotebookRead"}
CMD_LEITURA = re.compile(r"^\s*(cat|ls|grep|rg|find|head|tail|wc|git\s+(log|diff|status|show|grep)|gh\s+(pr|run)\s+(view|list|checks)|"
                         r"python3?\s+-m\s+(unittest|pytest)|pytest|npm\s+(run\s+)?test|node\s+--test|curl\s+-s)\b")
_avisados = {}


def contar(arquivo, agora=None, janela=JANELA_S):
    """(leituras, total_tool_use) nos últimos `janela` s do transcript."""
    agora = agora or time.time()
    leit = tot = 0
    for linha in reversed(coletores._cauda_texto(arquivo, 262144).split("\n")):
        if '"tool_use"' not in linha:
            continue
        try:
            o = json.loads(linha)
            ts = datetime.fromisoformat(str(o["timestamp"]).replace("Z", "+00:00")).timestamp()
            c = o["message"]["content"]
        except (ValueError, KeyError, TypeError):
            continue
        if agora - ts > janela:
            break
        for x in c if isinstance(c, list) else []:
            if x.get("type") != "tool_use":
                continue
            tot += 1
            cmd = ((x.get("input") or {}).get("command") or "").strip()
            if x.get("name") in LEITURA_TOOLS or (x.get("name") == "Bash" and CMD_LEITURA.match(cmd)):
                leit += 1
    return leit, tot


def analisar(sessoes, agora=None, limite=LIMITE):
    agora = agora or time.time()
    achados = []
    for s in sessoes:
        if s.get("nivel") != 3 or not s.get("arquivo") or agora - s["ultima_atividade"] > JANELA_S:
            continue
        leit, tot = contar(s["arquivo"], agora)
        if leit > limite:
            achados.append({"sessao": s["id"], "projeto": s["projeto"], "modelo": s["modelo"], "leituras": leit, "total": tot,
                            "janela_min": JANELA_S // 60,
                            "sugestao": "Descer para Haiku (varredura/leitura) ou Sonnet (testes e código): %d leituras/testes em %d min num modelo de nível 3." % (leit, JANELA_S // 60)})
    return achados


def executores_caros(agora=None, horas=24, minimo_brl=0.5):
    """Subagente rodando em modelo de nível 3 (Opus/Fable) em tarefa de executor: Sonnet/Haiku fariam por bem menos."""
    agora = agora or time.time()
    out = []
    with db.conectar() as con:
        for t in con.execute("SELECT * FROM tarefas WHERE auto=1 AND nivel=3 AND executor LIKE 'Claude%' AND iniciado>=? ORDER BY custo_brl DESC", (agora - horas * 3600,)):
            if (t["custo_brl"] or 0) >= minimo_brl:
                out.append({"sessao": t["ref"], "projeto": t["projeto"], "modelo": t["modelo"], "leituras": 0, "total": 0, "tarefa_id": t["id"],
                            "sugestao": "subagente “%s” (#%d) rodou no %s e custou ≈ R$ %.2f equivalente: Sonnet (código) ou Haiku (leitura) bastam." % (
                                t["titulo"][:60], t["id"], t["modelo"], t["custo_brl"])})
    return out


def verificar(sessoes=None, agora=None):
    """Emite evento + mensagem ao orquestrador (uma vez a cada 30 min por sessão) e devolve os achados."""
    agora = agora or time.time()
    achados = analisar(sessoes if sessoes is not None else coletores.retrato().get("claude_sessoes", []), agora)
    for a in achados:
        if agora - _avisados.get(a["sessao"], 0) > 1800:
            _avisados[a["sessao"]] = agora
            txt = "DESPERDÍCIO %s (%s): %s" % (a["projeto"], a["modelo"], a["sugestao"])
            with db.conectar() as con:
                db.evento(con, "desperdicio", txt)
            db.msg("orquestra", "orquestrador", txt)
            try:
                from . import notificar
                notificar.enviar("Orquestra · desperdício", txt)
            except Exception:
                pass
    return achados
