import io
import json
import os
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from orquestra import cli, custo, custos, db, sellerturbo, transcritos
from tests.base import Base


def agora_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def msg(mid, modelo, i, o, cr=0, cw=0, ts=None, cwd="/p/meu-projeto"):
    return json.dumps({"cwd": cwd, "timestamp": ts or agora_iso(), "message": {"id": mid, "role": "assistant", "model": modelo, "content": [],
                       "usage": {"input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}) + "\n"


def orq(*a):
    out = io.StringIO()
    return cli.main(list(a), out), out.getvalue()


class BaseCustos(Base):
    def setUp(self):
        super().setUp()
        self.precos = self.escreve("precos.json", json.dumps({"vigencia": "2026-11-01", "cambio_brl": 5.0, "modelo_orquestrador": "claude-opus-5-5", "modelos": {
            "claude-sonnet-5-5": {"entrada": 3, "saida": 15, "cache_leitura": 0.3, "cache_escrita": 3.75},
            "claude-opus-5-5": {"entrada": 5, "saida": 25, "cache_leitura": 0.5, "cache_escrita": 6.25},
            "gpt-6-sol": {"entrada": 2, "saida": 10, "cache_leitura": 0.2, "cache_escrita": 2}}}))
        os.environ["ORQUESTRA_PRECOS"] = self.precos
        os.environ["ORQUESTRA_HOME"] = os.path.join(self.tmp, "casa")
        self.escreve("casa/config/planos.json", json.dumps({"vigencia": "2026-11-01", "claude": {"limite_mensal_brl": None}, "codex": {"valor_semanal_brl": 100.0}}))
        self.escreve("casa/config/energia.json", json.dumps({"watts_por_nucleo": 10.0, "watts_runner": 60.0, "tarifa_kwh_brl": 1.0}))

    def tearDown(self):
        os.environ.pop("ORQUESTRA_PRECOS", None); os.environ.pop("ORQUESTRA_HOME", None)
        super().tearDown()


class TestCustosClaude(BaseCustos):
    def test_cache_leitura_e_escrita_tem_preco_proprio(self):
        # (1000×3 + 500×15 + 1e6×0.3 + 1e5×3.75) / 1e6 × 5
        self.assertAlmostEqual(custo.custo_brl("claude-sonnet-5-5", 1000, 500, 1_000_000, cache_escrita=100_000), 3.4275, places=1)
        self.assertEqual(custo.custo_brl("claude-sonnet-5-5", 0, 0, 1_000_000), 1.5)       # só leitura de cache
        self.assertEqual(custo.custo_brl("claude-sonnet-5-5", 0, 0, 0, cache_escrita=1_000_000), 18.75)

    def test_tokens_por_modelo_com_cache_dedup_e_subagente(self):
        self.escreve("claude/projects/-p/sess1.jsonl", msg("a", "claude-sonnet-5-5", 1000, 100, 1_000_000, 100_000) + msg("a", "claude-sonnet-5-5", 1000, 500, 1_000_000, 100_000) + msg("b", "claude-opus-5-5", 10, 20))
        self.escreve("claude/projects/-p/sess1/subagents/agent-x.jsonl", msg("c", "claude-sonnet-5-5", 100, 50))
        transcritos.atualizar()
        rel = custos.relatorio("dia", "modelo")
        g = {x["chave"]: x for x in rel["agrupado"]}
        self.assertEqual((g["claude-sonnet-5-5"]["tin"], g["claude-sonnet-5-5"]["tout"], g["claude-sonnet-5-5"]["cache_leitura"], g["claude-sonnet-5-5"]["cache_escrita"]), (1100, 550, 1_000_000, 100_000))
        self.assertAlmostEqual(g["claude-sonnet-5-5"]["brl"], 3.44, places=1)
        self.assertTrue(rel["estimativa"])
        self.assertEqual(rel["precos_vigencia"], "2026-11-01")

    def test_modelo_sem_preco_nao_inventa_valor(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "modelo-desconhecido", 1000, 1000))
        transcritos.atualizar()
        rel = custos.relatorio("dia")
        self.assertEqual(rel["sem_preco"], ["modelo-desconhecido"])
        self.assertEqual(rel["total_brl"], 0)

    def test_atribui_projeto_e_tarefa_do_quadro_pelo_pulso(self):
        self.escreve("claude/projects/-p/abcdef1234567.jsonl", msg("a", "claude-sonnet-5-5", 1000, 1000, cwd="/x/seller-turbo"))
        t = db.tarefa_nova("Consertar X", nivel=2); db.tarefa_pegar(t, "claude:abcdef12")
        transcritos.atualizar()
        self.assertEqual(custos.relatorio("dia", "projeto")["agrupado"][0]["chave"], "seller-turbo")
        top = custos.relatorio("dia", "tarefa")["top_tarefas"][0]
        self.assertIn("#%d Consertar X" % t, top["chave"])

    def test_economia_vs_modelo_do_orquestrador(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "claude-sonnet-5-5", 1_000_000, 0))
        transcritos.atualizar()
        # medio: 3×5=15; caro: 5×5=25 → economia 10
        self.assertEqual(custos.relatorio("dia")["economia_vs_orquestrador"], 10.0)

    def test_orquestrador_a_parte_por_dia_e_por_pr(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "claude-opus-5-5", 1_000_000, 0))  # 25 R$
        transcritos.atualizar()
        hoje = datetime.now().strftime("%Y-%m-%d")
        with mock.patch("orquestra.custos.sellerturbo.prs_mesclados", return_value={hoje: 2}):
            rel = custos.relatorio("dia")
        self.assertEqual(rel["orquestrador"][0]["brl"], 25.0)
        self.assertEqual(rel["orquestrador"][0]["brl_por_pr"], 12.5)
        self.assertEqual(rel["custo_por_pr"], 12.5)

    def test_totais_periodos_e_ranking(self):
        for i in range(12):
            self.escreve("claude/projects/-p/s%02d.jsonl" % i, msg("m", "claude-sonnet-5-5", 1_000_000 * (i + 1), 0))
            t = db.tarefa_nova("T%d" % i); db.tarefa_pegar(t, "claude:s%02d" % i)
        transcritos.atualizar()
        rel = custos.relatorio("semana", "tarefa")
        self.assertEqual(len(rel["top_tarefas"]), 10)
        self.assertGreaterEqual(rel["top_tarefas"][0]["brl"], rel["top_tarefas"][9]["brl"])
        self.assertEqual(len(rel["por_dia"]), 7)
        self.assertEqual(len(custos.relatorio("mes")["por_dia"]), 30)

    def test_alerta_vem_das_janelas_informadas_nao_do_r_do_claude(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "claude-sonnet-5-5", 2_000_000, 0))   # R$ 30 hoje: não gera alerta
        transcritos.atualizar()
        rel = custos.relatorio("dia")
        self.assertEqual([a for a in rel["alertas"] if "R$" in a], [])
        self.assertNotIn("projecao_mes", rel["previsao"]["claude"])
        self.assertTrue(rel["previsao"]["claude"]["equivalente_api"])

    def test_exporta_csv_markdown_e_cli(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "claude-sonnet-5-5", 1000, 1000, 5000, 100))
        csv_ = orq("custos", "--dia", "--por", "modelo", "--csv")[1]
        self.assertIn("cache_leitura", csv_); self.assertIn("claude-sonnet-5-5", csv_)
        md = orq("custos", "--dia", "--md")[1]
        self.assertIn("estimativa", md.lower()); self.assertIn("10 tarefas mais caras", md)
        j = json.loads(orq("custos", "--semana", "--por", "projeto", "--json")[1])
        self.assertEqual(j["agrupado"][0]["chave"], "meu-projeto")

    def test_nao_guarda_texto_de_mensagem(self):
        linha = json.loads(msg("a", "claude-sonnet-5-5", 1, 1)); linha["message"]["content"] = [{"type": "text", "text": "SEGREDO-NO-TEXTO token=abc"}]
        self.escreve("claude/projects/-p/s.jsonl", json.dumps(linha) + "\n")
        transcritos.atualizar()
        with db.conectar() as con:
            guardado = " ".join(r[0] for r in con.execute("SELECT dados FROM analise_transcript"))
        self.assertNotIn("SEGREDO-NO-TEXTO", guardado)


def rollout(sid, pct_ini, pct_fim, resets, i, cr, o, ts=None):
    ts = ts or agora_iso()
    L = [json.dumps({"timestamp": ts, "type": "session_meta", "payload": {"cwd": "/a/lote"}}),
         json.dumps({"type": "turn_context", "payload": {"model": "gpt-6-sol"}}),
         '{"payload":{"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,"resets_at":%d}}}}' % (pct_ini, resets),
         json.dumps({"payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": i, "cached_input_tokens": cr, "cache_write_input_tokens": 0, "output_tokens": o}}}}, separators=(",", ":")),
         '{"payload":{"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,"resets_at":%d}}}}' % (pct_fim, resets)]
    return "\n".join(L) + "\n"


class TestCustosCodexMaquinaCI(BaseCustos):
    def test_cota_do_codex_e_repartida_entre_sessoes_da_mesma_janela(self):
        hoje = int(time.time()) + 86400
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", rollout("a", 10.0, 14.0, hoje, 100_000, 50_000, 10_000))
        self.escreve("codex/sessions/2026/11/07/rollout-b.jsonl", rollout("b", 12.0, 20.0, hoje, 100_000, 50_000, 10_000))
        transcritos.atualizar()
        rel = custos.relatorio("dia", "agente")
        # consumo da janela: 20 − 10 = 10 pts, repartido por peso (iguais) → 5 pts cada; plano de R$ 100/semana → R$ 5 cada
        self.assertAlmostEqual(rel["codex"]["cota_pct"], 10.0, places=1)
        self.assertAlmostEqual(rel["partes"]["codex"], 10.0, places=1)
        self.assertEqual(rel["tokens"]["cache_leitura"], 100_000)
        self.assertEqual(rel["tokens"]["tin"], 2 * (100_000 - 50_000))  # entrada não cacheada = entrada − cache lido

    def test_maquina_cpu_tempo_vira_kwh_e_reais(self):
        hoje = datetime.now().strftime("%Y-%m-%d")
        with db.conectar() as con:
            con.execute("INSERT INTO custos_maquina(dia,chave,tarefa_id,cpu_s) VALUES (?,?,?,?)", (hoje, "codex:1", None, 3600.0))
        rel = custos.relatorio("dia")
        # 3600 s × 10 W = 0,01 kWh × R$ 1,00
        self.assertAlmostEqual(rel["maquina"]["kwh"], 0.01, places=4)
        self.assertAlmostEqual(rel["partes"]["maquina"], 0.01, places=3)

    def test_ci_minutos_viram_kwh_e_reais(self):
        hoje = datetime.now().strftime("%Y-%m-%d")
        with mock.patch("orquestra.custos.sellerturbo.ci_minutos", return_value={hoje: 120.0}):
            rel = custos.relatorio("dia")
        self.assertAlmostEqual(rel["partes"]["ci"], 0.12, places=3)  # 2 h × 60 W = 0,12 kWh × R$ 1

    def test_acumulo_de_cpu_do_ao_vivo_grava_no_banco(self):
        from orquestra import aovivo
        am = aovivo.Amostrador()
        t0 = time.time()
        it = [{"id": "codex:9", "vivo": True, "cpu": 50.0, "tarefa_id": None}]
        am._acumular_cpu(it, t0); am._acumular_cpu(it, t0 + 10); am.t_flush = 0; am._acumular_cpu(it, t0 + 20)
        with db.conectar() as con:
            cpu = con.execute("SELECT cpu_s FROM custos_maquina WHERE chave='codex:9'").fetchone()[0]
        self.assertAlmostEqual(cpu, 10.0, places=1)  # 50% × 20 s


class TestCiEPrs(Base):
    def test_ci_e_prs_mesclados_por_dia(self):
        os.environ.pop("ORQUESTRA_SEM_GH")
        sellerturbo._ci.update(quando=0, dados=None); sellerturbo._mesclados.update(quando=0, dados=None)
        runs = [{"databaseId": 1, "startedAt": "2026-11-07T10:00:00Z", "updatedAt": "2026-11-07T10:30:00Z", "workflowName": "w"}]
        prs = [{"number": 1, "mergedAt": "2026-11-07T12:00:00Z"}, {"number": 2, "mergedAt": "2026-11-07T13:00:00Z"}]
        with mock.patch("orquestra.sellerturbo._rodar", side_effect=[runs, prs]):
            ci, m = sellerturbo.ci_minutos(), sellerturbo.prs_mesclados()
        self.assertAlmostEqual(list(ci.values())[0], 30.0)
        self.assertEqual(list(m.values())[0], 2)
        os.environ["ORQUESTRA_SEM_GH"] = "1"
        sellerturbo._ci.update(quando=0, dados=None); sellerturbo._mesclados.update(quando=0, dados=None)  # não vaza para outros testes


if __name__ == "__main__":
    unittest.main()


def rollout_serie(janela, pontos, i=100_000, cr=50_000, o=10_000):
    """pontos: [(segundos atrás, % usado)] — uma sessão com a série de leituras do ciclo."""
    agora = time.time()
    L = [json.dumps({"timestamp": datetime.fromtimestamp(agora - pontos[0][0], timezone.utc).isoformat().replace("+00:00", "Z"), "type": "session_meta", "payload": {"cwd": "/a/lote"}}),
         json.dumps({"payload": {"model": "gpt-6-sol"}})]
    for seg, pct in pontos:
        ts = datetime.fromtimestamp(agora - seg, timezone.utc).isoformat().replace("+00:00", "Z")
        L.append('{"timestamp":"%s","payload":{"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,"resets_at":%d}}}}' % (ts, pct, janela))
    L.append(json.dumps({"payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": i, "cached_input_tokens": cr, "cache_write_input_tokens": 0, "output_tokens": o}}}}, separators=(",", ":")))
    return "\n".join(L) + "\n"


class TestRevisaoDaTelaDeCustos(BaseCustos):
    def cota(self, usado, dias=3.4):
        renova = int(time.time() + dias * 86400)
        db.cota_registrar("codex", usado, renova)
        return renova

    def test_ritmo_das_ultimas_24h_nao_alerta_quando_o_uso_ja_caiu(self):
        renova = self.cota(70.0)
        # uso alto no começo do ciclo (10% → 67% em 3 dias) e só ~3 pts nas últimas 24 h
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", rollout_serie(renova, [(5 * 86400, 10.0), (3 * 86400, 40.0), (26 * 3600, 67.0), (3600, 70.0)]))
        transcritos.atualizar()
        r24, rciclo, _ = custos.ritmo_codex()
        self.assertAlmostEqual(r24, 3.0 * 86400 / (25 * 3600), delta=0.3)   # ≈ 2,9 pts/dia
        self.assertGreater(rciclo, 10)                                     # a média do ciclo é alta e engana
        rel = custos.relatorio("semana")
        x = rel["previsao"]["codex"]
        self.assertFalse(x["estoura"])
        self.assertLess(x["projecao_fim_ciclo_pct"], 100)
        self.assertEqual([a for a in rel["alertas"] if "Codex" in a and "ritmo das últimas 24 h" in a], [])
        self.assertIsNotNone(x["ritmo_ciclo_pts_dia"])  # a média do ciclo aparece, mas só informa

    def test_alerta_quando_o_ritmo_de_24h_estoura(self):
        renova = self.cota(70.0)
        self.escreve("codex/sessions/2026/11/07/rollout-a.jsonl", rollout_serie(renova, [(30 * 3600, 40.0), (3600, 70.0)]))
        transcritos.atualizar()
        rel = custos.relatorio("semana")
        self.assertTrue(rel["previsao"]["codex"]["estoura"])
        self.assertTrue([a for a in rel["alertas"] if "ritmo das últimas 24 h" in a])

    def test_cota_do_card_e_so_do_ciclo_atual_e_nunca_passa_de_100(self):
        atual = self.cota(60.0)
        anterior = atual - 7 * 86400
        self.escreve("codex/sessions/2026/11/05/rollout-velho.jsonl", rollout_serie(anterior, [(6 * 86400, 5.0), (5 * 86400, 65.0)]))
        self.escreve("codex/sessions/2026/11/07/rollout-novo.jsonl", rollout_serie(atual, [(2 * 86400, 10.0), (3600, 60.0)]))
        transcritos.atualizar()
        c = custos.relatorio("semana")["codex"]
        self.assertLessEqual(c["cota_pct"], 100)
        self.assertAlmostEqual(c["cota_pct"], 50.0, delta=1)            # só o ciclo atual
        self.assertAlmostEqual(c["pontos_periodo"], 110.0, delta=1)     # 60 + 50 pts de ciclos diferentes, sem %

    def test_claude_e_rotulado_equivalente_api_e_nunca_projeta_em_reais(self):
        self.escreve("claude/projects/-p/s.jsonl", msg("a", "claude-sonnet-5-5", 2_000_000, 0))
        transcritos.atualizar()
        rel = custos.relatorio("dia")
        self.assertTrue(rel["claude_equivalente_api"])
        self.assertIn("equivalente a preço de API", rel["rotulo_claude"])
        pc = rel["previsao"]["claude"]
        self.assertEqual(set(pc), {"gasto_mes_equivalente_api", "equivalente_api"})   # sem limite, sem projeção em R$
        md = custos.markdown(rel)
        self.assertIn("equivalente a preço de API", md)
        self.assertNotIn("limite_mensal_brl", md)

    def test_planos_json_nao_pede_limite_em_reais_do_claude(self):
        raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(raiz, "config", "planos.json"), encoding="utf-8") as f:
            self.assertNotIn("limite_mensal_brl", f.read())
