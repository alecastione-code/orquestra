"""Fila por orçamento: tarefa `--quando-sobrar` espera enquanto a cota está acima do ritmo do dia."""
from . import db

MOTIVO = "cota acima do ritmo do dia (%s); libera sozinha quando a cota renovar ou o ritmo voltar"


def acima_do_ritmo(cotas=None):
    return [c for c in (cotas if cotas is not None else db.cotas()) if c["acima_ritmo"]]


def em_espera(t, cotas=None):
    """Motivo (str) se a tarefa deve esperar; None se pode andar."""
    if t["estado"] != "nova" or not t.get("quando_sobrar"):
        return None
    ac = acima_do_ritmo(cotas)
    if not ac:
        return None
    return MOTIVO % ", ".join("%s: %s" % (c["agente"], c.get("texto_ritmo", "acima do ritmo")) for c in ac)


def fila(cotas=None):
    cotas = cotas if cotas is not None else db.cotas()
    return [dict(t, espera=em_espera(t, cotas)) for t in db.tarefas("nova") if em_espera(t, cotas)]


_ultimo = {"espera": None}


def verificar():
    """Chamado em loop: avisa quando tarefas em espera são liberadas (ou entram em espera)."""
    esperando = {t["id"] for t in fila()}
    antes = _ultimo["espera"]
    _ultimo["espera"] = esperando
    if antes is None:
        return
    for tid in sorted(antes - esperando):
        t = db.tarefa(tid)
        if t["estado"] == "nova":
            txt = "Tarefa #%d liberada da fila por orçamento: %s" % (tid, t["titulo"])
            with db.conectar() as con:
                db.evento(con, "fila", txt)
            db.msg("orquestra", "orquestrador", txt)
    for tid in sorted(esperando - antes):
        with db.conectar() as con:
            db.evento(con, "fila", "Tarefa #%d em espera: cota acima do ritmo" % tid)
