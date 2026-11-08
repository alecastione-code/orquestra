import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import db, desperdicio, orcamento
from tests.base import Base


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def tool(ts, nome, cmd=None, i=[0]):
    i[0] += 1
    inp = {"command": cmd} if cmd else {"file_path": "/x"}
    return json.dumps({"timestamp": iso(ts), "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t%d" % i[0], "name": nome, "input": inp}]}}) + "\n"


class TestDesperdicio(Base):
    def sessao(self, linhas, nivel=3):
        arq = self.escreve("claude/projects/p/s.jsonl", "".join(linhas))
        return {"id": "abc", "projeto": "p", "modelo": "claude-opus-5-5", "nivel": nivel, "arquivo": arq, "ultima_atividade": time.time()}

    def test_mais_de_15_leituras_em_10_min_no_nivel_3_alerta(self):
        agora = time.time()
        ls = [tool(agora - 30 * k, "Read") for k in range(12)] + [tool(agora - 5 * k, "Bash", "git status") for k in range(1, 5)]
        a = desperdicio.analisar([self.sessao(ls)], agora)
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["leituras"], 16)
        self.assertIn("Haiku", a[0]["sugestao"])

    def test_nao_alerta_com_escrita_nivel_baixo_ou_janela_velha(self):
        agora = time.time()
        escrita = [tool(agora - 5 * k, "Edit") for k in range(30)]
        self.assertEqual(desperdicio.analisar([self.sessao(escrita)], agora), [])
        leituras = [tool(agora - 5 * k, "Read") for k in range(30)]
        self.assertEqual(desperdicio.analisar([self.sessao(leituras, nivel=2)], agora), [])
        velhas = [tool(agora - 3600 - k, "Read") for k in range(30)]
        self.assertEqual(desperdicio.analisar([self.sessao(velhas)], agora), [])

    def test_verificar_registra_evento_e_mensagem_uma_vez(self):
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        desperdicio._avisados.clear()
        agora = time.time()
        s = self.sessao([tool(agora - k, "Read") for k in range(20)])
        desperdicio.verificar([s], agora); desperdicio.verificar([s], agora)
        self.assertEqual(len([m for m in db.msgs("orquestrador") if "DESPERDÍCIO" in m["texto"]]), 1)
        self.assertTrue([e for e in db.eventos() if e["tipo"] == "desperdicio"])
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR")


class TestFilaPorOrcamento(Base):
    def acima(self):
        agora = time.time()
        with db.conectar() as con:
            con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em) VALUES ('codex',50,?, 'x',?)", (agora + 3 * 86400, agora - 90000))
        db.cota_registrar("codex", 70, agora + 3 * 86400)

    def test_espera_quando_acima_do_ritmo_e_urgente_passa(self):
        brando = db.tarefa_nova("limpeza", quando_sobrar=True)
        urgente = db.tarefa_nova("urgente")
        self.acima()
        self.assertIn("acima do ritmo", orcamento.em_espera(db.tarefa(brando)))
        self.assertIsNone(orcamento.em_espera(db.tarefa(urgente)))
        with self.assertRaises(ValueError):
            db.tarefa_pegar(brando, "x")
        db.tarefa_pegar(urgente, "x")
        db.tarefa_pegar(brando, "x", forcar=True)

    def test_libera_sozinha_quando_o_ritmo_volta(self):
        brando = db.tarefa_nova("limpeza", quando_sobrar=True)
        self.acima()
        self.assertEqual([t["id"] for t in orcamento.fila()], [brando])
        with db.conectar() as con:  # novo dia: a leitura de hoje vira a base → consumo_hoje = 0
            con.execute("UPDATE cotas SET lido_em=lido_em-200000")
        db.cota_registrar("codex", 70, time.time() + 3 * 86400)
        self.assertEqual(orcamento.fila(), [])
        db.tarefa_pegar(brando, "x")

    def test_aviso_de_liberacao(self):
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        brando = db.tarefa_nova("limpeza", quando_sobrar=True)
        self.acima()
        orcamento._ultimo["espera"] = None
        orcamento.verificar()
        with db.conectar() as con:
            con.execute("UPDATE cotas SET lido_em=lido_em-200000")
        db.cota_registrar("codex", 70, time.time() + 3 * 86400)
        orcamento.verificar()
        self.assertTrue([m for m in db.msgs("orquestrador") if "liberada" in m["texto"]])
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR")


if __name__ == "__main__":
    unittest.main()
