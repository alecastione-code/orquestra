"""Rodadas do vigia (vigia-faturamento.sh) viram tarefas de nível 0, com duração e status."""
import json
import os
import re
import time

from . import config, db

ALVO = re.compile(r"vigia-faturamento")
_estado = {"medido": None}


def _status_ultimo():
    p = os.path.join(config.pasta_vigia(), "ultimo.json")
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
    except (OSError, ValueError):
        return None, "ultimo.json ausente ou ilegível"
    sts = set()

    def anda(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "status" and isinstance(v, str):
                    sts.add(v)
                else:
                    anda(v)
        elif isinstance(o, list):
            for v in o:
                anda(v)
    anda(j)
    ruins = sorted(x for x in sts if x not in ("ok", "sem_dados", "n/a"))
    return j.get("medido_em"), ("atenção: " + ", ".join(ruins)) if ruins else "ok"


def verificar(rows, agora=None):
    """`rows`: saída de aovivo.parse_ps. Abre tarefa quando a rodada começa; fecha quando termina."""
    agora = agora or time.time()
    vivos = {r["pid"] for r in rows if ALVO.search(r["cmd"]) and "grep" not in r["cmd"]}
    abertas = [t for t in db.tarefas("pega") if t["projeto"] == "vigia"]
    for pid in sorted(vivos - {t["pid"] for t in abertas}):
        tid = db.tarefa_nova("Rodada do vigia de faturamento", nivel=0, projeto="vigia", executor="script", tipo="vigia")
        db.tarefa_pegar(tid, "vigia")
        db.tarefa_vincular(tid, pid=pid, log=os.path.join(config.pasta_vigia(), "ultima-rodada.log"))
    for t in abertas:
        if t["pid"] not in vivos:
            medido, status = _status_ultimo()
            db.tarefa_feita(t["id"], "rodada concluída — %s%s" % (status, " (medido_em %s)" % medido if medido else ""))
            with db.conectar() as con:
                db.evento(con, "vigia", "rodada #%d: %s em %ds" % (t["id"], status, agora - (t["iniciado"] or agora)))
                if status.startswith("atenção"):
                    con.execute("UPDATE tarefas SET entrega='quebrou' WHERE id=?", (t["id"],))
    return len(vivos)


def rodadas(limite=20):
    return [dict(t, duracao=int((t["concluido"] or time.time()) - t["iniciado"]) if t["iniciado"] else None)
            for t in db.tarefas(projeto="vigia")[:limite]]
