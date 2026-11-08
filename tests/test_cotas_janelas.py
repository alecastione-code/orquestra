import io
import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import cli, cotas, db, servidor
from tests.base import Base


def orq(*a, entrada=None):
    out = io.StringIO()
    if entrada is not None:
        import sys
        antigo, sys.stdin = sys.stdin, io.StringIO(entrada)
    try:
        return cli.main(list(a), out), out.getvalue()
    finally:
        if entrada is not None:
            sys.stdin = antigo


def leitura(agente, usado, renova, ha_s, janela_h=None):
    """Insere uma leitura antiga (histórico) do mesmo ciclo."""
    t = time.time() - ha_s
    with db.conectar() as con:
        con.execute("INSERT INTO cotas(agente,usado_pct,renova_em,fonte,lido_em,janela_h,confirmado_em) VALUES (?,?,?,?,?,?,?)",
                    (agente, usado, renova, "teste", t, janela_h or db.janela_do_nome(agente), t))


class TestJanelaDe5Horas(Base):
    def test_ritmo_e_previsao_por_hora_nao_por_dia(self):
        renova = time.time() + 4 * 3600
        leitura("claude-5h", 6.0, renova, 3600)
        db.cota_registrar("claude-5h", 12.0, renova, "informado")
        c = db.cotas()[0]
        self.assertEqual((c["janela_h"], c["ritmo_unidade"]), (5.0, "pts/h"))
        self.assertAlmostEqual(c["ritmo_atual"], 6.0, delta=0.4)          # 6 pts em 1 h
        self.assertAlmostEqual(c["ritmo_permitido"], 22.0, delta=0.5)     # 88% disponível ÷ 4 h
        self.assertAlmostEqual(c["previsao_fim_pct"], 36, delta=3)        # 12 + 6 × 4 h
        self.assertEqual((c["disponivel_pct"], c["estoura"], c["acima_ritmo"]), (88.0, False, False))
        self.assertIn("pts/h", c["texto_ritmo"])
        self.assertNotIn("/dia", c["texto_ritmo"])

    def test_alerta_so_pela_previsao_contra_a_janela(self):
        renova = time.time() + 4 * 3600
        leitura("claude-5h", 50.0, renova, 3600)
        db.cota_registrar("claude-5h", 80.0, renova, "informado")         # 30 pts/h → estoura
        c = db.cotas()[0]
        self.assertTrue(c["estoura"])
        txt = " ".join(cotas.alertas())
        self.assertIn("última hora", txt); self.assertIn("pts/h", txt); self.assertNotIn("/dia", txt)

    def test_uso_alto_sem_consumo_nao_alerta(self):
        renova = time.time() + 1 * 3600
        leitura("claude-5h", 90.0, renova, 3600)
        db.cota_registrar("claude-5h", 90.0, renova, "informado")         # 90% mas parado: não estoura
        self.assertEqual([a for a in cotas.alertas() if "chega a" in a], [])


class TestSemanalEDisponivel(Base):
    def test_usado_disponivel_renova_ritmo_e_previsao(self):
        renova = time.time() + 5 * 86400
        leitura("claude-semanal", 5.0, renova, 86400 + 60)
        db.cota_registrar("claude-semanal", 7.0, renova, "informado")
        c = db.cotas()[0]
        self.assertEqual((c["usado_pct"], c["disponivel_pct"], c["janela_h"], c["ritmo_unidade"]), (7.0, 93.0, 168.0, "pts/dia"))
        self.assertAlmostEqual(c["horas_ate_renovar"], 120, delta=0.1)
        self.assertAlmostEqual(c["ritmo_atual"], 2.0, delta=0.2)
        self.assertAlmostEqual(c["ritmo_permitido"], 18.6, delta=0.2)
        self.assertFalse(c["estoura"])

    def test_alerta_semanal_estoura_pelo_ritmo_de_24h(self):
        renova = time.time() + 3 * 86400
        leitura("claude-semanal", 40.0, renova, 86400 + 60)
        db.cota_registrar("claude-semanal", 70.0, renova, "informado")
        self.assertTrue(any("últimas 24 h" in a for a in cotas.alertas()))


class TestLeituraVelhaEEtiqueta(Base):
    """Leitura velha NÃO é alerta: vira etiqueta cinza. Limites: janela de 5 h = 60 min; semanal = 6 h."""

    def test_limites_por_janela_e_nunca_alerta(self):
        renova = time.time() + 86400
        leitura("claude-5h", 10.0, renova, 50 * 60)
        leitura("claude-semanal", 7.0, renova + 5 * 86400, 5 * 3600)
        c = {x["agente"]: x for x in db.cotas()}
        self.assertFalse(c["claude-5h"]["leitura_velha"]); self.assertFalse(c["claude-semanal"]["leitura_velha"])
        leitura("claude-5h", 10.0, renova, 70 * 60)       # nova linha de histórico mais velha NÃO muda a última; atualiza a confirmação:
        with db.conectar() as con:
            con.execute("UPDATE cotas SET confirmado_em=lido_em, lido_em=? WHERE agente='claude-5h'", (time.time() - 70 * 60,))
            con.execute("UPDATE cotas SET confirmado_em=? WHERE agente='claude-semanal'", (time.time() - 7 * 3600,))
        c = {x["agente"]: x for x in db.cotas()}
        self.assertTrue(c["claude-5h"]["leitura_velha"]); self.assertTrue(c["claude-semanal"]["leitura_velha"])
        self.assertTrue(c["claude-5h"]["desatualizada"])   # só confirmada, sem estimativa
        self.assertEqual([a for a in cotas.alertas() if "desatualizada" in a or "peça à IA" in a], [])      # nada de faixa vermelha
        self.assertEqual([a for a in servidor.visao()["alertas"] if "desatualizada" in a or "peça à IA" in a], [])
        self.assertIn("leitura velha", orq("cotas")[1])

    def test_informar_o_mesmo_valor_confirma_sem_gravar_linha_nova(self):
        renova = time.time() + 86400
        leitura("claude-5h", 10.0, renova, 90 * 60)
        self.assertTrue(db.cotas()[0]["leitura_velha"])
        db.cota_registrar("claude-5h", 10.0, renova, "informado")
        c = db.cotas()[0]
        self.assertFalse(c["leitura_velha"])
        with db.conectar() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cotas").fetchone()[0], 1)

    def test_leitura_recente_nao_tem_etiqueta(self):
        db.cota_registrar("claude-5h", 10.0, time.time() + 3600, "informado")
        self.assertFalse(db.cotas()[0]["leitura_velha"])


JSON_USO = {"plan": {"windows": [
    {"label": "5-hour limit", "percentUsed": 12, "resetsAt": "2030-11-08T05:50:00Z"},
    {"label": "Weekly · all models", "percentUsed": 7, "resetsAt": "2030-11-14T23:00:00Z"},
    {"label": "Weekly · Fable 5.1", "percentUsed": 9, "resetsAt": "2030-11-14T23:00:00Z"},
]}}


class TestInformarEmLote(Base):
    def test_mapeia_as_labels_para_os_agentes(self):
        self.assertEqual(cotas.agente_da_label("5-hour limit"), ("claude-5h", 5.0))
        self.assertEqual(cotas.agente_da_label("Weekly · all models"), ("claude-semanal", 168.0))
        self.assertEqual(cotas.agente_da_label("Weekly · Fable 5.1"), ("claude-semanal-fable", 168.0))
        self.assertEqual(cotas.agente_da_label("Weekly · Opus"), ("claude-semanal-opus", 168.0))
        with self.assertRaises(ValueError):
            cotas.agente_da_label("Monthly credits")

    def test_cotas_informar_arquivo(self):
        arq = self.escreve("uso.json", json.dumps(JSON_USO))
        code, saida = orq("cotas-informar", arq)
        self.assertEqual(code, 0)
        por = {c["agente"]: c for c in db.cotas()}
        self.assertEqual(set(por), {"claude-5h", "claude-semanal", "claude-semanal-fable"})
        self.assertEqual((por["claude-5h"]["usado_pct"], por["claude-5h"]["janela_h"]), (12.0, 5.0))
        self.assertEqual(por["claude-semanal-fable"]["usado_pct"], 9.0)
        self.assertEqual(por["claude-semanal"]["renova_em"], datetime(2030, 11, 14, 23, tzinfo=timezone.utc).timestamp())
        self.assertEqual(por["claude-semanal"]["fonte"], "claude:informado")

    def test_stdin_e_cota_json_e_formatos_alternativos(self):
        self.assertEqual(orq("cotas-informar", "-", entrada=json.dumps(JSON_USO))[0], 0)
        arq = self.escreve("b.json", json.dumps({"windows": [{"label": "5-hour limit", "percentUsed": 20, "resetsAt": "2030-11-08T05:50:00Z"}]}))
        self.assertEqual(orq("cota", "--json", arq)[0], 0)
        self.assertEqual({c["agente"]: c for c in db.cotas()}["claude-5h"]["usado_pct"], 20.0)

    def test_erros_nao_gravam_nada_pela_metade(self):
        ruim = self.escreve("ruim.json", json.dumps({"plan": {"windows": [{"label": "5-hour limit", "percentUsed": 5, "resetsAt": "2030-11-08T05:50:00Z"},
                                                                             {"label": "???", "percentUsed": 1}]}}))
        self.assertEqual(orq("cotas-informar", ruim)[0], 1)
        self.assertEqual(db.cotas(), [])   # a validação roda antes de gravar qualquer janela
        self.assertEqual(orq("cotas-informar", self.escreve("v.json", "[]"))[0], 1)

    def test_orq_cota_manual_continua_funcionando_com_janela(self):
        orq("cota", "claude-5h", "--usado", "12", "--renova", "2030-11-08T05:50:00Z")
        self.assertEqual(db.cotas()[0]["janela_h"], 5.0)
        orq("cota", "outra", "--usado", "10", "--janela-h", "24")
        self.assertEqual({c["agente"]: c for c in db.cotas()}["outra"]["janela_h"], 24.0)


if __name__ == "__main__":
    unittest.main()
