import io
import json
import os
import time
import unittest

from orquestra import cerebro, cli, coletores, custo, db
from tests.base import Base


def orq(*a):
    out = io.StringIO()
    return cli.main(list(a), out), out.getvalue()


class TestRevisaoObrigatoria(Base):
    def test_nivel_1_e_2_aguardam_revisao_e_0_e_3_fecham(self):
        ids = {n: db.tarefa_nova("t%d" % n, nivel=n) for n in range(4)}
        for n, t in ids.items():
            db.tarefa_pegar(t, "x")
            db.tarefa_feita(t, "ok")
        est = {n: db.tarefa(t)["estado"] for n, t in ids.items()}
        self.assertEqual(est, {0: "feita", 1: "aguardando", 2: "aguardando", 3: "feita"})

    def test_so_revisada_deixa_feita_e_nao_revisa_duas_vezes(self):
        t = db.tarefa_nova("x", nivel=2)
        db.tarefa_pegar(t, "s")
        db.tarefa_feita(t, "ok")
        db.tarefa_revisada(t)
        self.assertEqual(db.tarefa(t)["estado"], "feita")
        with self.assertRaises(ValueError):
            db.tarefa_revisada(t)

    def test_nao_revisa_tarefa_que_nao_terminou(self):
        t = db.tarefa_nova("x", nivel=2)
        with self.assertRaises(ValueError):
            db.tarefa_revisada(t)


class TestPlacar(Base):
    def _entrega(self, nivel, voltas=0, gate=0, modelo="claude-sonnet-5-5", tipo="codigo"):
        t = db.tarefa_nova("t", nivel=nivel, tipo=tipo)
        db.tarefa_pegar(t, "a")
        db.tarefa_feita(t, "ok", modelo=modelo)
        for _ in range(voltas):
            db.tarefa_voltou(t, "ajustar")
            db.tarefa_feita(t, "de novo")
        for _ in range(gate):
            db.tarefa_gate(t)
        db.tarefa_revisada(t)
        return t

    def test_classifica_primeira_voltou_quebrou(self):
        a, b, c = self._entrega(2), self._entrega(2, voltas=1), self._entrega(2, gate=1)
        self.assertEqual([db.tarefa(x)["entrega"] for x in (a, b, c)], ["primeira", "voltou", "quebrou"])
        r = db.placar()[0]
        self.assertEqual((r["total"], r["primeira"], r["voltou"], r["quebrou"]), (3, 1, 1, 1))

    def test_cerebro_sobe_nivel_quando_executor_entrega_mal(self):
        for _ in range(3):
            self._entrega(1, voltas=1, tipo="leitura")
        r = cerebro.recomendar("varrer logs", [], db.placar())
        self.assertEqual(r["nivel"], 2)
        self.assertIn("Placar", r["porque"])

    def test_cerebro_confirma_quando_entrega_bem(self):
        for _ in range(4):
            self._entrega(2)
        r = cerebro.recomendar("consertar bug com teste", [], db.placar())
        self.assertEqual(r["nivel"], 2)
        self.assertEqual(r.get("placar"), "ok")

    def test_comando_placar(self):
        self._entrega(2)
        self.assertIn("1ª" if False else "total 1", orq("placar")[1])


class TestCusto(Base):
    def setUp(self):
        super().setUp()
        self.tab = self.escreve("precos.json", json.dumps({"cambio_brl": 5.0, "modelo_orquestrador": "caro",
                                "modelos": {"barato": {"entrada": 1, "saida": 5}, "caro": {"entrada": 10, "saida": 50}}}))
        os.environ["ORQUESTRA_PRECOS"] = self.tab

    def tearDown(self):
        os.environ.pop("ORQUESTRA_PRECOS", None)
        super().tearDown()

    def test_custo_em_reais(self):
        self.assertEqual(custo.custo_brl("barato", 1_000_000, 200_000), 10.0)  # (1+1)*5
        self.assertIsNone(custo.custo_brl("desconhecido", 1, 1))

    def test_fechar_tarefa_mostra_custou_e_teria_custado(self):
        t = db.tarefa_nova("x", nivel=2)
        db.tarefa_pegar(t, "a")
        _, saida = orq("tarefa", "feita", str(t), "--tokens-in", "1000000", "--tokens-out", "200000", "--modelo", "barato")
        self.assertIn("custou R$ 10.00", saida)
        self.assertIn("teria custado R$ 100.00", saida)
        self.assertIn("estimativa", saida)
        self.assertEqual(custo.economia_dia(), {"valor": 90.0, "tarefas": 1, "estimativa": True})

    def test_fatia_da_cota_do_codex(self):
        agora = time.time()
        with db.conectar() as con:
            con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em) VALUES ('codex',60,?, 'x',?)", (agora + 9e5, agora - 3000))
            con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em) VALUES ('codex',64,?, 'x',?)", (agora + 9e5, agora - 10))
        t = db.tarefa_nova("x", nivel=2, executor="Codex gpt-6-sol")
        db.tarefa_pegar(t, "codex:1")
        with db.conectar() as con:
            con.execute("UPDATE tarefas SET iniciado=? WHERE id=?", (agora - 2900, t))
        db.tarefa_feita(t, "ok", modelo="gpt-6-sol")
        self.assertEqual(custo.resumo(db.tarefa(t))["fatia_cota_codex_pts"], 4.0)

    def test_tokens_janela_do_transcript(self):
        def l(i, ts, o):
            return json.dumps({"timestamp": ts, "message": {"id": i, "model": "barato", "usage": {"input_tokens": 1, "output_tokens": o}}}) + "\n"
        arq = self.escreve("claude/projects/p/s.jsonl", l("a", "2026-11-07T10:00:00Z", 5) + l("b", "2026-11-07T11:00:00Z", 7) + l("b", "2026-11-07T11:00:00Z", 9))
        from datetime import datetime
        ini = datetime.fromisoformat("2026-11-07T10:30:00+00:00").timestamp()
        fim = datetime.fromisoformat("2026-11-07T12:00:00+00:00").timestamp()
        self.assertEqual(coletores.tokens_janela(arq, ini, fim)["barato"]["saida"], 9)


if __name__ == "__main__":
    unittest.main()
