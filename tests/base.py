import os
import shutil
import tempfile
import unittest


class Base(unittest.TestCase):
    """Isola tudo em pastas temporárias: nada toca a casa real."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orq-test-")
        self.env = {
            "ORQUESTRA_DB": os.path.join(self.tmp, "db", "o.db"),
            "ORQUESTRA_CODEX_HOME": os.path.join(self.tmp, "codex"),
            "ORQUESTRA_CLAUDE_HOME": os.path.join(self.tmp, "claude"),
            "ORQUESTRA_SKILLS": os.path.join(self.tmp, "claude", "skills"),
            "ORQUESTRA_SKILLS_OFF": os.path.join(self.tmp, "claude", "skills-desativadas"),
            "ORQUESTRA_LOGS_CODEX": os.path.join(self.tmp, "logs"),
            "ORQUESTRA_SEM_GH": "1",
            "ORQUESTRA_SEM_TOP": "1",
            "ORQUESTRA_PS": "",
            "ORQUESTRA_STATUS_JSON": os.path.join(self.tmp, "home", "status.json"),
            "ORQUESTRA_FILA_UNICA": os.path.join(self.tmp, "fila-unica.out"),
            "ORQUESTRA_DIARIO": os.path.join(self.tmp, "diario.md"),
            "ORQUESTRA_APP_SESSIONS": os.path.join(self.tmp, "app"),
        }
        self._antigo = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        from orquestra import aovivo, coletores
        aovivo.AMOSTRADOR.__init__()                     # o amostrador é um singleton: nada de estado de outro teste
        coletores._ultimo.update(quando=0, dados={})
        for d in ("codex", "claude/skills", "logs"):
            os.makedirs(os.path.join(self.tmp, d))

    def tearDown(self):
        for k, v in self._antigo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def escreve(self, rel, texto, binario=False):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(texto)
        return p
