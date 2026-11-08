import http.server
import json
import os
import signal
import stat
import subprocess
import threading
import time
import unittest
from unittest import mock

from orquestra import acoes, aovivo, db, notificar
from tests.base import Base


def estado(pid):
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


class TestAcoes(Base):
    def setUp(self):
        super().setUp()
        self.script = self.escreve("bin/codex", "#!/bin/sh\nsleep 60\n")
        os.chmod(self.script, 0o755)
        self.p = subprocess.Popen([self.script, "exec", "-m", "x"], start_new_session=True)
        time.sleep(0.4)

    def tearDown(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)       # o script + o `sleep 60` filho (sem deixar órfãos)
        except ProcessLookupError:
            pass
        self.p.wait()
        super().tearDown()

    def test_pausar_retomar_encerrar_com_registro(self):
        iid = "codex:%d" % self.p.pid
        t = db.tarefa_nova("x"); db.tarefa_pegar(t, iid)
        acoes.pausar(iid, t)
        time.sleep(0.3)
        self.assertTrue(estado(self.p.pid).startswith("T"))
        self.assertIn("SIGSTOP", db.tarefa(t)["pausa"])
        acoes.retomar(iid, t)
        time.sleep(0.3)
        self.assertFalse(estado(self.p.pid).startswith("T"))
        self.assertEqual(db.tarefa(t)["pausa"], "")
        with self.assertRaises(ValueError):
            acoes.encerrar(iid)  # sem confirmação
        self.assertIsNone(self.p.poll())
        acoes.encerrar(iid, True)
        self.assertIsNotNone(self.p.wait(timeout=5))
        tipos = [e["texto"] for e in db.eventos() if e["tipo"] == "acao"]
        self.assertEqual(len(tipos), 3)

    def test_recusa_processo_que_nao_e_codex_exec(self):
        outro = subprocess.Popen(["sleep", "30"])
        try:
            with self.assertRaises(PermissionError):
                acoes.pausar("codex:%d" % outro.pid)
        finally:
            outro.kill(); outro.wait()

    def test_recusa_sessao_claude_sem_pid(self):
        with self.assertRaises(ValueError):
            acoes.pausar("claude:abc123")

    def test_redistribuir_devolve_a_fila(self):
        t = db.tarefa_nova("x", executor="Codex"); db.tarefa_pegar(t, "codex:1")
        acoes.redistribuir(t, "Claude Sonnet")
        r = db.tarefa(t)
        self.assertEqual((r["estado"], r["agente"], r["executor"]), ("nova", "", "Claude Sonnet"))
        self.assertTrue([e for e in db.eventos() if "redistribuir" in e["texto"]])


class Captura(http.server.BaseHTTPRequestHandler):
    corpos = []

    def do_POST(self):
        Captura.corpos.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.send_response(200); self.end_headers()

    def log_message(self, *a):
        pass


class TestNotificar(Base):
    def test_mac_usa_osascript_display_notification(self):
        with mock.patch("orquestra.notificar.subprocess.run") as run:
            canais = notificar.enviar("Orquestra", 'texto "com aspas"')
        self.assertEqual(canais, ["mac"])
        args = run.call_args[0][0]
        self.assertEqual(args[:2], ["osascript", "-e"])
        self.assertIn("display notification", args[2])
        self.assertNotIn('texto "com', args[2])  # aspas escapadas

    def test_webhook_opcional_so_quando_configurado(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), Captura)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.escreve("orqhome/config/notificacao.json", json.dumps({"mac": False, "webhook_url": "http://127.0.0.1:%d/x" % srv.server_address[1]}))
        os.environ["ORQUESTRA_HOME"] = os.path.join(self.tmp, "orqhome")
        try:
            self.assertEqual(notificar.enviar("T", "alerta de teste"), ["webhook"])
            self.assertEqual(Captura.corpos[-1], "alerta de teste")
        finally:
            os.environ.pop("ORQUESTRA_HOME"); srv.shutdown()

    def test_sem_config_nao_inventa_servico(self):
        os.environ["ORQUESTRA_HOME"] = os.path.join(self.tmp, "vazio")
        try:
            with mock.patch("orquestra.notificar.subprocess.run"), mock.patch("orquestra.notificar.urllib.request.urlopen") as u:
                notificar.enviar("T", "x")
            u.assert_not_called()
        finally:
            os.environ.pop("ORQUESTRA_HOME")

    def test_alerta_travada_dispara_notificacao(self):
        with mock.patch("orquestra.notificar.enviar") as env:
            am = aovivo.Amostrador()
            am.estados["codex:1"] = "ativa"
            am._alertar([{"id": "codex:1", "estado": "travada", "nome": "n", "motivo": "m", "titulo": "t"}])
        env.assert_called_once()


if __name__ == "__main__":
    unittest.main()
