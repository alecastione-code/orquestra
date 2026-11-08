import json
import os
import time
import unittest
from unittest import mock

from orquestra import aovivo, db, sellerturbo, timeline, vigia
from tests.base import Base

PR_OK = {"number": 1, "title": "ok", "headRefName": "qa/t1", "isDraft": False, "url": "u",
         "statusCheckRollup": [{"name": "a", "status": "COMPLETED", "conclusion": "SUCCESS"}, {"name": "b", "status": "COMPLETED", "conclusion": "SKIPPED"}]}
PR_FALHA = {"number": 2, "title": "ruim", "headRefName": "fix/x", "statusCheckRollup": [{"name": "Release tests 1/2", "status": "COMPLETED", "conclusion": "FAILURE"}]}
PR_RODANDO = {"number": 3, "title": "andando", "headRefName": "t466c-pub", "statusCheckRollup": [{"name": "a", "status": "IN_PROGRESS", "conclusion": ""}]}
PR_OUTRO = {"number": 4, "title": "dependabot", "headRefName": "dependabot/npm/x", "statusCheckRollup": []}
CANCELADO = {"number": 5, "title": "cancelado", "headRefName": "jarvis/y", "statusCheckRollup": [{"name": "a", "status": "COMPLETED", "conclusion": "CANCELLED"}]}


class TestSellerTurbo(Base):
    def test_gate_e_filtro_de_ramos(self):
        r = {p["numero"]: p for p in sellerturbo.resumir_prs([PR_OK, PR_FALHA, PR_RODANDO, PR_OUTRO, CANCELADO])}
        self.assertEqual(set(r), {1, 2, 3, 5})  # dependabot fora
        self.assertEqual((r[1]["gate"], r[2]["gate"], r[3]["gate"], r[5]["gate"]), ("ok", "falhou", "pendente", "ok"))
        self.assertEqual(r[2]["falhos"], ["Release tests 1/2"])

    def test_alerta_so_na_virada_para_falhou(self):
        antes = {1: "ok", 2: "pendente"}
        prs = sellerturbo.resumir_prs([PR_OK, PR_FALHA])
        self.assertEqual([p["numero"] for p in sellerturbo.alertas(prs, antes)], [2])
        self.assertEqual(sellerturbo.alertas(prs, {1: "ok", 2: "falhou"}), [])
        self.assertEqual(sellerturbo.alertas(prs, {}), [])  # primeira leitura não alerta

    def test_atualizar_com_gh_simulado_alerta_e_le_fila_unica(self):
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        self.escreve("logs/fila-unica.out", "17:43 #269 MERGED\n19:45 fim\n")
        pendente = dict(PR_FALHA, statusCheckRollup=[{"name": "t", "status": "IN_PROGRESS"}])
        sellerturbo._gates.clear()
        with mock.patch("orquestra.sellerturbo._rodar", side_effect=[[pendente], [], [PR_FALHA], []]):
            sellerturbo.atualizar(); d = sellerturbo.atualizar()
        self.assertEqual(d["gates_falhos"], 1)
        self.assertEqual(d["fila_unica"][-1], "19:45 fim")
        self.assertTrue([m for m in db.msgs("orquestrador") if "GATE FALHOU no PR #2" in m["texto"]])
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR")

    def test_gh_indisponivel_nao_quebra(self):
        with mock.patch("orquestra.sellerturbo._rodar", side_effect=RuntimeError("sem rede")):
            self.assertEqual(sellerturbo.atualizar()["erro"], "sem rede")


class TestVigia(Base):
    def rows(self, vivo):
        txt = "  900     1  1.0 100 00:05 S    /bin/bash /x/vigia/vigia-faturamento.sh\n" if vivo else "  901 1 0.0 10 00:01 S  zsh\n"
        return aovivo.parse_ps(txt)

    def test_rodada_vira_tarefa_nivel_0_com_duracao_e_status(self):
        self.escreve("logs/vigia/ultimo.json", json.dumps({"medido_em": "2026-11-07T22:00:00Z", "contas": [{"p": {"status": "ok"}}]}))
        vigia.verificar(self.rows(True))
        t = db.tarefas("pega")[0]
        self.assertEqual((t["projeto"], t["nivel"], t["pid"], t["tipo"]), ("vigia", 0, 900, "vigia"))
        time.sleep(0.05)
        vigia.verificar(self.rows(False))
        t = db.tarefas("feita")[0]  # nível 0 fecha direto, sem revisão
        self.assertIn("ok", t["resultado"])
        self.assertGreaterEqual(vigia.rodadas()[0]["duracao"], 0)

    def test_status_de_atencao_quando_alguma_conta_nao_esta_ok(self):
        self.escreve("logs/vigia/ultimo.json", json.dumps({"medido_em": "x", "contas": [{"a": {"status": "ok"}}, {"b": {"status": "queda"}}]}))
        vigia.verificar(self.rows(True)); vigia.verificar(self.rows(False))
        self.assertIn("atenção: queda", db.tarefas("feita")[0]["resultado"])

    def test_nao_duplica_enquanto_roda(self):
        vigia.verificar(self.rows(True)); vigia.verificar(self.rows(True))
        self.assertEqual(len(db.tarefas()), 1)


class TestLinhaDoTempo(Base):
    def test_dia_e_markdown(self):
        t = db.tarefa_nova("Consertar X", nivel=2)
        db.tarefa_pegar(t, "sonnet-1")
        db.tarefa_feita(t, "feito em 3 linhas", modelo="claude-sonnet-5-5", tokens_in=1000, tokens_out=500)
        db.tarefa_revisada(t)
        with db.conectar() as con:
            db.evento(con, "alerta", "TRAVADA codex:1")
        md = timeline.markdown()
        self.assertIn("# Linha do tempo", md)
        self.assertIn("sonnet-1", md)
        self.assertIn("Consertar X", md)
        self.assertIn("feito em 3 linhas", md)
        self.assertIn("TRAVADA codex:1", md)
        self.assertIn("estimativa", md)
        self.assertEqual(timeline.dia("1999-01-01")["itens"], [])


if __name__ == "__main__":
    unittest.main()
