"use strict";
const $ = (s, r = document) => r.querySelector(s);
const NIVEL = ["N0 script", "N1 leitura", "N2 código", "N3 decisão"];
let aba = "visao", cacheAV = null, cacheDia = null, cacheML = null, cacheCU = null, cacheMT = null, periodoCU = "semana", porCU = "modelo", cacheST = null, V = null, cacheSkills = [], cacheTar = [], cacheMsg = [], cachePrj = [];
let digitando = false, recResp = null, filtroSkill = "", abaDesenhada = null;

// Handlers ficam em e._h[tipo] e um único listener delega: o patch troca o handler sem recriar o elemento.
function liga(e, t) {
  e._l = e._l || {};
  if (!e._l[t]) { e._l[t] = 1; e.addEventListener(t, (ev) => { if (e._h && e._h[t]) return e._h[t](ev); }); }
}
function h(tag, attrs, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) { (e._h = e._h || {})[k.slice(2)] = v; liga(e, k.slice(2)); }
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const f of filhos.flat(Infinity)) if (f != null && f !== false) e.append(f.nodeType ? f : document.createTextNode(String(f)));
  return e;
}
const api = async (url, corpo) => {
  const r = await fetch(url, corpo ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo) } : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.erro || r.status);
  return j;
};
function dur(s) {
  if (s == null) return "—";
  if (s < 60) return s + " s";
  if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
  return Math.floor(s / 86400) + " d";
}
const hora = (t) => t ? new Date(t * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const mi = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + " mi" : n >= 1e3 ? (n / 1e3).toFixed(1) + " mil" : String(n);
const tagN = (n) => h("span", { class: "tag n" + n }, NIVEL[n]);

function confirmar(titulo, texto) {
  return new Promise((ok) => {
    $("#m-t").textContent = titulo; $("#m-p").textContent = texto; $("#modal").hidden = false;
    const fim = (v) => { $("#modal").hidden = true; $("#m-s").onclick = $("#m-n").onclick = null; ok(v); };
    $("#m-s").onclick = () => fim(true); $("#m-n").onclick = () => fim(false);
  });
}

// ---------- telas
const NOME_COTA = (a) => a === "claude-5h" ? "Claude · janela de 5 h" : a === "claude-semanal" ? "Claude · semanal (todos os modelos)" : a.startsWith("claude-semanal-") ? "Claude · semanal · " + a.slice(15).replace(/^./, (x) => x.toUpperCase()) : a === "codex" ? "Codex · semanal" : a;
function quando(t) { return t ? new Date(t * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"; }
function idadeTxt(seg) { return seg < 3600 ? Math.max(Math.round(seg / 60), 0) + " min" : Math.floor(seg / 3600) + " h " + String(Math.floor((seg % 3600) / 60)).padStart(2, "0") + " min"; }
function horaTxt(t) { return t ? new Date(t * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—"; }
function cotaCard(c) {
  const est = c.estoura, curta = c.janela_h <= 24, estimado = c.origem === "estimado";
  // "15% confirmado há 3 min" ou "≈ 22% estimado (confirmado 15% às 23:20)"; zerou sozinho na renovação também é estimativa
  const leitura = c.origem === "zerou na renovação" ? "≈ 0% (a janela renovou; recomeçou a contar)"
    : estimado ? "≈ " + Math.round(c.usado_pct) + "% estimado (confirmado " + c.confirmado_pct + "% às " + horaTxt(c.confirmado) + (c.resetou ? "; a janela renovou" : "") + ")"
    : Math.round(c.usado_pct) + "% confirmado há " + idadeTxt(c.idade_leitura_s);
  return h("div", { class: "cota" },
    h("div", { class: "linha" }, h("div", {}, h("b", {}, NOME_COTA(c.agente)), h("br"),
      h("small", {}, "renova " + quando(c.renova_em) + (c.horas_ate_renovar != null ? " · em " + (c.horas_ate_renovar < 48 ? c.horas_ate_renovar.toFixed(1) + " h" : (c.horas_ate_renovar / 24).toFixed(1) + " dias") : ""))),
      h("big", {}, (estimado || c.resetou ? "≈" : "") + Math.round(c.usado_pct) + "%")),
    h("div", { class: "barra" + (est ? " alta" : "") }, h("div", { style: "width:" + Math.min(100, c.usado_pct) + "%" })),
    h("small", {}, leitura + " · disponível " + c.disponivel_pct + "% · ritmo " + c.ritmo_atual + " " + c.ritmo_unidade + " (permitido " + (c.ritmo_permitido != null ? c.ritmo_permitido : "—") + ") · previsão " + (c.previsao_fim_pct != null ? c.previsao_fim_pct + "% ao renovar" : "—")),
    c.parado_sem_consumo ? h("small", { class: "etq" }, "parado, sem consumo desde " + horaTxt(c.confirmado)) : null,
    c.leitura_velha && !estimado ? h("small", { class: "etq" }, "lido há " + idadeTxt(c.idade_leitura_s)) : null,    // etiqueta cinza discreta, não alerta
    est ? h("div", { class: "alerta", style: "margin-top:10px" }, "Vai estourar antes de renovar" + (estimado ? " (estimado)" : "") + (curta ? " — ritmo da última hora" : " — ritmo das últimas 24 h") + " · só nível 0–1 e urgentes.") : null);
}
function agenteLinha(a) {
  const rot = { orquestrador: "Orquestrador", claude: "Sessão Claude", codex: "Codex" }[a.tipo];
  return h("div", { class: "ag" + (a.vivo ? " vivo" : "") },
    h("i", { class: "pt" }),
    h("div", {}, h("b", {}, a.nome), tagN(a.nivel), h("span", { class: "tag n0" }, rot),
      h("small", {}, [a.modelo !== "?" ? a.modelo : "", a.detalhe, a.tarefa, a.idade_log != null ? "log mexeu há " + dur(a.idade_log) : ""].filter(Boolean).join(" · "))),
    h("span", { class: "tempo" }, a.vivo ? "há " + dur(a.ha_segundos) : "parada " + dur(a.ha_segundos)));
}
function cartaoCodex(x) {
  if (!x) return null;
  const c = x.cota, rodando = x.rodando > 0;
  const quanto = x.ultima_execucao_s == null ? "sem execução registrada" : "última execução há " + dur(x.ultima_execucao_s);
  return h("section", { class: "card c7" }, h("h2", {}, "Codex", h("em", {}, rodando ? x.rodando + " rodando" : "parado")),
    h("p", { class: "ef", style: "font-size:15px" }, h("strong", {}, rodando ? "Rodando agora" : "Parado"), " · " + quanto +
      (c ? " · cota " + Math.round(c.usado_pct) + "% (ritmo " + c.ritmo_atual + " " + c.ritmo_unidade + ", previsão " + (c.previsao_fim_pct != null ? c.previsao_fim_pct + "%" : "—") + " ao renovar" + (c.estoura ? " ⚠" : "") + ")" : " · cota sem leitura")),
    h("p", { class: "ef" }, x.sessoes_24h + " sessão(ões) nas últimas 24 h · " + mi(x.tokens_24h.i + x.tokens_24h.o) + " tokens (sem cache) · " + x.pts_cota_24h + " pts da cota semanal" + (c && c.leitura_velha ? " · lido há " + idadeTxt(c.idade_leitura_s) : "")));
}
const RS = (v) => v == null ? "—" : "R$ " + v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function resumoDiaCard(r) {
  if (!r) return null;
  return h("section", { class: "card c12 resumo" }, h("h2", {}, "Resumo do dia", h("em", {}, r.dia)), r.linhas.map((l) => h("p", { class: "ef" }, l)));
}
function esperandoCard(ws) {
  if (!ws || !ws.length) return null;
  return h("section", { class: "card c12 espera" }, h("h2", {}, "Esperando você", h("em", {}, ws.length + " sessão(ões), da mais antiga para a mais nova")),
    ws.map((w) => h("div", { class: "espera-i" },
      h("div", {}, w.link ? h("a", { class: "lk", href: w.link, title: "Abrir esta sessão no app Claude" }, w.titulo) : h("b", {}, w.titulo),
        h("small", {}, " · parada há " + dur(w.parada_s) + (w.projeto ? " · " + w.projeto : ""))),
      w.frase ? h("small", { class: "frase" }, "“" + w.frase + "”") : null)));
}
function contextoFaixas(cs) {
  if (!cs || !cs.length) return null;
  const COR = { criando: "alerta amarela", passou: "alerta ok", nao_passou: "alerta" };
  return h("div", { class: "c12" }, cs.map((c) => h("div", { class: COR[c.estado] || "alerta" },
    c.link ? h("a", { class: "lk", href: c.link, title: c.estado === "passou" ? "Abrir a sessão nova" : "Abrir o chat" }, c.texto) : c.texto)));
}
function arquivadorCard(a) {
  if (!a) return null;
  const t = a.tempo_status_s;
  return h("section", { class: "card c12" + (a.atrasado ? " espera" : "") }, h("h2", {}, "Arquivador",
      h("em", {}, a.ultima ? "última rodada " + horaTxt(a.ultima) + " · próxima " + horaTxt(a.proxima) : "nenhuma rodada ainda")),
    a.atrasado ? h("div", { class: "alerta" }, "Mais de 70 min sem rodada do arquivador (tarefa agendada orquestra-arquivador).") : null,
    a.erro ? h("div", { class: "alerta" }, "Erro na rodada " + horaTxt(a.erro.quando) + ": " + a.erro.texto) : null,
    h("p", { class: "ef" }, "Arquivadas nesta rodada: " + (a.arquivadas.length ? a.arquivadas.map((x) => x.sessao + " (" + x.motivo.replace(" (arquivador automático)", "") + ")").join(" · ") : "nenhuma")),
    h("p", { class: "ef" }, "Puladas: " + (a.puladas.length ? a.puladas.map((x) => x.titulo + " — " + x.motivo).join(" · ") : "nenhuma")),
    h("p", { class: "ef" }, "orq status --json: " + (t == null ? "ainda não medido" : t.toFixed(2).replace(".", ",") + " s" + (t < 1 ? " (meta < 1 s ✓)" : " ⚠ acima da meta de 1 s"))));
}
const ROT_ETAPA = { pr_aberto: "PR aberto", na_fila: "na fila", mesclado: "mesclado", no_ar: "no ar", conferido: "conferido", terminado: "terminado" };
function concluidosHoje(t) {
  if (!t) return null;
  const lista = t.concluidos_hoje || [];
  return h("section", { class: "card c12" }, h("h2", {}, "Concluídos hoje", h("em", {}, lista.length + " · atualizado " + horaTxt(Date.parse(t.gerado_em) / 1000))),
    lista.length ? lista.map((x) => h("div", { class: "espera-i" },
      h("div", {}, x.pr && x.pr.url ? h("a", { class: "lk", href: x.pr.url }, x.titulo) : h("b", {}, x.titulo), h("small", {}, x.pr ? " · PR #" + x.pr.numero : "")),
      h("small", {}, Object.entries(x.etapas).filter(([k]) => ROT_ETAPA[k]).map(([k, v]) => ROT_ETAPA[k] + " " + horaTxt(Date.parse(v) / 1000) + (k === "conferido" && x.conferido_por ? " (" + x.conferido_por + ")" : "")).join(" → ")),
      x.ultima_frase ? h("small", { class: "frase" }, x.ultima_frase) : null)) : h("div", { class: "vazio" }, "Nada concluído ainda hoje."));
}
function irPara(destino) { const b = document.querySelector('#abas button[data-aba="' + destino + '"]'); if (b) b.click(); }
function pendencias(d) {
  const it = [];
  if (d.melhorias_sugeridas) it.push([d.melhorias_sugeridas + " melhoria(s) esperando sua decisão", "melhorias"]);
  if (d.gates_falhos) it.push([d.gates_falhos + " gate(s) falhando no Seller Turbo", "seller"]);
  if (d.fila_espera) it.push([d.fila_espera + " tarefa(s) esperando cota", "tarefas"]);
  if (d.erros && d.erros.length) it.push(["coleta com erro: " + d.erros.join("; ").slice(0, 120), "visao"]);
  if (!it.length) return null;
  return h("div", { class: "c12 pend" }, it.map(([t, a]) => h("button", { class: "etq clic", onclick: () => irPara(a) }, t)));
}
function agendadasEEventos(d) {
  return [h("section", { class: "card c6" }, h("h2", {}, "Tarefas agendadas", h("em", {}, (d.agendadas || []).length)),
      (d.agendadas || []).length ? d.agendadas.slice(0, 12).map((x) => h("div", { class: "msg" }, h("b", {}, x.nome), h("small", {}, x.descricao || ""))) : h("div", { class: "vazio" }, "Nenhuma tarefa agendada encontrada.")),
    h("section", { class: "card c6" }, h("h2", {}, "Últimos eventos"),
      (d.eventos || []).length ? d.eventos.map((e) => h("div", { class: "msg" }, h("small", {}, hora(e.quando) + " · " + e.tipo), e.texto)) : h("div", { class: "vazio" }, "Sem eventos ainda."))];
}
function placarAuto(ps) {
  return h("section", { class: "card c12" }, h("h2", {}, "Executores automáticos", h("em", {}, "subagentes e Codex · 7 dias")),
    ps && ps.length ? h("div", { class: "tok", style: "grid-template-columns:1fr auto auto auto auto auto" }, ["modelo · executor", "tarefas", "feitas", "rodando", "bloqueadas", "custo equiv."].map((x) => h("span", {}, x)),
      ps.map((r) => [h("b", { style: "text-align:left" }, (r.modelo || "?") + " · " + (r.executor || "—")), h("b", {}, r.total), h("b", {}, r.feitas || 0), h("b", {}, r.rodando || 0), h("b", {}, r.bloqueadas || 0), h("b", {}, RS(r.brl))]))
      : h("div", { class: "vazio" }, "Nenhum subagente nem rollout do Codex nos últimos 7 dias."));
}
function telaVisao() {
  const d = V, c = d.tarefas_contagem;
  const vivos = d.agentes.filter((a) => a.vivo), inativos = d.agentes.filter((a) => !a.vivo),
    parados = [...inativos.filter((a) => a.tipo !== "codex").slice(0, 4), ...inativos.filter((a) => a.tipo === "codex").slice(0, 4)].sort((x, y) => x.ha_segundos - y.ha_segundos);   // Claude e Codex aparecem juntos
  const tok = Object.entries(d.tokens_hoje);
  // handlers buscam os campos no DOM vivo (#id): o patch mantém os nós antigos, então não podem capturar nós desta renderização
  const recomendar = async () => {
    const el = $("#rec-in");
    if (!el || !el.value.trim()) return;
    recResp = await api("/api/recomendar", { descricao: el.value });
    desenhar();
  };
  const recIn = h("input", { id: "rec-in", placeholder: "Descreva a tarefa — ex.: consertar bug com teste", onfocus() { digitando = true; }, onblur() { digitando = false; }, onkeydown(e) { if (e.key === "Enter") recomendar(); } });
  const r = recResp;
  const recOut = h("div", {}, r ? h("div", { class: "resp" }, h("b", {}, "Nível " + r.nivel + " · " + r.executor), " (" + r.modelo + ")" + (r.dono ? " · precisa do dono" : ""), h("br"), r.porque,
    r.alternativa ? h("div", { style: "margin-top:6px;color:var(--mudo)" }, "Alternativa: " + r.alternativa) : null) : null);
  return h("div", { class: "grade" },
    resumoDiaCard(d.resumo_dia), esperandoCard(d.esperando), concluidosHoje(d.trabalhos), contextoFaixas(d.contexto), arquivadorCard(d.arquivador), pendencias(d),
    d.economia_dia && d.economia_dia.tarefas ? h("div", { class: "c12" }, h("div", { class: "resp" }, h("b", {}, "Economia estimada hoje: R$ " + d.economia_dia.valor.toFixed(2)), " em " + d.economia_dia.tarefas + " tarefa(s) vs. executar no modelo do orquestrador (estimativa por tokens × config/precos.json).")) : null,
    d.desperdicio && d.desperdicio.length ? h("div", { class: "c12" }, d.desperdicio.map((x) => h("div", { class: "alerta" }, h("b", {}, "Desperdício · " + x.projeto + " (" + x.modelo + "): "), x.sugestao))) : null,
    d.alertas.length ? h("div", { class: "c12" }, d.alertas.map((a) => h("div", { class: "alerta" }, a))) : null,
    h("section", { class: "card c7" }, h("h2", {}, "Agentes vivos agora", h("em", {}, d.vivos + " ativos")),
      vivos.length ? vivos.map(agenteLinha) : h("div", { class: "vazio" }, "Ninguém trabalhando neste minuto."),
      parados.length ? [h("h2", { style: "margin-top:18px" }, "Atividade recente (24 h)"), parados.map(agenteLinha)] : null),
    h("section", { class: "card c5" }, h("h2", {}, "Cotas"), d.cotas.length ? d.cotas.map(cotaCard) : h("div", { class: "vazio" }, "Sem leitura de cota. A IA informa com `orq cotas-informar <arquivo.json>` ou `orq cota <agente> --usado N --renova DATA`.")),
    cartaoCodex(d.codex),
    h("section", { class: "card c5" }, h("h2", {}, "Quadro"),
      h("div", { class: "kpis" }, [["nova", "Novas"], ["pega", "Em andamento"], ["aguardando", "Aguardando revisão"], ["feita", "Feitas"], ["bloqueada", "Bloqueadas"]].map(([e, n]) => h("div", { class: "kpi" }, h("b", {}, c[e] || 0), h("span", {}, n))))),
    h("section", { class: "card c12" }, h("h2", {}, "Placar por executor", h("em", {}, "entrega de primeira")),
      d.placar && d.placar.length ? h("div", { class: "tok", style: "grid-template-columns:auto 1fr auto auto auto auto" }, ["nível", "modelo · tipo", "total", "1ª vez", "voltou", "quebrou"].map((x) => h("span", {}, x)),
        d.placar.map((r) => [h("b", { style: "text-align:left" }, "N" + r.nivel), h("b", { style: "text-align:left" }, (r.modelo || "?") + " · " + (r.tipo || "—")), h("b", {}, r.total), h("b", {}, Math.round(r.taxa_primeira * 100) + "%"), h("b", {}, r.voltou || 0), h("b", {}, r.quebrou || 0)]))
        : h("div", { class: "vazio" }, "Sem tarefas revisadas ainda — o placar nasce quando o orquestrador aprova ou devolve.")),
    placarAuto(d.placar_auto), agendadasEEventos(d),
    h("section", { class: "card c7" }, h("h2", {}, "Tokens de hoje por modelo"),
      tok.length ? h("div", { class: "tok" }, h("span", {}, "modelo"), h("span", {}, "entrada/cache"), h("span", {}, "saída"),
        tok.sort((a, b) => b[1].saida - a[1].saida).map(([m, v]) => [h("b", { style: "text-align:left" }, m), h("b", {}, mi(v.entrada) + " / " + mi(v.cache)), h("b", {}, mi(v.saida))]))
        : h("div", { class: "vazio" }, "Nenhuma sessão Claude hoje.")),
    h("section", { class: "card c12" }, h("h2", {}, "Recomendar executor"),
      h("div", { class: "rec" }, recIn, h("button", { class: "btn", onclick: recomendar }, "Recomendar")), recOut));
}
function telaTarefas() {
  const nome = { nova: "Novas", pega: "Em andamento", aguardando: "Aguardando revisão", feita: "Feitas", bloqueada: "Bloqueadas" };
  const tit = h("input", { id: "tar-tit", placeholder: "Título da tarefa", onfocus() { digitando = true; }, onblur() { digitando = false; } });
  const niv = h("select", { id: "tar-niv" }, [0, 1, 2, 3].map((n) => h("option", { value: n, selected: n === 2 }, NIVEL[n])));
  const prj = h("input", { id: "tar-prj", placeholder: "Projeto", onfocus() { digitando = true; }, onblur() { digitando = false; } });
  const criar = async () => { const t = $("#tar-tit"); if (!t.value.trim()) return; await api("/api/tarefas", { titulo: t.value, nivel: +$("#tar-niv").value, projeto: $("#tar-prj").value }); t.value = ""; await carregar(); };
  const acao = (t, ac, extra) => h("button", { class: ac === "bloqueada" ? "sec" : "", onclick: async () => { try { await api("/api/tarefas/" + t.id, { acao: ac, ...extra() }); await carregar(); } catch (e) { alert(e.message); } } }, { pegar: "Pegar", feita: "Concluir", revisada: "Aprovar revisão", voltou: "Devolver", bloqueada: "Bloquear" }[ac]);
  return h("div", {},
    h("div", { class: "form" }, tit, niv, prj, h("button", { class: "btn", onclick: criar }, "Nova tarefa")),
    h("div", { class: "quadro" }, Object.keys(nome).map((e) => {
      const ts = cacheTar.filter((t) => t.estado === e && !t.auto);
      return h("div", { class: "col" }, h("h3", {}, nome[e], h("span", {}, ts.length)),
        ts.map((t) => h("div", { class: "tar" }, h("b", {}, "#" + t.id + " " + t.titulo), tagN(t.nivel),
          h("small", {}, [t.projeto, t.agente, t.resultado].filter(Boolean).join(" · ") || "—"),
          t.custo && (t.custo.custou != null || t.custo.fatia_cota_codex_pts != null) ? h("small", {}, "≈ " + t.custo.texto) : null,
          t.espera ? h("small", { style: "color:#f5c542" }, "⏸ em espera: " + t.espera) : null,
          t.entrega ? h("small", {}, "entrega: " + t.entrega + (t.voltas ? " · voltou " + t.voltas + "×" : "")) : null,
          e === "feita" ? null : h("div", { class: "bt" },
            e === "nova" ? acao(t, "pegar", () => ({ agente: prompt("Quem pega?", "orquestrador") || "dono" })) : null,
            e === "aguardando" ? acao(t, "revisada", () => ({ por: "dono" })) : acao(t, "feita", () => ({ resultado: prompt("Resultado em 3 linhas:") || "" })),
            e === "aguardando" ? acao(t, "voltou", () => ({ motivo: prompt("Por que volta?") || "ajustar" })) : null,
            e === "bloqueada" ? null : acao(t, "bloqueada", () => ({ motivo: prompt("Motivo:") || "sem motivo" }))))),
        ts.length ? null : h("div", { class: "vazio" }, "—"));
    })), telaExecutores());
}
function tarefaAuto(t) {
  const dur_ = t.iniciado && t.concluido ? dur(Math.round(t.concluido - t.iniciado)) : t.estado === "pega" ? "rodando há " + dur(Math.round(Date.now() / 1000 - t.iniciado)) : "—";
  const tk = (t.tokens_in || 0) + (t.tokens_out || 0) + (t.tokens_cache || 0);
  return h("div", { class: "tar auto" }, h("b", {}, t.titulo), tagN(t.nivel), h("span", { class: "tag n0" }, { feita: "feita", pega: "rodando", bloqueada: "parou" }[t.estado] || t.estado),
    h("small", {}, [t.executor, t.projeto, t.sessao_mae ? "mãe " + t.sessao_mae.slice(0, 8) : "", hora(t.iniciado) + " → " + (t.concluido ? hora(t.concluido) : "…"), dur_, mi(tk) + " tokens", t.custo_brl != null ? "≈ " + RS(t.custo_brl) : ""].filter(Boolean).join(" · ")),
    t.resultado ? h("small", { class: "frase" }, t.resultado) : null);
}
function telaExecutores() {
  const as = cacheTar.filter((t) => t.auto);
  return h("section", { class: "card", style: "margin-top:14px" }, h("h2", {}, "Tarefas automáticas dos executores", h("em", {}, as.length + " mais recentes")),
    h("p", { class: "ef" }, "Cada subagente das sessões Claude e cada execução do Codex entra aqui sozinho, com título, modelo, sessão-mãe, tempo, tokens, custo equivalente e o resultado em 3 linhas."),
    as.length ? as.map(tarefaAuto) : h("div", { class: "vazio" }, "Nenhum executor registrado ainda."));
}
function telaConversa() {
  const para = h("input", { id: "msg-para", placeholder: "Para (orquestrador, codex, …)", value: "orquestrador", onfocus() { digitando = true; }, onblur() { digitando = false; } });
  const enviar = async () => { const t = $("#msg-txt"); if (!t.value.trim()) return; await api("/api/mensagens", { de: "dono", para: $("#msg-para").value, texto: t.value }); t.value = ""; await carregar(); };
  const txt = h("input", { id: "msg-txt", placeholder: "Mensagem do dono", onfocus() { digitando = true; }, onblur() { digitando = false; }, onkeydown(e) { if (e.key === "Enter") enviar(); } });
  return h("section", { class: "card" }, h("h2", {}, "Conversa entre agentes"),
    h("div", { class: "form", style: "grid-template-columns:1fr 3fr auto" }, para, txt, h("button", { class: "btn", onclick: enviar }, "Enviar")),
    cacheMsg.some((m) => !m.lida) ? h("div", { class: "rec" }, h("button", { class: "btn sec", onclick: async () => { await api("/api/mensagens", { acao: "lidas" }); await carregar(); } }, "Marcar todas como lidas")) : null,
    cacheMsg.length ? cacheMsg.map((m) => h("div", { class: "msg" + (m.de === "dono" ? " dono" : "") }, h("small", {}, m.de + " → " + m.para + " · " + hora(m.criado) + (m.lida ? "" : " · não lida")), m.texto))
      : h("div", { class: "vazio" }, "Sem mensagens. Agentes falam por `orq msg <para> \"texto\"`."));
}
function telaProjetos() {
  const nome = h("input", { id: "prj-nome", placeholder: "Novo projeto", onfocus() { digitando = true; }, onblur() { digitando = false; } });
  return h("section", { class: "card" }, h("h2", {}, "Projetos"),
    h("div", { class: "rec" }, nome, h("button", { class: "btn", onclick: async () => { const el = $("#prj-nome"); if (el.value.trim()) { await api("/api/projetos", { nome: el.value.trim() }); el.value = ""; await carregar(); } } }, "Criar")),
    cachePrj.length ? cachePrj.map((p) => h("div", { class: "pj" }, h("b", {}, p.nome),
      h("span", { class: "tempo" }, ["nova", "pega", "feita", "bloqueada"].map((e) => (p.tarefas[e] || 0) + " " + e).join(" · "))))
      : h("div", { class: "vazio" }, "Nenhum projeto. Crie um acima ou via `orq projeto novo`."));
}
function telaSkills() {
  const busca = h("input", { id: "sk-busca", placeholder: "Filtrar skills…", value: filtroSkill, oninput(e) { filtroSkill = e.target.value; desenhar(); } });
  const linhas = () => cacheSkills.filter((s) => !filtroSkill || s.nome.includes(filtroSkill.toLowerCase())).map((s) =>
    h("div", { class: "skill" + (s.ativa ? "" : " off") },
      h("div", {}, h("b", {}, s.nome), h("p", { class: "ef" }, h("strong", {}, s.ativa ? s.se_ativa.slice(0, 7) : s.se_desativada.slice(0, 11)), (s.ativa ? s.se_ativa : s.se_desativada).replace(/^(Ativa|Desativada):/, "")),
        s.custo ? h("p", { class: "ef" }, h("strong", {}, "Custo (7 dias, estimativa): "), s.custo.usos + " usos · carregar " + brl(s.custo.brl_carregar) + " + trabalho guiado " + brl(s.custo.brl_guiado) + " (" + s.custo.pct_gasto + "% do gasto) · "
          + (s.custo.custo_por_resultado != null ? brl(s.custo.custo_por_resultado) + " por resultado" : "sem resultado associado") + " · recomendação: " + s.custo.acao + " — " + s.custo.porque,
          s.custo.acao === "otimizar" || s.custo.acao === "pausar" ? h("button", { class: "btn sec", style: "margin-left:8px;padding:3px 12px;font-size:12px", async onclick() { try { const r = await api("/api/skills/otimizar", { nome: s.nome }); alert("Proposta registrada em Melhorias: " + r.proposta); } catch (e) { alert(e.message); } } }, "Otimizar") : null) : h("p", { class: "ef" }, "Sem uso nos últimos 7 dias: custo zero (só ocupa a lista).")),
      h("button", { class: "chave" + (s.ativa ? " on" : ""), "aria-label": (s.ativa ? "Desativar " : "Ativar ") + s.nome, title: s.ativa ? "Desativar" : "Ativar", async onclick() {
        const ac = s.ativa ? "desativar" : "ativar";
        if (!(await confirmar((s.ativa ? "Desativar " : "Ativar ") + s.nome + "?", s.ativa ? "O link vai para ~/.claude/skills-desativadas/ (nada é apagado). " + s.se_desativada : "O link volta para ~/.claude/skills/. " + s.se_ativa))) return;
        try { await api("/api/skills", { nome: s.nome, acao: ac, confirmar: true }); await carregar(); } catch (e) { alert(e.message); }
      } })));
  const lista = h("div", {}, ...linhas());
  return h("section", { class: "card" }, h("h2", {}, "Skills", h("em", {}, cacheSkills.filter((s) => s.ativa).length + " ativas · " + cacheSkills.filter((s) => !s.ativa).length + " desativadas")), busca, lista);
}

function spark(hist, cor) {
  const ns = "http://www.w3.org/2000/svg", W = 220, H = 44, svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H); svg.setAttribute("class", "spark");
  if (!hist || hist.length < 2) { const t = document.createElementNS(ns, "text"); t.setAttribute("x", 4); t.setAttribute("y", 26); t.setAttribute("fill", "#a9aaa5"); t.setAttribute("font-size", "11"); t.textContent = "sem leitura de CPU"; svg.append(t); return svg; }
  const max = Math.max(10, ...hist.map((p) => p[1])), pts = hist.map((p, i) => [(i / (hist.length - 1)) * W, H - 3 - (p[1] / max) * (H - 8)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = document.createElementNS(ns, "path"); area.setAttribute("d", d + " L" + W + " " + H + " L0 " + H + " Z"); area.setAttribute("fill", cor); area.setAttribute("opacity", ".16");
  const ln = document.createElementNS(ns, "path"); ln.setAttribute("d", d); ln.setAttribute("fill", "none"); ln.setAttribute("stroke", cor); ln.setAttribute("stroke-width", "2"); ln.setAttribute("stroke-linejoin", "round");
  svg.append(area, ln); return svg;
}
const ESTADO = { ativa: ["Ativa", "#5fd39a"], quieta: ["Quieta", "#a9aaa5"], travada: ["Travada", "#ff5d5d"], pausada: ["Pausada", "#f5c542"],
  esperando_dono: ["Esperando o dono", "#7cb7ff"], terminou: ["Terminou", "#5fd39a"], falhou: ["Falhou", "#ff5d5d"] };
function cartaoAV(i) {
  const [rot, cor] = ESTADO[i.estado] || [i.estado, "#a9aaa5"];
  return h("div", { class: "av" + (i.estado === "travada" || i.estado === "falhou" ? " mal" : "") },
    h("div", { class: "av-topo" }, h("div", {}, h("b", {}, i.titulo || i.nome), tagN(i.nivel), h("span", { class: "tag n0" }, i.modelo), i.detalhe ? h("small", { style: "display:block;color:var(--mudo)" }, i.detalhe) : null),
      h("span", { class: "est", style: "color:" + cor + ";border-color:" + cor }, rot)),
    h("p", { class: "motivo" }, i.motivo),
    h("p", { class: "etapa" }, h("strong", {}, "Etapa: "), i.etapa_agente || i.etapa || "—"),
    h("div", { class: "av-nums" },
      h("div", {}, h("b", {}, i.cpu != null ? i.cpu + "%" : "—"), h("span", {}, "CPU" + (i.processos > 1 ? " · " + i.processos + " proc." : ""))),
      h("div", {}, h("b", {}, i.rss_mb != null ? i.rss_mb + " MB" : "—"), h("span", {}, "memória")),
      h("div", {}, h("b", {}, i.energia ? i.energia.valor + (i.energia.estimativa ? " CPU-s" : "") : "—"), h("span", {}, i.energia && i.energia.estimativa ? "energia (estimativa)" : "energia")),
      h("div", {}, h("b", {}, dur(i.tempo_s)), h("span", {}, "rodando")),
      h("div", {}, h("b", {}, i.eta_texto), h("span", {}, "para terminar"))),
    spark(i.cpu_hist, cor),
    i.pid && i.vivo && i.tipo === "codex" ? h("div", { class: "bt acoes-av" },
      i.estado === "pausada" ? btnAcao("Retomar", i, "retomar") : btnAcao("Pausar", i, "pausar"),
      h("button", { class: "sec", onclick: async () => { const ex = prompt("Redistribuir para qual executor? (ex.: Claude Sonnet)", "Claude Sonnet"); if (!ex) return;
        try { await api("/api/acoes", { acao: "redistribuir", tarefa_id: i.tarefa_id, executor: ex, titulo: i.titulo, nivel: i.nivel }); alert("Tarefa devolvida à fila para " + ex + ". Encerre o processo antigo se não quiser que continue."); } catch (e) { alert(e.message); } } }, "Redistribuir"),
      h("button", { class: "perigo", onclick: async () => { if (!(await confirmar("Encerrar " + (i.titulo || i.nome) + "?", "Envia SIGTERM ao processo " + i.pid + " e filhos. O trabalho em andamento pode ser perdido."))) return;
        try { await api("/api/acoes", { acao: "encerrar", id: i.id, confirmar: true }); await carregar(); } catch (e) { alert(e.message); } } }, "Encerrar")) : null);
}
function btnAcao(rot, i, ac) {
  return h("button", { class: "sec", onclick: async () => { try { await api("/api/acoes", { acao: ac, id: i.id, tarefa_id: i.tarefa_id }); await carregar(); } catch (e) { alert(e.message); } } }, rot);
}
function telaAoVivo() {
  const d = cacheAV; if (!d) return h("div", { class: "vazio" }, "Carregando…");
  const r = d.resumo;
  return h("div", {}, h("div", { class: "kpis", style: "margin-bottom:14px" },
    [["ativa", "Ativas"], ["travada", "Travadas"], ["pausada", "Pausadas"], ["esperando_dono", "Esperando o dono"], ["falhou", "Falhas"]].map(([k, n]) =>
      h("div", { class: "kpi" }, h("b", { style: (k === "travada" || k === "falhou") && r[k] ? "color:var(--mal)" : "" }, r[k]), h("span", {}, n)))),
    h("p", { class: "vazio" }, "Atualiza a cada 3 s · energia: " + d.energia),
    h("div", { class: "avgrade" }, d.itens.length ? d.itens.map(cartaoAV) : h("div", { class: "vazio" }, "Nenhum agente ou tarefa rodando.")));
}

function telaDia() {
  const d = cacheDia; if (!d) return h("div", { class: "vazio" }, "Carregando…");
  const baixar = async () => { const t = await (await fetch("/api/linha-do-tempo.md?dia=" + d.dia)).text(); const a = h("a", { href: URL.createObjectURL(new Blob([t], { type: "text/markdown" })), download: "linha-do-tempo-" + d.dia + ".md" }); document.body.append(a); a.click(); a.remove(); };
  return h("section", { class: "card" }, h("h2", {}, "Linha do tempo · " + d.dia, h("em", {}, "economia estimada R$ " + d.economia.valor.toFixed(2))),
    h("div", { class: "rec" }, h("button", { class: "btn", onclick: baixar }, "Exportar Markdown")),
    d.itens.length ? d.itens.map((i) => h("div", { class: "msg" },
      h("small", {}, new Date(i.quando * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) + " · " + (i.tipo === "tarefa" ? i.agente + " · N" + i.nivel + " · " + i.estado + (i.entrega ? " · " + i.entrega : "") : i.tipo.replace("evento:", ""))),
      i.tipo === "tarefa" ? [h("b", {}, "#" + i.id + " " + i.titulo), h("small", {}, (i.duracao_s ? dur(i.duracao_s) + " · " : "") + i.custo + (i.resultado ? " · " + i.resultado : ""))] : i.titulo))
      : h("div", { class: "vazio" }, "Nada registrado neste dia."));
}
const GATE = { falhou: ["Gate falhou", "#ff5d5d"], pendente: ["Rodando", "#f5c542"], ok: ["Gate ok", "#5fd39a"], "sem checks": ["Sem checks", "#a9aaa5"], "fora da vez": ["Fora da vez", "#a9aaa5"] };
function telaSeller() {
  const d = cacheST; if (!d) return h("div", { class: "vazio" }, "Carregando…");
  return h("div", { class: "grade" },
    d.erro ? h("div", { class: "c12" }, h("div", { class: "alerta" }, "Não consegui ler o GitHub: " + d.erro)) : null,
    h("section", { class: "card c7" }, h("h2", {}, "Fila de PRs", h("em", {}, d.prs.length + " abertos")),
      d.prs.length ? d.prs.map((p) => h("div", { class: "ag" }, h("i", { class: "pt", style: "background:" + GATE[p.gate][1] }),
        h("div", {}, h("b", {}, "#" + p.numero + " " + p.titulo), h("small", {}, p.ramo + (p.falhos.length ? " · falhou: " + p.falhos.join(", ") : ""))),
        h("span", { class: "est", style: "color:" + GATE[p.gate][1] + ";border-color:" + GATE[p.gate][1] }, GATE[p.gate][0]))) : h("div", { class: "vazio" }, "Nenhum PR aberto nos ramos qa/ fix/ jarvis/ t4xx.")),
    h("section", { class: "card c5" }, h("h2", {}, "Últimos deploys / gates"),
      d.deploys.length ? d.deploys.map((r) => h("div", { class: "pj" }, h("div", {}, h("b", {}, r.workflow), h("small", { style: "display:block;color:var(--mudo)" }, r.titulo.slice(0, 60))),
        h("span", { class: "tag " + (r.resultado === "failure" ? "n3" : r.resultado === "success" ? "n1" : "n0") }, r.resultado))) : h("div", { class: "vazio" }, "Sem execuções.")),
    h("section", { class: "card c12" }, h("h2", {}, "Fila única (últimas linhas)"), d.fila_unica.length ? h("pre", { class: "pre" }, d.fila_unica.join("\n")) : h("div", { class: "vazio" }, "fila-unica.out não encontrado.")));
}

function telaMelhorias() {
  const l = cacheML || [];
  const agir = (m, ac) => h("button", { class: ac === "recusar" ? "sec" : "", onclick: async () => { try { await api("/api/melhorias/" + m.id, { acao: ac }); await carregar(); } catch (e) { alert(e.message); } } }, { aprovar: "Aprovar", recusar: "Recusar", feita: "Marcar feita" }[ac]);
  const campo = (rot, v) => v ? h("p", { class: "ef" }, h("strong", {}, rot + ": "), v) : null;
  return h("section", { class: "card" }, h("h2", {}, "Melhorias sugeridas", h("em", {}, l.filter((m) => m.estado === "sugerida").length + " para decidir")),
    h("div", { class: "rec" }, h("button", { class: "btn", onclick: async () => { const r = await api("/api/melhorias", { acao: "analisar", periodo: "dia" }); alert(r.novas + " sugestão(ões) nova(s)."); await carregar(); } }, "Analisar agora (dia)"),
      h("button", { class: "btn sec", onclick: async () => { const r = await api("/api/melhorias", { acao: "analisar", periodo: "semana" }); alert(r.novas + " sugestão(ões) nova(s)."); await carregar(); } }, "Analisar a semana")),
    h("p", { class: "vazio" }, "Rodada automática todo dia às 22:00. Risco baixo e reversível é aplicado sozinho (vira tarefa e avisa o orquestrador); dinheiro, produção, permissões, segredos, apagar ou desligar skill esperam o seu OK."),
    l.filter((m) => m.estado === "aplicada").slice(0, 3).map((m) => h("div", { class: "resp", style: "margin-bottom:8px" }, h("b", {}, "Aplicada sozinha: "), m.problema + " → tarefa #" + m.tarefa_id)),
    l.length ? l.map((m) => h("div", { class: "skill" + (m.estado === "sugerida" ? "" : " off") },
      h("div", {}, h("b", {}, m.problema), h("span", { class: "tag " + (m.estado === "sugerida" ? "" : m.estado === "aplicada" ? "n1" : "n0") }, m.estado + " · risco " + m.risco), campo("Evidência", m.evidencia), campo("Proposta", m.proposta),
        h("p", { class: "ef" }, h("strong", {}, "Ganho: "), m.ganho + " · ", h("strong", {}, "Custo: "), m.custo + " · ", h("strong", {}, "Executor: "), m.executor + " · ", h("strong", {}, "Risco: "), m.risco),
        m.tarefa_id ? h("p", { class: "ef" }, "→ tarefa #" + m.tarefa_id) : null),
      h("div", { class: "bt", style: "flex-direction:column" }, m.estado === "sugerida" ? [agir(m, "aprovar"), agir(m, "recusar")] : m.estado === "aprovada" || m.estado === "aplicada" ? [agir(m, "feita"), agir(m, "recusar")] : null)))
      : h("div", { class: "vazio" }, "Nenhuma sugestão ainda. Clique em “Analisar agora”."));
}

const brl = (v) => "R$ " + (v || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function barrasDia(dias) {
  const max = Math.max(1, ...dias.map((d) => d.total));
  return h("div", { class: "barras" }, dias.map((d) => h("div", { class: "bcol", title: d.dia + " · " + brl(d.total) }, h("div", { class: "b", style: "height:" + Math.max(2, (d.total / max) * 100) + "%" }), h("small", {}, d.dia.slice(8)))));
}
function telaCustos() {
  const d = cacheCU; if (!d) return h("div", { class: "vazio" }, "Calculando custos… (a primeira leitura dos transcripts pode levar alguns segundos)");
  const sel = (op, atual, fn) => h("select", { onchange: (e) => fn(e.target.value), style: "width:auto" }, op.map(([v, n]) => h("option", { value: v, selected: v === atual }, n)));
  const q = "?periodo=" + periodoCU + "&por=" + porCU;
  const pv = d.previsao;
  return h("div", { class: "grade" },
    d.alertas.length ? h("div", { class: "c12" }, d.alertas.map((a) => h("div", { class: "alerta" }, a))) : null,
    h("section", { class: "card c12" }, h("h2", {}, "Custos · ESTIMATIVA", h("em", {}, "preços de " + (d.precos_vigencia || "?") + " · planos de " + (d.planos_vigencia || "?"))),
      h("div", { class: "rec" }, sel([["dia", "Hoje"], ["semana", "7 dias"], ["mes", "30 dias"]], periodoCU, (v) => { periodoCU = v; cacheCU = null; carregar(); }),
        sel([["modelo", "por modelo"], ["projeto", "por projeto"], ["tarefa", "por tarefa"], ["agente", "por agente"]], porCU, (v) => { porCU = v; cacheCU = null; carregar(); }),
        h("a", { class: "btn", href: "/api/custos.csv" + q, download: "custos.csv", style: "text-decoration:none" }, "CSV"),
        h("a", { class: "btn", href: "/api/custos.md" + q, download: "custos.md", style: "text-decoration:none" }, "Markdown")),
      h("div", { class: "kpis" },
        h("div", { class: "kpi" }, h("b", {}, brl(d.total_brl)), h("span", {}, "total equivalente a preço de API")),
        h("div", { class: "kpi" }, h("b", {}, brl(d.partes.claude)), h("span", {}, "Claude: equivalente a preço de API (o plano do dono não é cobrado assim)")),
        h("div", { class: "kpi" }, h("b", {}, brl(d.partes.codex)), h("span", {}, d.codex.cota_pct + "% da cota do ciclo atual · " + d.codex.pontos_periodo + " pts no período")),
        h("div", { class: "kpi" }, h("b", {}, brl(d.partes.maquina + d.partes.ci)), h("span", {}, d.maquina.kwh + " kWh (máquina + CI)")),
        h("div", { class: "kpi" }, h("b", { style: "color:var(--ok)" }, brl(d.economia_vs_orquestrador)), h("span", {}, "economia vs. tudo no orquestrador")),
        h("div", { class: "kpi" }, h("b", {}, d.custo_por_pr != null ? brl(d.custo_por_pr) : "—"), h("span", {}, "por PR publicado")))),
    h("section", { class: "card c7" }, h("h2", {}, "Por dia"), barrasDia(d.por_dia)),
    h("section", { class: "card c5" }, h("h2", {}, "Janelas das IAs (informadas por elas)"),
      h("p", { class: "ef" }, h("strong", {}, "Claude: "), "R$ " + pv.claude.gasto_mes_equivalente_api.toLocaleString("pt-BR", { minimumFractionDigits: 2 }) + " no mês, equivalente a preço de API (não é limite nem cobrança)."),
      pv.janelas.length ? pv.janelas.map((j) => h("p", { class: "ef", style: j.estoura ? "color:#ffb4b4" : "" }, h("strong", {}, j.nome + ": "), (j.origem === "estimado" ? "≈ " + j.usado_pct + "% estimado (confirmado " + j.confirmado_pct + "%)" : j.usado_pct + "% " + (j.origem === "confirmado" ? "confirmado" : j.origem)) + " · " + j.disponivel_pct + "% disponível · ritmo " + j.ritmo_atual + " " + j.ritmo_unidade + " (permitido " + (j.ritmo_permitido != null ? j.ritmo_permitido : "—") + ") → " + (j.previsao_fim_pct != null ? j.previsao_fim_pct + "%" : "—") + " ao renovar" + (j.estoura ? " ⚠" : "") + (j.leitura_velha && j.origem === "confirmado" ? " · lido há " + idadeTxt(j.idade_leitura_s) : "")))
        : h("p", { class: "vazio" }, "Nenhuma janela informada ainda.")),
    h("section", { class: "card c7" }, h("h2", {}, "Por " + d.por),
      h("div", { class: "tok", style: "grid-template-columns:1fr auto auto auto auto" }, ["", "R$", "entrada", "saída", "cache leit./esc."].map((x) => h("span", {}, x)),
        d.agrupado.slice(0, 20).map((a) => [h("b", { style: "text-align:left;overflow-wrap:anywhere" }, a.chave), h("b", {}, brl(a.brl)), h("b", {}, mi(a.tin)), h("b", {}, mi(a.tout)), h("b", {}, mi(a.cache_leitura) + " / " + mi(a.cache_escrita))]))),
    h("section", { class: "card c5" }, h("h2", {}, "10 tarefas mais caras"),
      d.top_tarefas.length ? d.top_tarefas.map((t) => h("div", { class: "pj" }, h("span", {}, t.chave), h("b", {}, brl(t.brl)))) : h("div", { class: "vazio" }, "Nenhuma tarefa com custo atribuído ainda (use o hook de pulso: docs/HOOKS.md).")),
    h("section", { class: "card c12" }, h("h2", {}, "Orquestrador à parte (por dia e por PR)"),
      d.orquestrador.length ? d.orquestrador.map((o) => h("div", { class: "pj" }, h("span", {}, o.dia), h("span", { class: "tempo" }, (o.prs != null ? o.prs + " PR(s)" : "PRs: sem dado") + (o.brl_por_pr != null ? " · " + brl(o.brl_por_pr) + "/PR" : "")), h("b", {}, brl(o.brl)))) : h("div", { class: "vazio" }, "Sem uso de modelo de nível 3 no período.")),
    d.sem_preco.length ? h("div", { class: "c12" }, h("div", { class: "alerta" }, "Sem preço na tabela para: " + d.sem_preco.join(", ") + " — acrescente em config/precos.json.")) : null);
}
const TEND = (s) => { const v = s.filter((x) => x != null); return v.length < 2 ? "→" : v[v.length - 1] > v[v.length - 2] ? "↗" : v[v.length - 1] < v[v.length - 2] ? "↘" : "→"; };
function miniSerie(c) {
  const v = c.serie, max = Math.max(1, ...v.map((x) => x || 0));
  return h("div", { class: "barras mini" }, v.map((x, i) => h("div", { class: "bcol", title: (x == null ? "sem dado" : x + " " + c.unidade) }, h("div", { class: "b" + (i === v.length - 1 ? " atual" : ""), style: "height:" + (x == null ? 2 : Math.max(4, (x / max) * 100)) + "%" }))));
}
function telaMetricas() {
  const d = cacheMT; if (!d) return h("div", { class: "vazio" }, "Carregando…");
  return h("div", {}, h("p", { class: "vazio" }, "O que medir para melhorar sem fim — 4 semanas, a mais recente à direita. Metas da skill orquestrar (economia-e-qualidade.md)."),
    h("div", { class: "avgrade" }, d.cartoes.map((c) => h("div", { class: "av" + (c.estado === "ruim" ? " mal" : "") },
      h("div", { class: "av-topo" }, h("b", {}, c.titulo), h("span", { class: "est", style: "color:" + (c.estado === "ruim" ? "#ff5d5d" : c.estado === "ok" ? "#5fd39a" : "#a9aaa5") + ";border-color:currentColor" }, c.estado)),
      h("div", { class: "av-nums", style: "grid-template-columns:1fr 1fr" }, h("div", {}, h("b", { style: "font-size:28px" }, c.atual == null ? "—" : c.atual), h("span", {}, c.unidade + " · " + TEND(c.serie))), h("div", {}, h("b", {}, c.meta), h("span", {}, "meta"))),
      miniSerie(c), h("p", { class: "motivo" }, c.estado === "ruim" ? "Se piorar: " + c.dica : c.dica)))),
    h("section", { class: "card", style: "margin-top:14px" }, h("h2", {}, "Retrabalho por nível"), h("div", { class: "kpis" }, [1, 2, 3].map((n) => h("div", { class: "kpi" }, h("b", {}, d.retrabalho_por_nivel[n] == null ? "—" : d.retrabalho_por_nivel[n] + "%"), h("span", {}, "nível " + n + " (meta ≤ 15%)"))))));
}

// ---------- ciclo
const TELAS = { custos: telaCustos, metricas: telaMetricas, melhorias: telaMelhorias, dia: telaDia, seller: telaSeller, visao: telaVisao, aovivo: telaAoVivo, tarefas: telaTarefas, conversa: telaConversa, projetos: telaProjetos, skills: telaSkills };
// Redesenho incremental: compara a árvore nova com a viva e só toca no que mudou (nada mudou → zero mutações no DOM).
// Mantém rolagem, foco, texto digitado e animações já tocadas; troca por completo só ao mudar de aba.
function mesmoTipo(a, b) { return a.nodeType === b.nodeType && (a.nodeType !== 1 || (a.tagName === b.tagName && a.namespaceURI === b.namespaceURI)); }
function patchFilhos(o, n) {
  const nc = Array.from(n.childNodes);
  for (let i = 0; i < nc.length; i++) {
    const oc = o.childNodes[i];
    if (!oc) o.appendChild(nc[i]);
    else if (mesmoTipo(oc, nc[i])) patch(oc, nc[i]);
    else o.replaceChild(nc[i], oc);
  }
  while (o.childNodes.length > nc.length) o.removeChild(o.lastChild);
}
function patch(o, n) {
  if (o.nodeType !== 1) { if (o.data !== n.data) o.data = n.data; return; }
  const vivo = /^(INPUT|SELECT|TEXTAREA)$/.test(o.tagName);   // o que o usuário digitou/escolheu não é sobrescrito
  for (const a of Array.from(o.attributes)) if (!n.hasAttribute(a.name) && !(vivo && a.name === "value")) o.removeAttribute(a.name);
  for (const a of Array.from(n.attributes)) if (!(vivo && a.name === "value") && o.getAttribute(a.name) !== a.value) o.setAttribute(a.name, a.value);
  o._h = n._h; for (const t in (n._h || {})) liga(o, t);
  if (o.tagName === "SELECT") return;
  patchFilhos(o, n);
}
function texto(el, t) { if (el.textContent !== t) el.textContent = t; }
function desenhar() {
  if (!V) return;
  const cx = V.cotas.find((c) => c.agente === "codex");
  texto($("#sub"), V.vivos + " agentes ativos" + (cx ? " · Codex " + Math.round(cx.usado_pct) + "%" : ""));
  const p = $("#pulso"); p.classList.remove("off"); texto($("span", p), "coletado " + new Date(V.coletado_em * 1000).toLocaleTimeString("pt-BR"));
  const main = $("#main"), novo = document.createElement("div");
  novo.append(TELAS[aba]());
  if (abaDesenhada !== aba) { main.replaceChildren(...novo.childNodes); abaDesenhada = aba; }
  else patchFilhos(main, novo);
}
async function carregar() {
  try {
    V = await api("/api/visao");
    if (aba === "aovivo") cacheAV = await api("/api/ao-vivo");
    if (aba === "custos") cacheCU = await api("/api/custos?periodo=" + periodoCU + "&por=" + porCU);
    if (aba === "metricas") cacheMT = await api("/api/metricas");
    if (aba === "melhorias") cacheML = await api("/api/melhorias");
    if (aba === "dia") cacheDia = await api("/api/linha-do-tempo");
    if (aba === "seller") cacheST = await api("/api/seller-turbo");
    if (aba === "tarefas") cacheTar = await api("/api/tarefas");
    if (aba === "conversa") cacheMsg = await api("/api/mensagens?limite=100");
    if (aba === "projetos") cachePrj = await api("/api/projetos");
    if (aba === "skills") cacheSkills = await api("/api/skills");
    desenhar();
  } catch (e) { console.error("falha ao atualizar o painel:", e); $("#pulso").classList.add("off"); $("#pulso span").textContent = "sem conexão com o servidor"; }
}
$("#abas").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  aba = b.dataset.aba; history.replaceState(null, "", "#" + aba); document.querySelectorAll("#abas button").forEach((x) => x.classList.toggle("on", x === b)); carregar();
});
{ const h0 = (location.hash || "").slice(1); if (TELAS[h0]) { aba = h0; document.querySelectorAll("#abas button").forEach((x) => x.classList.toggle("on", x.dataset.aba === h0)); } }
carregar();
setInterval(() => { if (!digitando && $("#modal").hidden && aba !== "aovivo") carregar(); }, 5000);
setInterval(() => { if (aba === "aovivo" && $("#modal").hidden) carregar(); }, 3000);
