import json
import threading
import unittest
import urllib.error
import urllib.request

from orquestra import servidor
from tests.base import Base


class TestServidor(Base):
    def setUp(self):
        super().setUp()
        self.srv = servidor.criar(porta=0)
        self.p = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def chama(self, caminho, corpo=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request("http://127.0.0.1:%d%s" % (self.p, caminho), data=json.dumps(corpo).encode() if corpo is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as x:
                return x.status, json.loads(x.read() or b"null") if "json" in x.headers["Content-Type"] else x.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_visao_e_tarefa(self):
        c, v = self.chama("/api/visao")
        self.assertEqual(c, 200)
        self.assertIn("agentes", v)
        c, r = self.chama("/api/tarefas", {"titulo": "t", "nivel": 1})
        self.assertEqual(c, 200)
        self.chama("/api/tarefas/%d" % r["id"], {"acao": "pegar", "agente": "x"})
        self.assertEqual(self.chama("/api/tarefas?estado=pega")[1][0]["agente"], "x")

    def test_skill_exige_confirmacao(self):
        c, e = self.chama("/api/skills", {"nome": "a", "acao": "desativar"})
        self.assertEqual(c, 400)
        self.assertIn("confirma", e["erro"])

    def test_recomendar(self):
        self.assertEqual(self.chama("/api/recomendar", {"descricao": "decidir preço"})[1]["nivel"], 3)

    def test_painel_e_seguranca(self):
        c, corpo = self.chama("/")
        self.assertEqual(c, 200)
        self.assertEqual(self.chama("/../orquestra/db.py")[0], 404)
        self.assertEqual(self.chama("/api/mensagens", {"texto": "oi"}, {"Origin": "http://evil.com"})[0], 403)
        self.assertEqual(self.chama("/api/visao", None, {"Host": "evil.com"})[0], 403)


if __name__ == "__main__":
    unittest.main()
