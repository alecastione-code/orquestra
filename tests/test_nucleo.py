import io
import time
import unittest

from orquestra import cli, db
from tests.base import Base


def orq(*args):
    out = io.StringIO()
    code = cli.main(list(args), out)
    return code, out.getvalue()


class TestBancoCli(Base):
    def test_ciclo_da_tarefa(self):
        _, tid = orq("tarefa", "nova", "Varrer logs", "--nivel", "1", "--projeto", "demo")
        tid = tid.strip()
        self.assertEqual(db.tarefas()[0]["estado"], "nova")
        orq("tarefa", "pegar", tid, "--agente", "haiku-1")
        t = db.tarefas("pega")[0]
        self.assertEqual((t["agente"], t["nivel"], t["projeto"]), ("haiku-1", 1, "demo"))
        orq("tarefa", "feita", tid, "--resultado", "ok em 3 linhas")
        self.assertEqual(db.tarefas("aguardando")[0]["resultado"], "ok em 3 linhas")  # nível 1 espera revisão
        self.assertEqual(db.tarefas("feita"), [])
        orq("tarefa", "revisada", tid)
        self.assertEqual(db.tarefas("feita")[0]["entrega"], "primeira")
        self.assertIn("demo", [p["nome"] for p in db.projetos()])

    def test_bloqueada_e_pegar_inexistente(self):
        _, tid = orq("tarefa", "nova", "x")
        orq("tarefa", "bloqueada", tid.strip(), "--motivo", "sem cota")
        self.assertIn("sem cota", db.tarefas("bloqueada")[0]["resultado"])
        self.assertEqual(orq("tarefa", "pegar", "999", "--agente", "a")[0], 1)

    def test_nivel_invalido(self):
        self.assertEqual(orq("tarefa", "nova", "x", "--nivel", "7")[0], 1)

    def test_mensagens_marcam_lida(self):
        orq("msg", "codex", "cuidado com o arquivo X", "--de", "sonnet")
        _, saida = orq("msgs", "--agente", "codex")
        self.assertIn("cuidado com o arquivo X", saida)
        self.assertEqual(orq("msgs", "--agente", "codex")[1], "")

    def test_cota_ritmo(self):
        agora = time.time()
        orq("cota", "codex", "--usado", "70", "--renova", str(agora + 3 * 86400))
        c = db.cotas()[0]
        self.assertAlmostEqual(c["ritmo_dia"], 10.0, places=0)  # (100-70)/3
        self.assertEqual(c["restante"], 30.0)
        self.assertFalse(c["acima_ritmo"])

    def test_cota_acima_do_ritmo(self):
        agora = time.time()
        with db.conectar() as con:  # leitura de ontem: 50%; hoje 70% = +20 pts > ritmo 10
            con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em) VALUES ('codex',50,?, 'x',?)",
                        (agora + 3 * 86400, agora - 90000))
        db.cota_registrar("codex", 70, agora + 3 * 86400)
        c = db.cotas()[0]
        self.assertAlmostEqual(c["consumo_hoje"], 20, places=1)
        self.assertTrue(c["acima_ritmo"])

    def test_pulso_e_projeto(self):
        orq("pulso", "--agente", "sonnet-2")
        self.assertEqual(db.pulsos_recentes()[0]["agente"], "sonnet-2")
        orq("projeto", "novo", "p1")
        self.assertIn("p1", orq("projeto", "lista")[1])


if __name__ == "__main__":
    unittest.main()
