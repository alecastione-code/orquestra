"""`orq esperar`: bloqueia até um evento e devolve uma linha (para o orquestrador rodar em segundo plano)."""
import os
import time

from . import config, db


def _ultimo_evento():
    with db.conectar() as con:
        r = con.execute("SELECT COALESCE(MAX(id),0) FROM eventos").fetchone()
        return r[0]


def _novos(desde):
    with db.conectar() as con:
        return [dict(r) for r in con.execute("SELECT * FROM eventos WHERE id>? ORDER BY id", (desde,))]


def _tamanho_fila():
    try:
        return os.path.getsize(os.path.join(config.pasta_logs_codex(), "fila-unica.out"))
    except OSError:
        return 0


def _linhas_novas_fila(offset):
    p = os.path.join(config.pasta_logs_codex(), "fila-unica.out")
    try:
        with open(p, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace").splitlines(), f.tell()
    except OSError:
        return [], offset


def esperar(falha=False, merge=False, feita=None, travada=False, timeout=3300, intervalo=2.0, dormir=time.sleep, agora=time.time):
    """Devolve (codigo, linha). 0 = evento; 2 = timeout. Eventos 'alerta' vêm do servidor (ao vivo, gates, desperdício)."""
    if not (falha or merge or feita or travada):
        raise ValueError("diga o que esperar: --falha, --merge, --feita <id> ou --travada")
    visto, offset, fim = _ultimo_evento(), _tamanho_fila(), agora() + timeout
    while True:
        if feita:
            t = db.tarefa(feita)
            if t["estado"] in ("feita", "aguardando"):
                return 0, "FEITA #%d (%s): %s" % (t["id"], t["estado"], " ".join((t["resultado"] or t["titulo"]).split())[:160])
            if t["estado"] == "bloqueada":
                return 0, "BLOQUEADA #%d: %s" % (t["id"], " ".join(t["resultado"].split())[:160])
        for e in _novos(visto):
            visto = e["id"]
            tx = e["texto"]
            if e["tipo"] in ("alerta", "gate") and ((falha and ("FALHOU" in tx or "FALHA" in tx)) or (travada and "TRAVADA" in tx)):
                return 0, tx
        if merge:
            linhas, offset = _linhas_novas_fila(offset)
            for l in linhas:
                if "MERGED" in l:
                    return 0, "MERGE: " + l.strip()[:160]
        if agora() >= fim:
            return 2, "TIMEOUT: nada aconteceu em %d min" % (timeout // 60)
        dormir(intervalo)


def parse_timeout(txt):
    txt = str(txt).strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600}
    if txt[-1] in mult:
        return int(float(txt[:-1]) * mult[txt[-1]])
    return int(float(txt)) * 60  # número puro = minutos
