# Pulso automático das sessões Claude (hooks)

O `scripts/hook-pulso.sh` registra, a cada ferramenta usada e a cada fim de turno, um pulso no quadro
(`claude:<sessão>` · projeto · modelo · ferramenta · hora). É rápido (espera o banco no máximo 1 s),
silencioso e **sempre sai com código 0**: nunca bloqueia nem faz falhar a ferramenta.

O Orquestra **não edita** o seu `~/.claude/settings.json`. Para ligar, junte este trecho ao arquivo
(se já existir a chave `hooks`, acrescente os dois itens às listas `PostToolUse` e `Stop`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "\"$HOME/Library/Application Support/Orquestra/runtime/scripts/hook-pulso.sh\"" }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "\"$HOME/Library/Application Support/Orquestra/runtime/scripts/hook-pulso.sh\"" }
        ]
      }
    ]
  }
}
```

O caminho tem espaço ("Application Support"), por isso o comando vai **entre aspas escapadas** no JSON
(`\"…\"`): sem elas o shell quebra no espaço e o pulso nunca roda. Alternativa sem espaço: o instalador cria
o link `~/.local/bin/orq-pulso`, e então o comando pode ser só `"$HOME/.local/bin/orq-pulso"`.
O teste automático (`tests/test_bloco_a.py`) roda exatamente a string do JSON deste arquivo com `sh -c`.

Teste sem ligar nada (a mesma string do JSON, via `sh -c`):

```bash
export ORQUESTRA_DB="$(mktemp -d)/teste.db"   # banco TEMPORÁRIO: o exemplo nunca grava no quadro real
echo '{"session_id":"abcdef123456","cwd":"/tmp/meu-projeto","tool_name":"Bash","hook_event_name":"PostToolUse"}' \
  | sh -c '"$HOME/Library/Application Support/Orquestra/runtime/scripts/hook-pulso.sh"'; echo "saiu com $?"
sqlite3 "$ORQUESTRA_DB" "select agente, nota from pulsos"   # prova que gravou no banco temporário
unset ORQUESTRA_DB
```

O hook só lê o JSON do stdin e grava `agente`, `hora` e a nota (projeto · modelo · ferramenta).
Não grava o comando executado nem o conteúdo de arquivos.
