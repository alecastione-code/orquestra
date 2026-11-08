"""Ações sobre processos de agentes (só `codex exec` conhecidos): pausar, retomar, encerrar, redistribuir."""
import os
import re
import signal

from . import aovivo, db

class _Alvo:
    @staticmethod
    def search(cmd):
        from . import coletores
        return coletores.eh_codex_exec(cmd)


ALVO = _Alvo()


def _raiz(pid, ps_txt=None):
    rows = aovivo.parse_ps(ps_txt if ps_txt is not None else aovivo.ps_arvore())
    r = next((x for x in rows if x["pid"] == pid), None)
    if not r:
        raise KeyError("processo %d não existe mais" % pid)
    if not ALVO.search(r["cmd"]):
        raise PermissionError("pid %d não é um `codex exec`: o Orquestra só age em agentes que ele conhece" % pid)
    return rows, r


def _pid(item_id):
    m = re.match(r"^(?:codex:)?(\d+)$", str(item_id))
    if not m:
        raise ValueError("este agente não tem PID (sessão Claude ou tarefa sem processo): não dá para sinalizar")
    return int(m.group(1))


def _sinalizar(item_id, sinal, nome, tarefa_id=None, ps_txt=None, quem="painel"):
    pid = _pid(item_id)
    rows, _ = _raiz(pid, ps_txt)
    alvos = [r["pid"] for r in aovivo.arvore(rows, pid)]
    for p in alvos:
        try:
            os.kill(p, sinal)
        except ProcessLookupError:
            pass
    with db.conectar() as con:
        db.evento(con, "acao", "%s: %s pid %d (%d processos) por %s" % (nome, item_id, pid, len(alvos), quem))
    return {"ok": True, "acao": nome, "pid": pid, "processos": len(alvos)}


def pausar(item_id, tarefa_id=None, **k):
    r = _sinalizar(item_id, signal.SIGSTOP, "pausar (SIGSTOP)", **k)
    if tarefa_id:
        db.tarefa_pausar(tarefa_id, "pausada pelo painel (SIGSTOP)")
    return r


def retomar(item_id, tarefa_id=None, **k):
    r = _sinalizar(item_id, signal.SIGCONT, "retomar (SIGCONT)", **k)
    if tarefa_id:
        db.tarefa_retomar(tarefa_id)
    return r


def encerrar(item_id, confirmar=False, **k):
    if confirmar is not True:
        raise ValueError("encerrar exige confirmação (confirmar: true)")
    return _sinalizar(item_id, signal.SIGTERM, "encerrar (SIGTERM)", **k)


def redistribuir(tarefa_id, executor, titulo=None, nivel=2):
    """Devolve a tarefa à fila para outro executor (não mata o processo antigo: use encerrar)."""
    if not executor.strip():
        raise ValueError("diga o novo executor")
    if tarefa_id:
        t = db.tarefa(tarefa_id)
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET estado='nova', agente='', executor=?, pid=NULL, pausa='', atualizado=? WHERE id=?",
                        (executor, db.agora(), int(tarefa_id)))
            db.evento(con, "acao", "redistribuir #%s: %s → %s" % (tarefa_id, t["agente"] or t["executor"] or "?", executor))
        return {"ok": True, "tarefa": int(tarefa_id)}
    novo = db.tarefa_nova(titulo or "tarefa redistribuída", nivel, executor=executor)
    with db.conectar() as con:
        db.evento(con, "acao", "redistribuir: nova tarefa #%d para %s" % (novo, executor))
    return {"ok": True, "tarefa": novo}
