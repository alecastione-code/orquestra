import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import cotas, db, estimativa, servidor, transcritos
from tests.base import Base


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def msg(mid, modelo, i=0, o=0, cr=0, cw=0, t=None):
    return json.dumps({"cwd": "/p/x", "timestamp": iso(t), "message": {"id": mid, "role": "assistant", "model": modelo, "content": [],
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}) + "\n"


def leitura(agente, usado, renova, t, janela_h=None, confirmado=None):
    with db.conectar() as con:
        con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em,janela_h,confirmado_em) VALUES (?,?,?,?,?,?,?)",
                    (agente, usado, renova, "teste", t, janela_h or db.janela_do_nome(agente), confirmado or t))


class Base2(Base):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.n = 0

    def tokens(self, linhas):
        """linhas: [(modelo, t, i, o, cr, cw)] numa sessão; roda a análise."""
        self.n += 1
        self.escreve("claude/projects/-p/s%d.jsonl" % self.n, "".join(msg("m%d_%d" % (self.n, k), m, i, o, cr, cw, t) for k, (m, t, i, o, cr, cw) in enumerate(linhas)))
        transcritos.atualizar()

    def cota(self, agente):
        return {c["agente"]: c for c in db.cotas(self.now)}[agente]


class TestPesos(Base2):
    def test_peso_por_modelo_saida_e_cache(self):
        t = self.now - 1800
        self.tokens([("claude-fable-5-1", t, 0, 1000, 0, 0), ("claude-opus-5-5", t, 0, 1000, 0, 0), ("claude-sonnet-5-5", t, 0, 1000, 0, 0), ("claude-haiku-5-5", t, 0, 1000, 0, 0)])
        w = lambda f: estimativa.tokens_ponderados(self.now - 3600, self.now, f)
        self.assertEqual((w("fable"), w("opus"), w("sonnet"), w("haiku")), (25000.0, 15000.0, 5000.0, 1500.0))   # saída 5× × peso do modelo
        self.assertAlmostEqual(w(None), 46500.0)
        # entrada pesa 1, saída 5, cache de leitura quase nada
        self.tokens([("claude-sonnet-5-5", t + 60, 1000, 0, 1_000_000, 0)])
        self.assertAlmostEqual(w("sonnet") - 5000.0, 1000 + 1_000_000 * 0.01)

    def test_pesos_sao_editaveis_em_config(self):
        self.escreve("casa/config/pesos.json", json.dumps({"modelos": {"sonnet": 2.0}, "tokens": {"entrada": 1, "saida": 1, "cache_escrita": 1, "cache_leitura": 0}}))
        os.environ["ORQUESTRA_HOME"] = os.path.join(self.tmp, "casa")
        try:
            self.tokens([("claude-sonnet-5-5", self.now - 600, 100, 100, 0, 0)])
            self.assertEqual(estimativa.tokens_ponderados(self.now - 3600, self.now), 400.0)
        finally:
            os.environ.pop("ORQUESTRA_HOME")


class TestEstimativaCalibrada(Base2):
    def setUp(self):
        super().setUp()
        self.renova = self.now + 4 * 86400

    def test_duas_leituras_calibram_o_fator_e_a_estimativa_cresce_com_os_tokens(self):
        r1, r2 = self.now - 7200, self.now - 3600
        leitura("claude-semanal", 10.0, self.renova, r1)
        leitura("claude-semanal", 20.0, self.renova, r2)
        self.tokens([("claude-sonnet-5-5", self.now - 5400, 0, 100_000, 0, 0),     # entre as leituras: 500k ponderados → +10 pts
                     ("claude-sonnet-5-5", self.now - 1800, 0, 50_000, 0, 0)])       # depois da última: 250k → +5 pts
        c = self.cota("claude-semanal")
        self.assertAlmostEqual(c["fator"], 10.0 / 500_000, places=9)
        self.assertAlmostEqual(c["usado_pct"], 25.0, delta=0.2)
        self.assertEqual((c["confirmado_pct"], c["origem"]), (20.0, "estimado"))
        self.assertFalse(c["desatualizada"])                      # estimativa nunca fica "desatualizada"

    def test_recalibra_a_cada_leitura_nova(self):
        leitura("claude-semanal", 10.0, self.renova, self.now - 10800)
        leitura("claude-semanal", 20.0, self.renova, self.now - 7200)
        self.tokens([("claude-sonnet-5-5", self.now - 9000, 0, 100_000, 0, 0), ("claude-sonnet-5-5", self.now - 5400, 0, 100_000, 0, 0)])
        f1 = self.cota("claude-semanal")["fator"]
        leitura("claude-semanal", 40.0, self.renova, self.now - 3600)          # leitura nova: +20 pts para 500k
        f2 = self.cota("claude-semanal")["fator"]
        self.assertAlmostEqual(f1, 10.0 / 500_000, places=9)
        self.assertAlmostEqual(f2, 30.0 / 1_000_000, places=9)            # média ponderada das calibragens recentes
        self.assertNotEqual(f1, f2)

    def test_uma_leitura_so_calibra_pelo_acumulado_da_janela(self):
        leitura("claude-5h", 15.0, self.now + 3 * 3600, self.now - 600)
        self.tokens([("claude-sonnet-5-5", self.now - 5400, 0, 60_000, 0, 0),      # dentro da janela de 5 h que termina em +3 h (começou há 2 h)
                     ("claude-sonnet-5-5", self.now - 300, 0, 20_000, 0, 0)])
        c = self.cota("claude-5h")
        self.assertAlmostEqual(c["fator"], 15.0 / 300_000, places=9)               # 15% explicados por 300k ponderados
        self.assertAlmostEqual(c["usado_pct"], 15.0 + 5.0, delta=0.3)              # +100k ponderados depois da leitura

    def test_sem_tokens_novos_a_estimativa_e_a_propria_leitura(self):
        leitura("claude-5h", 15.0, self.now + 3 * 3600, self.now - 600)
        c = self.cota("claude-5h")
        self.assertEqual((c["usado_pct"], c["origem"]), (15.0, "confirmado"))

    def test_fable_semanal_so_conta_tokens_do_fable(self):
        leitura("claude-semanal-fable", 9.0, self.renova, self.now - 3600)
        self.tokens([("claude-fable-5-1", self.now - 7200, 0, 10_000, 0, 0)])        # base: 250k ponderados explicam 9%
        antes = self.cota("claude-semanal-fable")["usado_pct"]
        self.tokens([("claude-sonnet-5-5", self.now - 600, 0, 5_000_000, 0, 0), ("claude-opus-5-5", self.now - 600, 0, 5_000_000, 0, 0)])
        self.assertAlmostEqual(self.cota("claude-semanal-fable")["usado_pct"], antes, places=3)    # sonnet/opus não mexem
        self.tokens([("claude-fable-5-1", self.now - 600, 0, 10_000, 0, 0)])
        self.assertGreater(self.cota("claude-semanal-fable")["usado_pct"], antes + 1)               # fable mexe

    def test_janela_geral_conta_todos_os_modelos(self):
        leitura("claude-5h", 10.0, self.now + 3 * 3600, self.now - 3600)
        self.tokens([("claude-sonnet-5-5", self.now - 5400, 0, 20_000, 0, 0)])
        antes = self.cota("claude-5h")["usado_pct"]
        self.tokens([("claude-opus-5-5", self.now - 600, 0, 5_000, 0, 0)])
        self.assertGreater(self.cota("claude-5h")["usado_pct"], antes)

    def test_zera_sozinha_quando_resetsAt_passa_e_recomeca_a_contar(self):
        renova_velha = self.now - 1800                       # a janela de 5 h renovou há 30 min
        leitura("claude-5h", 60.0, renova_velha, renova_velha - 3600)
        leitura("claude-5h", 80.0, renova_velha, renova_velha - 600)
        self.tokens([("claude-sonnet-5-5", renova_velha - 3000, 0, 80_000, 0, 0),         # antes da renovação (calibra)
                     ("claude-sonnet-5-5", self.now - 900, 0, 8_000, 0, 0)])               # depois da renovação: recomeça a contar
        c = self.cota("claude-5h")
        self.assertTrue(c["resetou"])
        self.assertAlmostEqual(c["renova_em"], renova_velha + 5 * 3600, delta=1)           # próxima renovação, 5 h depois
        self.assertEqual(c["confirmado_pct"], 80.0)
        self.assertLess(c["usado_pct"], 20)                                               # não carrega os 80% da janela anterior
        self.assertGreater(c["usado_pct"], 0)                                             # conta só o que veio depois
        self.assertEqual(c["origem"], "estimado")

    def test_zera_mesmo_sem_nenhum_token_novo(self):
        leitura("claude-5h", 80.0, self.now - 1800, self.now - 7200)
        c = self.cota("claude-5h")
        self.assertEqual((c["usado_pct"], c["resetou"]), (0.0, True))

    def test_estimativa_alimenta_o_alerta_com_etiqueta_estimado(self):
        leitura("claude-5h", 40.0, self.now + 3600, self.now - 1800)             # 1 h para renovar
        leitura("claude-5h", 50.0, self.now + 3600, self.now - 1700)
        self.tokens([("claude-opus-5-5", self.now - 1750, 0, 20_000, 0, 0),        # entre as duas leituras: calibra 10 pts para 300k
                     ("claude-opus-5-5", self.now - 900, 0, 60_000, 0, 0)])          # +30 pts na última hora
        c = self.cota("claude-5h")
        self.assertEqual(c["origem"], "estimado")
        self.assertTrue(c["estoura"])
        al = cotas.alertas(db.cotas(self.now))
        self.assertEqual(len(al), 1)
        self.assertIn("(estimado)", al[0]); self.assertIn("pts/h", al[0])


class TestFaixasDeAlerta(Base2):
    def test_uma_faixa_por_ia_mesmo_com_varios_motivos(self):
        # Codex: estoura E sobra < 40% → hoje eram duas faixas; vira uma
        renova = self.now + 3 * 86400
        leitura("codex", 40.0, renova, self.now - 86400 - 60, janela_h=168)
        leitura("codex", 70.0, renova, self.now - 60, janela_h=168)
        al = cotas.alertas(db.cotas(self.now), codex_procs=2)
        codex = [a for a in al if a.startswith("Codex")]
        self.assertEqual(len(codex), 1)
        self.assertIn("chega a", codex[0]); self.assertIn("sobra 30%", codex[0]); self.assertIn("2 Codex rodando", codex[0])

    def test_claude_com_varias_janelas_em_risco_vira_uma_faixa(self):
        leitura("claude-5h", 40.0, self.now + 3600, self.now - 3600)
        leitura("claude-5h", 90.0, self.now + 3600, self.now - 60)
        leitura("claude-semanal", 40.0, self.now + 86400, self.now - 86400 - 60)
        leitura("claude-semanal", 80.0, self.now + 86400, self.now - 60)
        al = [a for a in cotas.alertas(db.cotas(self.now)) if a.startswith("Claude")]
        self.assertEqual(len(al), 1)
        self.assertIn("janela de 5 h", al[0]); self.assertIn("semanal", al[0])

    def test_visao_nao_repete_o_alerta_do_codex(self):
        db.cota_registrar("codex", 70.0, self.now + 3 * 86400, "teste")
        faixas = [a for a in servidor.visao()["alertas"] if a.startswith("Codex")]
        self.assertLessEqual(len(faixas), 1)


class TestCodexZeraENaoParece(Base2):
    def test_zera_quando_resets_at_passa(self):
        leitura("codex", 80.0, self.now - 3600, self.now - 4 * 86400, janela_h=168)
        c = self.cota("codex")
        self.assertEqual((c["usado_pct"], c["resetou"], c["origem"]), (0.0, True, "zerou na renovação"))
        self.assertAlmostEqual(c["renova_em"], self.now - 3600 + 7 * 86400, delta=1)
        self.assertFalse(c["estoura"])

    def test_parado_sem_consumo_usa_a_hora_real_do_rollout(self):
        db.cota_registrar("codex", 70.0, self.now + 3 * 86400, "codex:sessions", 168.0, quando=self.now - 3 * 3600)
        c = self.cota("codex")
        self.assertTrue(c["parado_sem_consumo"])
        self.assertAlmostEqual(c["idade_leitura_s"], 3 * 3600, delta=5)       # idade = hora do rollout, não a da coleta
        db.cota_registrar("codex", 71.0, self.now + 3 * 86400, "codex:sessions", 168.0, quando=time.time() - 60)
        self.assertFalse(db.cotas()[0]["parado_sem_consumo"])


if __name__ == "__main__":
    unittest.main()
