import json
import os
import time
import unittest
from datetime import datetime, timezone

from orquestra import aovivo, coletores, db, servidor, transcritos
from tests.base import Base


def iso(seg_atras=0):
    return datetime.fromtimestamp(time.time() - seg_atras, timezone.utc).isoformat().replace("+00:00", "Z")


def rollout(sessao, originator="codex_exec", cwd="/a/seller-turbo-t9", prompt="# T9 — Fazer a coisa token=SEGREDO123 e mais coisas " + "x" * 80,
            modelo="gpt-6-sol", pct=(60.0, 64.0), janela=None, seg=0):
    janela = janela or int(time.time()) + 3 * 86400
    L = [json.dumps({"timestamp": iso(seg + 600), "type": "session_meta", "payload": {"id": sessao, "cwd": cwd, "originator": originator}}),
         json.dumps({"type": "turn_context", "payload": {"model": modelo}}),
         json.dumps({"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Você é o Codex"}]}}),
         json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}}),
         json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}}),
         '{"timestamp":"%s","payload":{"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,"resets_at":%d}}}}' % (iso(seg + 300), pct[0], janela),
         json.dumps({"payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 80_000, "cached_input_tokens": 50_000, "cache_write_input_tokens": 0, "output_tokens": 5_000}}}}, separators=(",", ":")),
         '{"timestamp":"%s","payload":{"rate_limits":{"primary":{"used_percent":%s,"window_minutes":10080,"resets_at":%d}}}}' % (iso(seg), pct[1], janela)]
    return "\n".join(L) + "\n"


class TestCodexSessoes(Base):
    def escreve_rollout(self, nome, texto, idade_s=0):
        p = self.escreve("codex/sessions/2026/11/07/rollout-2026-11-07T10-00-00-%s.jsonl" % nome, texto)
        t = time.time() - idade_s
        os.utime(p, (t, t))
        return p

    def test_le_titulo_modelo_tokens_origem_e_fatia_da_cota(self):
        self.escreve_rollout("aaaaaaaa-0000-0000-0000-000000000001", rollout("s1"))
        (c,) = coletores.codex_sessoes()
        self.assertTrue(c["titulo"].startswith("T9 — Fazer a coisa"))
        self.assertNotIn("SEGREDO123", c["titulo"])
        self.assertLessEqual(len(c["titulo"]), 70)
        self.assertEqual((c["modelo"], c["nivel"], c["origem"], c["projeto"]), ("gpt-6-sol", 2, "codex exec", "seller-turbo-t9"))
        self.assertEqual((c["tokens"]["i"], c["tokens"]["cr"], c["tokens"]["o"]), (30_000, 50_000, 5_000))
        self.assertAlmostEqual(c["cota_pts"], 4.0, places=1)       # única sessão da janela: 64 − 60
        self.assertLess(c["ha_segundos"], 60)

    def test_pula_o_agents_md_injetado_e_pega_o_prompt_de_verdade(self):
        texto = rollout("s1", prompt="# T477 — Diagnóstico de produto")
        linhas = texto.split("\n")
        agents = json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /Users/x/proj\n\n<INSTRUCTIONS>…</INSTRUCTIONS>"}]}}, separators=(",", ":"))
        linhas.insert(3, agents)
        self.escreve_rollout("aaaaaaaa-0000-0000-0000-000000000001", "\n".join(linhas))
        self.assertEqual(coletores.codex_sessoes()[0]["titulo"], "T477 — Diagnóstico de produto")

    def test_inclui_as_do_app_e_do_cli_alem_do_codex_exec(self):
        self.escreve_rollout("aaaaaaaa-0000-0000-0000-000000000001", rollout("s1"))
        self.escreve_rollout("bbbbbbbb-0000-0000-0000-000000000002", rollout("s2", originator="Codex Desktop", prompt="Revisar o PR 12", modelo="gpt-6-luna", cwd="/a/app"), idade_s=3600)
        por = {c["id"]: c for c in coletores.codex_sessoes()}
        self.assertEqual(por["00000002"]["origem"], "app/CLI do Codex")
        self.assertEqual((por["00000002"]["titulo"], por["00000002"]["nivel"]), ("Revisar o PR 12", 1))

    def test_ignora_o_antigo_e_nunca_le_auth_json(self):
        self.escreve_rollout("aaaaaaaa-0000-0000-0000-000000000001", rollout("s1"), idade_s=3 * 86400)
        self.escreve("codex/sessions/2026/11/07/auth.json", '{"token":"SEGREDO"}')
        self.assertEqual(coletores.codex_sessoes(), [])

    def test_versao_antiga_da_analise_e_refeita_para_ganhar_titulo(self):
        p = self.escreve_rollout("aaaaaaaa-0000-0000-0000-000000000001", rollout("s1"))
        st = os.stat(p)
        with db.conectar() as con:   # análise antiga (v2), sem título
            con.execute("INSERT INTO analise_transcript(arquivo,fonte,mtime,tam,dados) VALUES (?,?,?,?,?)", (p, "codex", st.st_mtime, st.st_size, json.dumps({"v": 2, "sessao": "x", "dia": "2026-11-07"})))
        self.assertTrue(transcritos.dados_arquivo(p, "codex")["titulo"].startswith("T9"))


class TestCodexNoPainel(Base):
    def setUp(self):
        super().setUp()
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        coletores._ultimo.update(quando=0, dados={})      # o retrato em cache é de outro teste

    def tearDown(self):
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR", None)
        coletores._ultimo.update(quando=0, dados={})
        super().tearDown()

    def rollout(self, nome, idade_s=0, **kw):
        p = self.escreve("codex/sessions/2026/11/07/rollout-2026-11-07T10-00-00-%s.jsonl" % nome, rollout(nome[-4:], seg=idade_s, **kw))
        t = time.time() - idade_s
        os.utime(p, (t, t))

    def test_atividade_recente_inclui_codex_mesmo_sem_processo_vivo(self):
        self.rollout("aaaaaaaa-0000-0000-0000-000000000001", idade_s=4 * 3600)
        ag = servidor.agentes(coletores.coletar_tudo())
        a = next(x for x in ag if x["id"].startswith("codex-sessao:"))
        self.assertEqual((a["tipo"], a["vivo"]), ("codex", False))
        self.assertTrue(a["nome"].startswith("T9 — Fazer a coisa"))
        self.assertIn("seller-turbo-t9 · codex exec", a["detalhe"])
        self.assertGreater(a["ha_segundos"], 3 * 3600)

    def test_sessao_com_atividade_recente_aparece_viva_e_sem_duplicar_o_processo(self):
        self.rollout("aaaaaaaa-0000-0000-0000-000000000001", idade_s=30)
        ret = coletores.coletar_tudo()
        self.assertTrue(next(x for x in servidor.agentes(ret) if x["id"].startswith("codex-sessao:"))["vivo"])
        ret["codex_processos"] = [{"pid": 7, "pasta": "seller-turbo-t9", "modelo": "gpt-6-sol", "nivel": 2, "tarefa": "T9", "titulo": "Brief do T9", "ha_segundos": 90, "idade_log": 5}]
        ag = servidor.agentes(ret)
        self.assertEqual([x["id"] for x in ag if x["tipo"] == "codex"], ["codex:7"])   # mesma execução: só o processo (com o título do brief)

    def test_ao_vivo_lista_rollout_sem_processo_como_quieta_e_nunca_alerta_travada(self):
        self.rollout("aaaaaaaa-0000-0000-0000-000000000001", idade_s=40 * 60)
        am = aovivo.Amostrador()
        am.estados["codex-sessao:00000001"] = "ativa"
        r = am.amostrar("", sessoes=[], buscar_energia=False)
        it = next(i for i in r["itens"] if i["id"] == "codex-sessao:00000001")
        self.assertEqual(it["estado"], "quieta")            # sem PID não dá para provar travamento
        self.assertIn("sem processo ativo", it["motivo"])
        self.assertEqual([m for m in db.msgs("orquestrador") if "TRAVADA" in m["texto"]], [])

    def test_cartao_codex_aparece_parado_com_ultima_execucao_e_cota(self):
        self.rollout("aaaaaaaa-0000-0000-0000-000000000001", idade_s=5 * 3600)
        coletores._ultima_exec.update(t=0, v=None)
        x = servidor.visao()["codex"]      # a cota vem do último rate_limits do rollout (64%), não de digitação
        self.assertEqual((x["estado"], x["rodando"], x["processos"]), ("parado", 0, 0))
        self.assertAlmostEqual(x["ultima_execucao_s"], 5 * 3600, delta=120)
        self.assertEqual(x["cota"]["usado_pct"], 64.0)
        self.assertIn("previsao_fim_pct", x["cota"])
        self.assertEqual(x["sessoes_24h"], 1)
        self.assertGreater(x["tokens_24h"]["i"], 0)

    def test_cartao_codex_sem_nenhuma_execucao_nao_quebra(self):
        coletores._ultima_exec.update(t=0, v=None)
        x = servidor.visao()["codex"]
        self.assertEqual((x["estado"], x["ultima_execucao_s"], x["cota"]), ("parado", None, None))

    def test_alerta_de_codex_com_cota_baixa_conta_so_processos_nao_sessoes_de_rollout(self):
        for i in range(5):
            self.rollout("aaaaaaaa-0000-0000-0000-00000000000%d" % i, idade_s=4 * 3600 + i, pct=(60.0, 65.0))
        db.cota_registrar("codex", 70.0, time.time() + 3 * 86400, "codex:sessions")
        alertas = servidor.visao()["alertas"]
        self.assertEqual([a for a in alertas if "rodando com cota baixa" in a], [])   # 5 sessões antigas ≠ 5 Codex rodando

    def test_cartao_marca_rodando_quando_ha_sessao_ativa(self):
        self.rollout("aaaaaaaa-0000-0000-0000-000000000001", idade_s=20)
        coletores._ultima_exec.update(t=0, v=None)
        self.assertEqual(servidor.visao()["codex"]["estado"], "rodando")


if __name__ == "__main__":
    unittest.main()
