import json
import os
import time
import unittest

from orquestra import aovivo, db
from tests.base import Base

PS = "  500     1  0.0 1000  00:20:00 S    /Users/x/.local/bin/codex exec -m gpt-6-sol --sandbox s -C /a/b/t9 # T9 — Brief do nove\\012resto\n" \
     "  501   500 %s 2048  00:19:00 S    node worker\n" \
     "  502   501  0.0  512  00:19:00 S    zsh\n"


class TestClassificacao(unittest.TestCase):
    def test_ativa_por_cpu(self):
        self.assertEqual(aovivo.classificar(True, 35.0, 3000)[0], "ativa")

    def test_ativa_por_log_recente_mesmo_sem_cpu(self):
        self.assertEqual(aovivo.classificar(True, 0.0, 60)[0], "ativa")

    def test_travada_cpu_zero_e_log_parado_mais_de_10_min(self):
        e, m = aovivo.classificar(True, 0.0, 11 * 60)
        self.assertEqual(e, "travada")
        self.assertIn("11 min", m)

    def test_quieta_dentro_dos_10_min(self):
        self.assertEqual(aovivo.classificar(True, 0.0, 5 * 60)[0], "quieta")

    def test_pausada_por_stat_t_ou_pelo_agente(self):
        self.assertEqual(aovivo.classificar(True, 0, 9999, "TN")[0], "pausada")
        e, m = aovivo.classificar(True, 50, 1, "S", "esperando decisão do dono")
        self.assertEqual(e, "pausada")
        self.assertIn("esperando decisão", m)

    def test_esperando_o_dono(self):
        self.assertEqual(aovivo.classificar(True, None, 300, aguardando_dono=True)[0], "esperando_dono")

    def test_terminou_e_falhou(self):
        self.assertEqual(aovivo.classificar(False, codigo=0)[0], "terminou")
        e, m = aovivo.classificar(False, codigo=2)
        self.assertEqual(e, "falhou")
        self.assertIn("2", m)

    def test_nao_usa_privilegio_de_administrador(self):
        with open(aovivo.__file__, encoding="utf-8") as f:
            codigo = f.read()
        self.assertNotIn('"sudo"', codigo)
        self.assertNotIn("powermetrics", codigo.replace("Sem privilégio", ""))


class TestArvoreEEtapa(Base):
    def test_arvore_soma_cpu_e_filhos(self):
        rows = aovivo.parse_ps(PS % "40.5")
        arv = aovivo.arvore(rows, 500)
        self.assertEqual({r["pid"] for r in arv}, {500, 501, 502})
        self.assertAlmostEqual(sum(r["cpu"] for r in arv), 40.5)

    def test_etapa_codex_ultimo_exec_e_oculta_segredo(self):
        log = self.escreve("logs/T9.log", "OpenAI Codex\nexec\n/bin/zsh -lc 'curl -H \"Authorization: Bearer abc123\" x'\n succeeded in 3ms:\nmensagem final\n")
        e = aovivo.etapa_codex(log)
        self.assertTrue(e.startswith("exec:"))
        self.assertNotIn("abc123", e)

    def test_etapa_claude_tool_use_pendente(self):
        linhas = [json.dumps({"message": {"role": "assistant", "stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"description": "rodar testes"}}]}})]
        arq = self.escreve("claude/projects/p/s.jsonl", "\n".join(linhas) + "\n")
        r = aovivo.etapa_claude(arq)
        self.assertEqual(r["etapa"], "Bash: rodar testes")
        self.assertTrue(r["pendente"])
        self.assertFalse(r["aguardando_dono"])

    def test_etapa_claude_aguardando_dono(self):
        arq = self.escreve("claude/projects/p/s.jsonl", json.dumps({"message": {"role": "assistant", "stop_reason": "end_turn", "content": [
            {"type": "text", "text": "Pronto. Quer que eu publique?"}]}}) + "\n")
        r = aovivo.etapa_claude(arq)
        self.assertTrue(r["aguardando_dono"])
        self.assertIn("publique", r["etapa"])


class TestAmostrador(Base):
    def setUp(self):
        super().setUp()
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"

    def tearDown(self):
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR", None)
        super().tearDown()

    def test_ativa_depois_travada_gera_alerta_uma_vez(self):
        log = self.escreve("logs/T9.log", "exec\nls\n")
        am = aovivo.Amostrador()
        r = am.amostrar(PS % "30.0", sessoes=[], buscar_energia=False)
        it = r["itens"][0]
        self.assertEqual((it["estado"], it["cpu"], it["processos"]), ("ativa", 30.0, 3))
        self.assertEqual(it["energia"]["estimativa"], True)
        antigo = time.time() - 15 * 60
        os.utime(log, (antigo, antigo))
        r = am.amostrar(PS % "0.0", sessoes=[], buscar_energia=False)
        self.assertEqual(r["itens"][0]["estado"], "travada")
        am.amostrar(PS % "0.0", sessoes=[], buscar_energia=False)
        alertas = [m for m in db.msgs("orquestrador") if "TRAVADA" in m["texto"]]
        self.assertEqual(len(alertas), 1)
        self.assertTrue([e for e in db.eventos() if e["tipo"] == "alerta"])
        self.assertEqual(am.ultima["resumo"]["travada"], 1)

    def test_processo_que_sai_com_codigo_vira_falhou(self):
        log = self.escreve("logs/T9.log", "exec\nls\nT9 exit 2\n")
        am = aovivo.Amostrador()
        am.amostrar(PS % "30.0", sessoes=[], buscar_energia=False)
        r = am.amostrar("", sessoes=[], buscar_energia=False)
        self.assertEqual(r["itens"][0]["estado"], "falhou")
        self.assertIn("2", r["itens"][0]["motivo"])
        self.assertTrue([m for m in db.msgs("orquestrador") if "FALHOU" in m["texto"]])

    def test_eta_pela_media_das_feitas_do_mesmo_nivel(self):
        for _ in range(2):
            t = db.tarefa_nova("x", nivel=2)
            db.tarefa_pegar(t, "a")
            with db.conectar() as con:
                con.execute("UPDATE tarefas SET iniciado=?, concluido=?, estado='feita' WHERE id=?", (time.time() - 1000, time.time() - 400, t))
        self.assertAlmostEqual(db.duracao_media(2), 600, delta=2)
        self.assertIsNone(db.duracao_media(1))
        t = db.tarefa_nova("em curso", nivel=2)
        db.tarefa_pegar(t, "T9")
        self.escreve("logs/T9.log", "exec\nls\n")
        r = aovivo.Amostrador().amostrar(PS % "10.0", sessoes=[], buscar_energia=False)
        self.assertTrue(r["itens"][0]["eta_texto"].startswith("em ~") or r["itens"][0]["eta_texto"] == "passou da média")
        self.assertEqual(r["itens"][0]["tarefa_id"], t)

    def test_tarefa_que_sumiu_do_banco_nao_derruba_a_coleta(self):
        am = aovivo.Amostrador()
        self.escreve("logs/T9.log", "exec\nls\n\nT9 exit 0\n")
        t = db.tarefa_nova("x"); db.tarefa_pegar(t, "T9")
        am.amostrar(PS % "30.0", sessoes=[], buscar_energia=False)
        with db.conectar() as con:
            con.execute("DELETE FROM tarefas")
        r = am.amostrar("", sessoes=[], buscar_energia=False)       # o processo sumiu e a tarefa também
        self.assertEqual(r["itens"][0]["estado"], "terminou")

    def test_tarefa_pausada_pelo_agente(self):
        t = db.tarefa_nova("x")
        db.tarefa_pegar(t, "T9")
        db.tarefa_pausar(t, "aguarda o dono")
        self.escreve("logs/T9.log", "exec\nls\n")
        r = aovivo.Amostrador().amostrar(PS % "50.0", sessoes=[], buscar_energia=False)
        self.assertEqual(r["itens"][0]["estado"], "pausada")


if __name__ == "__main__":
    unittest.main()


class TestSessaoArquivada(Base):
    """Sessão arquivada no app não vira cartão: a rodada do arquivador termina em archive_session("self") sem resultado
    e a Queima Neem parou numa ferramenta; ambas apareciam "Travada" por até 2 h (08/11)."""

    def _sessao(self, cli, app=None):
        linha = json.dumps({"message": {"role": "assistant", "stop_reason": "tool_use", "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__ccd_session_mgmt__archive_session", "input": {}}]}})
        arq = self.escreve("claude/projects/p/%s.jsonl" % cli, linha + "\n")
        antigo = time.time() - 30 * 60
        os.utime(arq, (antigo, antigo))
        if app is not None:
            self.escreve("app/a/b/local_%s.json" % cli[:6], json.dumps(dict(app, cliSessionId=cli)))
        return {"id": cli[:8], "titulo": cli, "projeto": "p", "arquivo": arq, "id_completo": cli, "modelo": "claude-opus-5-5", "nivel": 3}

    def test_arquivada_e_rodada_do_arquivador_ficam_fora_e_viva_continua(self):
        sessoes = [self._sessao("aaaaaaaa-arquivada", {"isArchived": True}),
                   self._sessao("bbbbbbbb-arquivador", {"isArchived": False, "scheduledTaskId": "orquestra-arquivador"}),
                   self._sessao("cccccccc-viva", {"isArchived": False}),
                   self._sessao("dddddddd-sem-app")]
        r = aovivo.Amostrador().amostrar("", sessoes=sessoes, buscar_energia=False)
        ids = sorted(i["id_completo"] for i in r["itens"] if i.get("id_completo"))
        self.assertEqual(ids, ["cccccccc-viva", "dddddddd-sem-app"])
        self.assertTrue(all(i["estado"] == "travada" for i in r["itens"]))
