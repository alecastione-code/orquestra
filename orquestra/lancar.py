"""`orq lancar codex`: registra a tarefa e lança o `codex exec` com log, PID e a regra de cota."""
import os
import subprocess

from . import coletores, config, db

MODELOS = {"gpt-6-sol": 2, "gpt-6-luna": 1}


def codex_bin():
    return os.path.expanduser(os.environ.get("ORQUESTRA_CODEX_BIN", "~/.local/bin/codex"))


def checar_cota(forcar=False):
    """Recusa se já há 1 Codex ativo e a cota dele está abaixo de 40% de sobra (regra da skill)."""
    if forcar:
        return
    ativos = len(coletores.codex_processos())
    cota = next((c for c in db.cotas() if c["agente"] == "codex"), None)
    if ativos >= 1 and cota and cota["restante"] < 40:
        raise ValueError("recusado: já há %d Codex ativo(s) e a cota está em %.0f%% de sobra (< 40%%). Use --forcar se for urgente." % (ativos, cota["restante"]))


def lancar(brief, modelo, pasta, titulo=None, projeto="", forcar=False):
    if modelo not in MODELOS:
        raise ValueError("modelo deve ser um de: %s" % ", ".join(MODELOS))
    if not os.path.isdir(os.path.expanduser(pasta)):
        raise ValueError("pasta não existe: %s" % pasta)
    if not os.path.isfile(brief):
        raise ValueError("brief não existe: %s" % brief)
    if not os.path.isfile(codex_bin()):
        raise ValueError("codex não encontrado em %s" % codex_bin())
    checar_cota(forcar)
    with open(brief, encoding="utf-8", errors="replace") as f:
        texto = f.read()
    primeira = texto.strip().splitlines()[0] if texto.strip() else ""
    titulo = titulo or (primeira.lstrip("# ").strip() or os.path.basename(brief))[:120]
    tid = db.tarefa_nova(titulo, MODELOS[modelo], projeto, "Codex " + modelo, texto)
    prompt = texto if texto.lstrip().startswith("# ") else "# O%d — %s\n\n%s" % (tid, titulo, texto)
    log_dir = config.pasta_logs_codex()
    os.makedirs(log_dir, exist_ok=True)
    log = os.path.join(log_dir, "O%d.log" % tid)
    env = dict(os.environ, ORQ_ID="O%d" % tid, ORQ_LOG=log)
    saida = open(log, "ab")
    p = subprocess.Popen(
        ["/bin/sh", "-c", '"$@"; echo "$ORQ_ID exit $?" >> "$ORQ_LOG"', "sh", codex_bin(), "exec", "-m", modelo,
         "--sandbox", "danger-full-access", "--skip-git-repo-check", "-C", os.path.expanduser(pasta), prompt],
        stdout=saida, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    saida.close()
    db.tarefa_pegar(tid, "codex-O%d" % tid, forcar=True)
    db.tarefa_vincular(tid, pid=p.pid, log=log, modelo=modelo)
    with db.conectar() as con:
        db.evento(con, "lancar", "Codex %s lançado para #%d (pid sh %d), log %s" % (modelo, tid, p.pid, log))
    return {"tarefa": tid, "pid": p.pid, "log": log, "modelo": modelo}
