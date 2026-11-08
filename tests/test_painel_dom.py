"""O painel não pode piscar: duas coletas iguais → zero mutações no DOM (logo, nenhuma animação de entrada reinicia), foco/rolagem/aba preservados.
Roda o app.js de verdade num Chrome headless (pulado se o Chrome não existir)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest

from orquestra import config, servidor
from tests.base import Base

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(os.path.exists(CHROME), "Chrome não encontrado")
class TestPainelNaoPisca(Base):
    def rodar(self, query=""):
        web = os.path.join(self.tmp, "web")
        shutil.copytree(os.path.join(RAIZ, "web"), web)
        shutil.copy(os.path.join(RAIZ, "tests", "dom", "teste_dom.html"), os.path.join(web, "teste_dom.html"))
        antigo = config.pasta_web
        config.pasta_web = lambda: web
        srv = servidor.criar(porta=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + os.path.join(self.tmp, "chrome"),
                                "--window-size=1400,900", "--virtual-time-budget=60000", "--enable-logging=stderr", "--v=0", "--dump-dom", "http://127.0.0.1:%d/teste_dom.html%s" % (srv.server_address[1], query)],
                               capture_output=True, text=True, timeout=120)
        finally:
            srv.shutdown(); srv.server_close(); config.pasta_web = antigo
        m = re.search(r'data-res="([^"]*)"', r.stdout)
        if not m and os.environ.get("ORQ_DEBUG_DOM"):
            import traceback
            try:
                servidor.api_get("/api/skills", {}); api = "api/skills ok"
            except Exception:
                api = traceback.format_exc()
            open(os.environ["ORQ_DEBUG_DOM"], "w").write("API: " + api + "\nSTDOUT:\n" + r.stdout[-1500:] + "\nSTDERR:\n" + r.stderr[-2500:])
        self.assertTrue(m, "a página de teste não terminou: " + r.stdout[-400:])
        return json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))

    def test_duas_coletas_iguais_nao_tocam_no_dom(self):
        db_ = __import__("orquestra.db", fromlist=["x"])
        t = db_.tarefa_nova("Tarefa de teste", nivel=2); db_.tarefa_pegar(t, "agente-x")
        db_.cota_registrar("claude-5h", 12.0, __import__("time").time() + 3 * 3600, "teste")
        res = self.rodar()
        self.assertEqual({a: n for a, n in res["mutacoes"].items() if n}, {}, "mutações no DOM com dados iguais: %s" % res["mutacoes"])
        self.assertEqual(len(res["mutacoes"]), 7)

    def test_faixas_novas_com_dados_nao_tocam_no_dom(self):
        """Resumo do dia, Esperando você, contexto e tarefas automáticas aparecem com dados e continuam estáveis na coleta seguinte."""
        from tests.test_executores import Corpo, asst, user
        self.escreve("claude/projects/-p/mae00000-1111-2222-3333-444444444444.jsonl",
                     "\n".join([user("a", 5000), asst("Posso seguir?", "claude-opus-5-5", 400, i=10, cr=650_000, cw=0)]) + "\n")
        import time as _t
        p = os.path.join(self.tmp, "claude/projects/-p/mae00000-1111-2222-3333-444444444444.jsonl")
        os.utime(p, (_t.time() - 600,) * 2)
        base = "claude/projects/-p/mae00000-1111-2222-3333-444444444444/subagents/agent-zz1"
        self.escreve(base + ".jsonl", "\n".join([user("faça", 900), asst("Feito\nok", "claude-sonnet-5-5", 600)]) + "\n")
        self.escreve(base + ".meta.json", json.dumps({"description": "Subagente de teste", "finished": True}))
        from orquestra import executores
        executores.sincronizar()
        res = self.rodar()
        self.assertEqual({a: n for a, n in res["mutacoes"].items() if n}, {}, "mutações no DOM com dados iguais: %s" % res["mutacoes"])

    def test_gravacao_de_10s_com_dados_reais_nao_troca_elementos(self):
        """10 s de uso real (5 s de atualização, 3 s no Ao vivo): só texto/números mudam; nenhum elemento é removido ou recriado."""
        res = self.rodar("?real=1")["real"]
        for aba, r in res.items():
            self.assertEqual(r["estruturais"], 0, "%s trocou elementos do DOM: %s" % (aba, r))

    def test_resultado_do_recomendar_e_filtro_de_skills_sobrevivem_a_nova_coleta(self):
        os.makedirs(self.env["ORQUESTRA_SKILLS"], exist_ok=True)
        for n in ("alfa", "beta"):
            self.escreve("agents/%s/SKILL.md" % n, "---\nname: %s\ndescription: faz %s\n---\n" % (n, n))
            os.symlink(os.path.join(self.tmp, "agents", n), os.path.join(self.env["ORQUESTRA_SKILLS"], n))
        f = self.rodar()["fluxos"]
        self.assertTrue(f["recomendacao"], "o resultado do Recomendar sumiu na coleta seguinte")
        self.assertEqual(f["filtro"]["antes"], 2)
        self.assertEqual((f["filtro"]["filtrado"], f["filtro"]["depoisDeColeta"]), (0, 0))   # o filtro continua aplicado
        self.assertTrue(f["filtro"]["campoMesmoNo"])

    def test_foco_texto_rolagem_e_aba_sobrevivem(self):
        res = self.rodar()
        f = res["foco"]
        self.assertTrue(f["mesmoNo"], "o campo foi recriado")
        self.assertTrue(f["ativo"], "o foco foi perdido")
        self.assertEqual(f["valor"], "consertar bug")
        self.assertTrue(f["scroll"], "a rolagem voltou ao topo")
        self.assertEqual(f["aba"], "visao")


if __name__ == "__main__":
    unittest.main()
