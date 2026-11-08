"""Autopiloto: o Orquestra age sozinho na fila do Seller Turbo. Se `fila-unica.sh` morreu, religa (no máximo 1 vez a cada 10 min)
e registra no quadro. Desliga com ORQUESTRA_AUTOPILOTO=0. Ações dentro do app Claude ficam com as tarefas agendadas."""
import os
import subprocess
import time

from . import config, db

ESPERA = 600           # entre duas religadas
AVISO = 3600           # entre dois avisos de "sem acesso"
_estado = {"ultima": 0, "aviso": 0}


def fila_viva():
    return subprocess.run(["pgrep", "-f", "fila-unica.sh"], capture_output=True).returncode == 0


def religar(pasta):
    subprocess.Popen(["/bin/zsh", "fila-unica.sh"], cwd=pasta, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def _registrar(texto):
    with db.conectar() as con:
        db.evento(con, "autopiloto", texto)


def rodada(agora=None, viva=fila_viva, iniciar=religar):
    if os.environ.get("ORQUESTRA_AUTOPILOTO") == "0":
        return "desligado"
    agora = agora or time.time()
    if viva():
        return "fila viva"
    pasta = config.pasta_logs_codex()
    try:
        with open(os.path.join(pasta, "fila-unica.sh"), "rb") as f:
            f.read(1)
    except OSError as e:
        if agora - _estado["aviso"] >= AVISO:
            _estado["aviso"] = agora
            _registrar("fila parada e sem acesso a %s (%s): no macOS, libere a pasta para o python3 do serviço" % (pasta, e.strerror or e))
        return "sem acesso"
    if agora - _estado["ultima"] < ESPERA:
        return "esperando"
    _estado["ultima"] = agora
    iniciar(pasta)
    _registrar("fila-unica.sh estava parada: religada")
    return "religada"
