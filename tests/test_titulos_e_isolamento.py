import json
import os
import re
import subprocess
import time
import unittest

from orquestra import aovivo, coletores, config, db, servidor
from tests.base import Base

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestIsolamentoDosTestes(Base):
    def test_testes_nunca_abrem_o_banco_de_producao(self):
        assert os.environ["ORQUESTRA_DB"].startswith(self.tmp)       # o Base isola tudo numa pasta temporária
        antigo = os.environ.pop("ORQUESTRA_DB")
        try:
            with self.assertRaises(RuntimeError) as e:
                db.conectar()
            self.assertIn("banco de produção", str(e.exception))
            os.environ["ORQUESTRA_HOME"] = os.path.dirname(config.banco_de_producao())   # mesmo caminho por outra via
            with self.assertRaises(RuntimeError):
                db.pulso("claude:abcdef12", "x")
        finally:
            os.environ.pop("ORQUESTRA_HOME", None)
            os.environ["ORQUESTRA_DB"] = antigo
        db.pulso("claude:ok", "temp")   # com o banco temporário volta a funcionar
        self.assertEqual(db.pulsos_recentes()[0]["agente"], "claude:ok")

    def test_exemplo_do_hooks_md_usa_banco_temporario(self):
        with open(os.path.join(RAIZ, "docs", "HOOKS.md"), encoding="utf-8") as f:
            texto = f.read()
        bloco = re.findall(r"```bash\n(.*?)```", texto, re.S)[0]
        self.assertIn('export ORQUESTRA_DB="$(mktemp -d)/', bloco)
        casa = os.path.join(self.tmp, "casa")
        scripts = os.path.join(casa, "Library", "Application Support", "Orquestra", "runtime", "scripts")
        os.makedirs(scripts)
        os.symlink(os.path.join(RAIZ, "scripts", "hook-pulso.sh"), os.path.join(scripts, "hook-pulso.sh"))
        env = {k: v for k, v in os.environ.items() if k != "ORQUESTRA_DB"}
        r = subprocess.run(["bash", "-c", bloco], capture_output=True, text=True, env=dict(env, HOME=casa))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude:abcdef12", r.stdout)                                   # gravou — num banco temporário
        self.assertFalse(os.path.exists(os.path.join(casa, "Library", "Application Support", "Orquestra", "orquestra.db")))


def linha_user(texto, branch=None):
    d = {"cwd": "/p/scratch-2026-10-31-070190", "timestamp": "2026-11-07T10:00:00Z", "message": {"role": "user", "content": texto}}
    if branch:
        d["gitBranch"] = branch
    return json.dumps(d) + "\n"


def linha_asst(i=1):
    return json.dumps({"cwd": "/p/scratch-2026-10-31-070190", "timestamp": "2026-11-07T10:00:01Z",
                       "message": {"id": "m%d" % i, "role": "assistant", "model": "claude-sonnet-5-5", "content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"


class TestTituloDaSessao(Base):
    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.tmp, "app-sessoes")
        os.environ["ORQUESTRA_APP_SESSIONS"] = self.app
        coletores._titulos_app.update(mtime=0, v={}, base=None)

    def tearDown(self):
        os.environ.pop("ORQUESTRA_APP_SESSIONS", None)
        super().tearDown()

    def app_json(self, cli_id, titulo):
        self.escreve("app-sessoes/conta/ws/local_x%s.json" % cli_id[:4], json.dumps({
            "sessionId": "local_x", "cliSessionId": cli_id, "title": titulo,
            "scratchPromptRecents": ["SEGREDO-DO-PROMPT token=abc123"], "promptAppendSnapshot": {"append": "SEGREDO-DO-APPEND"}}))

    def sessao(self, conteudo, nome="aaaaaaaa-1111-2222-3333-444444444444"):
        self.escreve("claude/projects/-p/%s.jsonl" % nome, conteudo)
        return coletores.claude_sessoes()[0]

    def test_prioridade_app_depois_transcript_depois_prompt_depois_branch_depois_pasta(self):
        cid = "aaaaaaaa-1111-2222-3333-444444444444"
        corpo = json.dumps({"type": "custom-title", "customTitle": "Título do transcript", "sessionId": cid}) + "\n" + linha_user("Pedido curto do dono", "qa/t1") + linha_asst()
        self.app_json(cid, "Ferramentas")
        s = self.sessao(corpo)
        self.assertEqual((s["titulo"], s["titulo_origem"]), ("Ferramentas", "app"))
        os.remove(os.path.join(self.app, "conta/ws/local_xaaaa.json")); coletores._titulos_app.update(mtime=0, v={})
        s = self.sessao(corpo)
        self.assertEqual((s["titulo"], s["titulo_origem"]), ("Título do transcript", "transcript"))
        s = self.sessao(linha_user("Pedido curto do dono\nsegunda linha longa", "qa/t1") + linha_asst())
        self.assertEqual((s["titulo"], s["titulo_origem"], s["branch"]), ("Pedido curto do dono", "prompt", "qa/t1"))
        s = self.sessao(linha_user("<system-reminder>ruído</system-reminder>", "fix/y") + linha_asst())
        self.assertEqual((s["titulo"], s["titulo_origem"]), ("fix/y", "branch"))
        s = self.sessao(linha_user("<system-reminder>ruído</system-reminder>") + linha_asst())
        self.assertEqual((s["titulo"], s["titulo_origem"]), ("scratch-2026-10-31-070190", "pasta"))

    def test_primeiro_prompt_e_curto_e_sem_segredo(self):
        s = self.sessao(linha_user("Use o token=SEGREDO123 para chamar a API e depois " + "x" * 200) + linha_asst())
        self.assertNotIn("SEGREDO123", s["titulo"])
        self.assertLessEqual(len(s["titulo"]), 70)

    def test_agentes_mostram_titulo_e_pasta_como_detalhe(self):
        cid = "aaaaaaaa-1111-2222-3333-444444444444"
        self.app_json(cid, "Seller Turbo continuação")
        self.escreve("claude/projects/-p/%s.jsonl" % cid, linha_user("oi", "qa/t9") + linha_asst())
        coletores._cache_transcript.clear()
        ag = servidor.agentes(coletores.coletar_tudo())
        a = next(x for x in ag if x["id"] == "claude:aaaaaaaa")
        self.assertEqual(a["nome"], "Seller Turbo continuação")
        self.assertIn("scratch-2026-10-31-070190", a["detalhe"])
        self.assertIn("qa/t9", a["detalhe"])
        self.assertNotIn("SEGREDO", json.dumps(ag))               # só título e id do metadado do app são lidos

    def test_pulso_do_hook_ganha_o_titulo_do_app(self):
        self.app_json("bbbbbbbb-1111-2222-3333-444444444444", "Orquestra")
        db.pulso("claude:bbbbbbbb", "proj · modelo · Bash")
        a = next(x for x in servidor.agentes({"claude_sessoes": [], "codex_processos": []}) if x["id"] == "pulso:claude:bbbbbbbb")
        self.assertEqual((a["nome"], a["detalhe"]), ("Orquestra", "claude:bbbbbbbb"))

    def test_pulso_da_mesma_sessao_nao_duplica_o_agente(self):
        cid = "dddddddd-1111-2222-3333-444444444444"
        self.app_json(cid, "Orquestra")
        self.escreve("claude/projects/-p/%s.jsonl" % cid, linha_user("oi") + linha_asst())
        coletores._cache_transcript.clear()
        db.pulso("claude:dddddddd", "proj · modelo · Bash")
        db.pulso("claude:eeeeeeee", "outra · modelo · Bash")      # sessão que (ainda) não está nos transcritos: aparece pelo pulso
        ag = servidor.agentes(coletores.coletar_tudo())
        self.assertEqual(len([a for a in ag if a["nome"] == "Orquestra"]), 1)
        self.assertEqual(len([a for a in ag if a["id"].startswith("pulso:claude:eeeeeeee")]), 1)

    def test_ao_vivo_usa_o_titulo(self):
        cid = "cccccccc-1111-2222-3333-444444444444"
        self.app_json(cid, "Ferramentas")
        arq = self.escreve("claude/projects/-p/%s.jsonl" % cid, linha_user("oi") + linha_asst())
        coletores._cache_transcript.clear()
        os.environ["ORQUESTRA_SEM_NOTIFICAR"] = "1"
        r = aovivo.Amostrador().amostrar("", sessoes=coletores.claude_sessoes(), buscar_energia=False)
        it = next(i for i in r["itens"] if i["id"] == "claude:cccccccc")
        self.assertEqual(it["nome"], "Ferramentas")
        self.assertIn("scratch-2026-10-31-070190", it["detalhe"])
        os.environ.pop("ORQUESTRA_SEM_NOTIFICAR")


if __name__ == "__main__":
    unittest.main()
