import unittest

from orquestra.cerebro import recomendar


def cota(agente="codex", restante=60.0, acima=False):
    return {"agente": agente, "restante": restante, "acima_ritmo": acima, "consumo_hoje": 20.0, "ritmo_dia": 10.0}


class TestCerebro(unittest.TestCase):
    def test_varrer_logs_e_nivel_1_haiku(self):
        r = recomendar("varrer logs")
        self.assertEqual(r["nivel"], 1)
        self.assertIn("haiku", r["modelo"])

    def test_consertar_bug_com_teste_e_nivel_2_sonnet(self):
        r = recomendar("consertar bug com teste")
        self.assertEqual(r["nivel"], 2)
        self.assertIn("sonnet", r["modelo"])

    def test_decidir_preco_e_nivel_3_com_dono(self):
        r = recomendar("decidir preço")
        self.assertEqual(r["nivel"], 3)
        self.assertTrue(r["dono"])

    def test_checagem_mecanica_e_nivel_0(self):
        self.assertEqual(recomendar("rodar um comando git status")["nivel"], 0)

    def test_navegador_vai_para_codex(self):
        r = recomendar("conferir no painel do marketplace logado no navegador")
        self.assertIn("Codex", r["executor"])

    def test_cota_acima_do_ritmo_desce_nivel(self):
        base = recomendar("consertar bug com teste", [cota()])
        desceu = recomendar("consertar bug com teste", [cota(acima=True)])
        self.assertEqual(base["nivel"], 2)
        self.assertEqual(desceu["nivel"], 1)
        self.assertTrue(desceu["desceu"])
        self.assertIn("ritmo", desceu["porque"])

    def test_decisao_nao_desce(self):
        self.assertEqual(recomendar("decidir preço", [cota(acima=True)])["nivel"], 3)

    def test_explica_em_uma_linha(self):
        for d in ("varrer logs", "consertar bug", "decidir preço", "coisa estranha"):
            self.assertNotIn("\n", recomendar(d)["porque"])


if __name__ == "__main__":
    unittest.main()
