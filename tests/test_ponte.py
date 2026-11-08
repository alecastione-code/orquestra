import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from orquestra import cli, db, melhorias, painel, ponte, servidor
from tests.base import Base


class Falso(BaseHTTPRequestHandler):
    estado = {}

    def log_message(self, *a):
        pass

    def _resp(self, cod, obj):
        b = json.dumps(obj).encode()
        self.send_response(cod); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _corpo(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def do_PUT(self):
        E = Falso.estado
        E.setdefault("auth", []).append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != "Bearer tk-certo":
            return self._resp(401, {})
        E["foto"] = self._corpo(); self._resp(204 if False else 200, {})

    def do_GET(self):
        self._resp(200, {"comandos": Falso.estado.get("comandos", [])})

    def do_POST(self):
        Falso.estado.setdefault("feitos", []).append((self.path, self._corpo())); self._resp(200, {})


class TestPonte(Base):
    def setUp(self):
        super().setUp()
        Falso.estado = {}
        self.srv = HTTPServer(("127.0.0.1", 0), Falso)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        os.environ.update(ORQUESTRA_PONTE_URL="http://127.0.0.1:%d" % self.srv.server_address[1], ORQUESTRA_SEM_KEYCHAIN="1")
        for k in ("ORQUESTRA_PONTE_TOKEN",):
            os.environ.pop(k, None)

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        for k in ("ORQUESTRA_PONTE_URL", "ORQUESTRA_SEM_KEYCHAIN", "ORQUESTRA_PONTE_TOKEN"):
            os.environ.pop(k, None)
        super().tearDown()

    def melhoria(self, titulo="Segredo token=ABC123456 " + "x" * 200):
        return melhorias.registrar(melhorias._s("k:" + titulo[:10], titulo, "ev", "prop", "g", "c", "Sonnet", "alto", 5.0), "manual")

    def test_sem_token_nao_faz_nada_e_sai_calado(self):
        self.assertIsNone(ponte.sincronizar())
        self.assertEqual(Falso.estado, {})
        self.assertEqual(cli.main(["ponte"]), 0)

    def test_envia_foto_no_formato_do_contrato_com_textos_curtos_e_sem_segredo(self):
        self.melhoria()
        db.cota_registrar("claude-5h", 40, None, "teste")
        os.environ["ORQUESTRA_PONTE_TOKEN"] = "tk-certo"
        r = ponte.sincronizar()
        self.assertEqual((r["enviada"], r["erro"]), (True, None))
        f = Falso.estado["foto"]
        self.assertEqual(set(f), {"geradoEm", "fila", "custosDia", "cotas", "melhorias"})
        self.assertEqual(set(f["custosDia"]), {"totalBRL", "porModelo", "economiaBRL"})
        self.assertEqual(f["cotas"][0]["agente"], "claude-5h")
        (m,) = f["melhorias"]
        self.assertLessEqual(len(m["titulo"]), 140)
        self.assertNotIn("ABC123456", json.dumps(f))
        self.assertLessEqual(len(json.dumps(f).encode()), 64 * 1024)
        self.assertEqual(Falso.estado["auth"], ["Bearer tk-certo"])

    def test_id_da_fila_cabe_nos_40_do_servidor(self):
        from unittest import mock
        item = {"id": "claude:" + "a" * 80, "nome": "x", "tipo": "claude", "estado": "rodando"}
        with mock.patch("orquestra.aovivo.fotografia", return_value={"itens": [item]}):
            (f,) = ponte.foto()["fila"]
        self.assertEqual(len(f["id"]), 40)

    def test_cota_sem_data_manda_texto_vazio_e_pct_numerico(self):
        cs = [{"agente": "claude-5h", "usado_pct": None, "renova_em": None}]
        from unittest import mock
        rel = {"total_brl": 0, "agrupado": [], "economia_vs_orquestrador": 0}
        with mock.patch("orquestra.db.cotas", return_value=cs), mock.patch("orquestra.custos.relatorio", return_value=rel):
            (c,) = ponte.foto()["cotas"]
        self.assertEqual((c["renovaEm"], c["usadoPct"]), ("", 0))

    def test_aplica_aprovar_e_recusar_e_responde_feito(self):
        a, b = self.melhoria("Aprovar esta"), self.melhoria("Recusar aquela")
        Falso.estado["comandos"] = [{"id": "c1", "tipo": "aprovar_melhoria", "alvo": a}, {"id": "c2", "tipo": "recusar_melhoria", "alvo": b},
                                    {"id": "c3", "tipo": "aprovar_melhoria", "alvo": 9999}, {"id": "c4", "tipo": "apagar_tudo", "alvo": 1}]
        os.environ["ORQUESTRA_PONTE_TOKEN"] = "tk-certo"
        self.assertEqual(ponte.sincronizar()["comandos"], 4)
        est = {m["id"]: m["estado"] for m in melhorias.lista()}
        self.assertEqual((est[a], est[b]), ("aprovada", "recusada"))
        feitos = {p.rsplit("/", 2)[1]: c for p, c in Falso.estado["feitos"]}
        self.assertTrue(feitos["c1"]["ok"] and feitos["c2"]["ok"])
        self.assertFalse(feitos["c3"]["ok"]); self.assertFalse(feitos["c4"]["ok"])
        self.assertTrue(all(len(c["msg"]) <= 200 for c in feitos.values()))

    def test_token_errado_nao_vaza_nem_aplica(self):
        os.environ["ORQUESTRA_PONTE_TOKEN"] = "tk-errado"
        r = ponte.sincronizar()
        self.assertEqual((r["enviada"], r["erro"]), (False, "HTTP 401"))
        self.assertNotIn("tk-errado", json.dumps(r))

    def test_so_fala_https_fora_do_localhost(self):
        os.environ["ORQUESTRA_PONTE_URL"] = "http://exemplo.com"
        os.environ["ORQUESTRA_PONTE_TOKEN"] = "tk-certo"
        self.assertIn("https", ponte.sincronizar()["erro"])


if __name__ == "__main__":
    unittest.main()
