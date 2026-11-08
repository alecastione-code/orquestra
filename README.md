<p align="center"><img src="logo/orquestra-256.png" width="128" alt="Logo do Orquestra"></p>

# Orquestra

Quadro comum e painel da orquestração (Claude + Codex). Local, sem internet, só stdlib.

- `scripts/instalar.sh` compila o app, instala o runtime, o `orq` (`~/.local/bin/orq`) e o LaunchAgent `com.orquestra.servidor`.
- Painel: http://127.0.0.1:4747 (ou `open ~/Applications/Orquestra.app`).
- Testes: `python3 -m unittest`.
- Ponte com o painel (opcional): endereço em `ORQUESTRA_PONTE_URL` ou em `ponte_url.txt` na pasta do Orquestra; token só no Keychain.
- Regras: ver a skill `orquestrar`. Coletores nunca leem `auth.json`, `.env` ou tokens.

## Criador

Criado por **Alexandre Castione** ([@alecastione-code](https://github.com/alecastione-code)), 2026.
Ideia, produto e direção: Alexandre Castione. Desenvolvido com Claude (Anthropic) e Codex (OpenAI) como executores.

## Licença e crédito

Licença Apache 2.0 (`LICENSE`). Pode usar, modificar e melhorar, inclusive comercialmente, **desde que mantenha o crédito ao criador**: toda cópia ou versão derivada precisa levar o arquivo `NOTICE` com "Criado por Alexandre Castione" e indicar o que foi modificado.

© 2026 Alexandre Castione.
