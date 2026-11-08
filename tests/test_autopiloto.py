import os

from orquestra import autopiloto, db
from tests.base import Base


class TestAutopiloto(Base):
    def setUp(self):
        super().setUp()
        autopiloto._estado.update(ultima=0, aviso=0)
        self.escreve("logs/fila-unica.sh", "#!/bin/zsh\n")
        self.iniciadas = []

    def rodar(self, viva, agora=10_000):
        return autopiloto.rodada(agora, viva=lambda: viva, iniciar=self.iniciadas.append)

    def eventos(self):
        return [e["texto"] for e in db.eventos() if e["tipo"] == "autopiloto"]

    def test_fila_viva_nao_mexe(self):
        self.assertEqual(self.rodar(True), "fila viva")
        self.assertEqual((self.iniciadas, self.eventos()), ([], []))

    def test_fila_morta_religa_uma_vez_e_registra(self):
        self.assertEqual(self.rodar(False), "religada")
        self.assertEqual(self.iniciadas, [os.environ["ORQUESTRA_LOGS_CODEX"]])
        self.assertEqual(self.rodar(False, agora=10_300), "esperando")   # dentro de 10 min não insiste
        self.assertEqual(len(self.iniciadas), 1)
        self.assertEqual(self.rodar(False, agora=10_700), "religada")
        self.assertEqual(len(self.eventos()), 2)

    def test_sem_acesso_avisa_uma_vez_por_hora_e_nao_religa(self):
        os.remove(os.path.join(os.environ["ORQUESTRA_LOGS_CODEX"], "fila-unica.sh"))
        self.assertEqual(self.rodar(False), "sem acesso")
        self.assertEqual(self.rodar(False, agora=10_600), "sem acesso")
        self.assertEqual(self.iniciadas, [])
        self.assertEqual(len(self.eventos()), 1)
        self.assertIn("sem acesso", self.eventos()[0])

    def test_desligado_por_variavel(self):
        os.environ["ORQUESTRA_AUTOPILOTO"] = "0"
        try:
            self.assertEqual(self.rodar(False), "desligado")
        finally:
            os.environ.pop("ORQUESTRA_AUTOPILOTO")
        self.assertEqual(self.iniciadas, [])
