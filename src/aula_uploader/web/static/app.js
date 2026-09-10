const $ = (id) => document.getElementById(id);

const state = {
  portals: [],
  portal: "fullcycle",
  session: {},
  cursos: [],
  cursosAll: [],
  capitulos: [],
  catalogById: {},
  produtos: [],
  produtoFiltro: "",
  aulas: [],
  planoByFile: {},
  busyCurso: null,
  busyCap: null,
  creds: {},
  view: null,
  busyLogin: false,
  layout: "new",
  story: "portal",
  xfer: null,
  editsByFile: {},
  platform: { os: "mac", folder_picker: true },
  projectsFilter: "andamento",
  jobOpeningId: "",
};

function aulaTitulo(aula) {
  const edit = state.editsByFile[aula.arquivo];
  return edit && edit.titulo !== undefined ? edit.titulo : aula.titulo;
}

function aulaOrdem(aula) {
  const edit = state.editsByFile[aula.arquivo];
  return edit && edit.ordem !== undefined ? edit.ordem : aula.ordem;
}

function captureEdit(arquivo, field, value) {
  if (!arquivo) return;
  state.editsByFile[arquivo] = state.editsByFile[arquivo] || {};
  state.editsByFile[arquivo][field] = value;
}

function migrateEditKey(oldName, newName) {
  if (!oldName || !newName || oldName === newName) return;
  if (!state.editsByFile[oldName]) return;
  state.editsByFile[newName] = {
    ...state.editsByFile[oldName],
    ...state.editsByFile[newName],
  };
  delete state.editsByFile[oldName];
}

function mergeServerAulas(incoming) {
  return (incoming || []).map((aula) => {
    const edit = state.editsByFile[aula.arquivo];
    if (!edit) return aula;
    return {
      ...aula,
      titulo: edit.titulo !== undefined ? edit.titulo : aula.titulo,
      ordem: edit.ordem !== undefined ? edit.ordem : aula.ordem,
    };
  });
}

let persistEditsTimer = 0;
function persistEditsDebounced() {
  window.clearTimeout(persistEditsTimer);
  persistEditsTimer = window.setTimeout(() => { persistEdits(); }, 350);
}

async function persistEdits() {
  if (!state.aulas.length) return;
  const edits = collectEdits();
  if (!edits.length) return;
  try {
    const data = await api("/api/videos/edits", {
      method: "POST",
      body: JSON.stringify({ aulas: edits }),
    });
    state.aulas = mergeServerAulas(data.aulas || []);
    edits.forEach(({ arquivo, titulo, ordem }) => {
      state.editsByFile[arquivo] = { titulo, ordem };
    });
  } catch {
    /* mantém no estado local; o envio manda de novo */
  }
}

function pathHints(platform) {
  const p = platform || state.platform || {};
  if (p.os === "windows") {
    return {
      dropHint: "Arraste a pasta ou o .zip. No Windows, cole o caminho abaixo (ex.: C:\\Users\\…\\aulas).",
      dropHintList: "O que você soltar agora entra na lista. No Windows, também dá para colar o caminho da pasta.",
      pathLabel: "Caminho da pasta",
      placeholder: "C:\\Users\\…\\aulas",
      folderDetail: "Lendo a pasta informada…",
    };
  }
  if (p.os === "linux") {
    return {
      dropHint: "Arraste a pasta ou o .zip. Ou cole o caminho absoluto abaixo (ex.: /home/…/aulas).",
      dropHintList: "O que você soltar agora entra na lista. Também dá para colar o caminho da pasta.",
      pathLabel: "Caminho da pasta",
      placeholder: "/home/…/aulas",
      folderDetail: "Lendo a pasta informada…",
    };
  }
  return {
    dropHint: "Escolher pasta abre o seletor nativo, sem o aviso do Chrome. Ou cole o caminho (mais rápido em pastas enormes).",
    dropHintList: "O que você soltar agora entra na lista. Não apaga o que já está aqui.",
    pathLabel: "Caminho da pasta",
    placeholder: "/Users/…/aulas",
    folderDetail: "Escolha no seletor. Em pastas grandes isso pode levar um pouco.",
  };
}

function applyPlatformUi() {
  const hints = pathHints(state.platform);
  if ($("path-lab")) $("path-lab").textContent = hints.pathLabel;
  if ($("local-path")) $("local-path").placeholder = hints.placeholder;
  const showPicker = Boolean(state.platform && state.platform.folder_picker);
  ["btn-folder", "btn-add-folder"].forEach((id) => {
    const btn = $(id);
    if (btn) btn.classList.toggle("hidden", !showPicker);
  });
  renderAulas();
}

function fold(text) {
  return String(text || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function matches(text, query) {
  const q = fold(query).trim();
  if (!q) return true;
  const hay = fold(text);
  return q.split(/\s+/).every((part) => hay.includes(part));
}

function stripTokenFromUrl() {
  const url = new URL(window.location.href);
  if (url.searchParams.has("k")) {
    url.searchParams.delete("k");
    history.replaceState({}, "", url.pathname + url.search);
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...(options.headers || {}),
    },
  });
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const detail = data.detail || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function renderSelecao() {
  const s = state.session || {};
  const here = isAuthed(state.portal) && s.portal === state.portal;
  const chosen = state.portals.find((p) => p.key === state.portal);
  const portalNome = (chosen && chosen.label) || s.portal_label || "";
  const portalOn = Boolean(here);
  $("card-portal").classList.toggle("off", !portalOn);
  $("sel-portal").className = "valor" + (portalOn ? "" : " empty");
  $("sel-portal").textContent = portalNome || "Nenhum ainda";
  $("sel-portal-sub").innerHTML = portalOn && s.portal_url
    ? `<a href="${s.portal_url}" target="_blank" rel="noopener">abrir o portal</a>`
    : "Passe o mouse para trocar";
  renderPortalMenu();

  const product = state.produtos.find((p) => p.id === state.produtoFiltro);
  const produtoOn = Boolean(product);
  $("card-produto").classList.toggle("off", !produtoOn);
  $("sel-produto").className = "valor" + (produtoOn ? "" : " empty");
  $("sel-produto").textContent = produtoOn ? (product.nome_curto || product.nome) : "Nenhum ainda";
  $("sel-produto-sub").textContent = produtoOn ? product.nome : "Passe o mouse para escolher";

  const cursoOn = Boolean(here && s.curso_id);
  $("card-curso").classList.toggle("off", !cursoOn);
  const cursoEl = $("sel-curso");
  if (cursoOn) {
    cursoEl.className = "valor";
    cursoEl.textContent = s.curso_nome || `Curso ${s.curso_id}`;
    $("sel-curso-sub").innerHTML = `ID ${s.curso_id}` +
      (s.curso_url ? ` · <a href="${s.curso_url}" target="_blank" rel="noopener">abrir no admin</a>` : "") +
      (state.busyCurso ? " · carregando capítulos…" : "");
  } else {
    cursoEl.className = "valor empty";
    cursoEl.textContent = "Nenhum ainda";
    $("sel-curso-sub").textContent = "Passe o mouse para buscar";
  }

  const cap = here ? s.capitulo : null;
  const capOn = Boolean(cap);
  $("card-cap").classList.toggle("off", !capOn);
  const capEl = $("sel-cap");
  if (capOn) {
    capEl.className = "valor" + (state.busyCap ? " loading" : "");
    capEl.textContent = cap.nome || `Capítulo ${cap.id}`;
    $("sel-cap-sub").innerHTML = `ID ${cap.id}` +
      (cap.url ? ` · <a href="${cap.url}" target="_blank" rel="noopener">abrir no admin</a>` : "") +
      (state.busyCap ? " · confirmando no portal…" : "");
  } else {
    capEl.className = "valor empty";
    capEl.textContent = "Nenhum ainda";
    $("sel-cap-sub").textContent = "Passe o mouse para criar ou buscar";
  }

  const vidOn = Boolean(capOn && state.aulas.length);
  if ($("card-videos")) {
    $("card-videos").classList.toggle("off", !capOn);
    $("sel-videos").className = "valor" + (vidOn || s.uploading || state.xfer ? "" : " empty");
    $("sel-videos").textContent = s.uploading
      ? "Enviando agora…"
      : (state.xfer
        ? "Carregando…"
        : (vidOn
          ? `${state.aulas.length} arquivo(s)`
          : (capOn ? "Solte a pasta" : "Nenhum ainda")));
    $("sel-videos-sub").textContent = !capOn
      ? "Depois do capítulo"
      : (s.uploading
        ? "Acompanhe o progresso embaixo"
        : (state.xfer
          ? (state.xfer.title || "Carregando…")
          : (state.aulas.length ? "Solte mais arquivos para acrescentar" : "Solte a pasta ou o vídeo aqui")));
  }
  markStories();
}

function progressView() {
  const s = state.session || {};
  const here = isAuthed(state.portal) && s.portal === state.portal;
  if (!isAuthed(state.portal)) return "login";
  if (!here || !s.curso_id) return "curso";
  if (!s.capitulo) return "capitulo";
  return "videos";
}

function canVisit(view) {
  const s = state.session || {};
  const here = isAuthed(state.portal) && s.portal === state.portal;
  if (view === "login") return true;
  if (view === "curso") return isAuthed(state.portal);
  if (view === "capitulo") return here && Boolean(s.curso_id);
  if (view === "videos" || view === "enviar") return here && Boolean(s.capitulo);
  return false;
}

function goView(view) {
  if (!canVisit(view)) return;
  state.view = view === "enviar" ? "videos" : view;
  setStep();
}

function goNext(next) {
  if (next === "capitulo" && !state.session.curso_id) {
    $("curso-status").textContent = "Escolha um curso na lista.";
    return;
  }
  if (next === "videos" && !(state.session.capitulo && state.session.capitulo.id)) {
    $("cap-status").textContent = "Escolha um capítulo.";
    return;
  }
  goView(next);
}

function showView() {
  if (isNew()) {
    document.querySelectorAll(".step-panel").forEach((el) => {
      el.classList.toggle("on", el.dataset.view === "videos" && state.story === "videos");
    });
    return;
  }
  const view = state.view || progressView();
  document.querySelectorAll(".step-panel").forEach((el) => {
    el.classList.toggle("on", el.dataset.view === view);
  });
}

function setStep() {
  const s = state.session || {};
  const here = isAuthed(state.portal) && s.portal === state.portal;
  const done = {
    login: isAuthed(state.portal),
    curso: here && Boolean(s.curso_id),
    capitulo: here && Boolean(s.capitulo),
    videos: state.aulas.length > 0,
    enviar: ["done", "done_with_errors"].includes((s.job || {}).status),
  };
  const view = state.view || progressView();
  document.querySelectorAll("#steps li").forEach((li) => {
    const key = li.dataset.step;
    const on = key === view || (view === "videos" && key === "enviar");
    li.classList.toggle("done", Boolean(done[key]));
    li.classList.toggle("on", on && !done[key]);
    li.classList.toggle("locked", !canVisit(key));
  });
  // O portal recusa arquivos acima do teto, então o envio fica travado até comprimir.
  const bloqueados = state.aulas.filter((a) => a.acima_do_teto).length;
  const busyUp = fileUploadBusy();
  $("btn-send").disabled = !(
    here && s.capitulo && state.aulas.length && !busyUp && !state.xfer
    && !s.converting && !bloqueados
  );
  $("btn-send").textContent = busyUp
    ? "Enviando…"
    : (bloqueados ? "Comprima os vídeos grandes" : "Enviar aulas");
  $("btn-send").classList.toggle("busy", busyUp);
  const nextCurso = $("btn-next-curso");
  const nextCap = $("btn-next-capitulo");
  const nextVid = $("btn-next-videos");
  if (nextCurso) {
    nextCurso.disabled = Boolean(state.busyLogin);
    nextCurso.textContent = isAuthed(state.portal) ? "Continuar" : "Entrar";
  }
  if (nextCap) nextCap.disabled = !(here && s.curso_id);
  if (nextVid) nextVid.disabled = !(here && s.capitulo);
  showView();
  renderSelecao();
}

function isAuthed(key) {
  return (state.session.autenticados || []).includes(key);
}

function isNew() {
  return true;
}

function canStory(name) {
  const s = state.session || {};
  const here = isAuthed(state.portal) && s.portal === state.portal;
  if (name === "portal") return true;
  if (name === "produto") return isAuthed(state.portal);
  if (name === "curso") return isAuthed(state.portal) && Boolean(state.produtoFiltro);
  if (name === "capitulo") return here && Boolean(s.curso_id);
  if (name === "videos") return here && Boolean(s.capitulo);
  return false;
}

function currentStory() {
  if (!isAuthed(state.portal)) return "portal";
  if (!state.produtoFiltro) return "produto";
  if (!state.session.curso_id) return "curso";
  if (!state.session.capitulo) return "capitulo";
  return "videos";
}

function markStories() {
  const order = ["portal", "produto", "curso", "capitulo", "videos"];
  const now = isNew() ? (state.story || currentStory()) : "";
  const doneUntil = order.indexOf(currentStory());
  order.forEach((name, idx) => {
    const card = document.querySelector(`[data-story="${name}"]`);
    if (!card) return;
    const done = idx < doneUntil || (name === "videos" && state.aulas.length);
    card.classList.toggle("now", isNew() && name === now);
    card.classList.toggle("done", isNew() && done && name !== now);
    card.classList.toggle("locked", isNew() && !canStory(name));
  });
}

function goStory(name, { open = true } = {}) {
  if (!canStory(name) && name !== "portal") {
    name = currentStory();
  }
  state.story = name;
  const viewMap = {
    portal: "login",
    produto: "curso",
    curso: "curso",
    capitulo: "capitulo",
    videos: "videos",
  };
  if (canVisit(viewMap[name])) state.view = viewMap[name];
  document.querySelectorAll("#selecao > div.open").forEach((el) => el.classList.remove("open"));
  setStep();
  if (name === "videos") {
    const midia = $("midia");
    if (midia) setTimeout(() => midia.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    return;
  }
  if (open) openCard(STORY_CARD[name], { instant: true, focus: true });
}

const STORY_CARD = {
  portal: "card-portal",
  produto: "card-produto",
  curso: "card-curso",
  capitulo: "card-cap",
  videos: "card-videos",
};

function placeLayoutDom() {
  const loginHome = $("box-login");
  const loginFly = $("fly-portal-body");
  if (loginHome && loginFly) {
    ["login-hint", "login-ok"].forEach((id) => {
      if ($(id)) loginHome.appendChild($(id));
    });
    ["login-status", "login-form"].forEach((id) => {
      if ($(id)) loginFly.appendChild($(id));
    });
  }
  const prod = $("produto-picks");
  if (prod && $("fly-produto-body")) $("fly-produto-body").appendChild(prod);
  const cursoBits = ["curso-q", "curso-status", "curso-list"];
  const cursoDest = $("fly-curso-body");
  if (cursoDest) cursoBits.forEach((id) => { if ($(id)) cursoDest.appendChild($(id)); });
  const tabs = $("cap-tabs");
  const capDest = $("fly-cap-body");
  if (capDest && tabs) capDest.appendChild(tabs);
  if (capDest && $("tab-existente")) capDest.appendChild($("tab-existente"));
  if (capDest && $("tab-novo")) capDest.appendChild($("tab-novo"));
}

const hoverCtl = { open: 0, close: 0, card: null };

function openCard(id, { instant = false, focus = false } = {}) {
  const card = $(id);
  if (!card) return;
  if (!isNew() && id !== "card-portal") return;
  if (isNew() && card.dataset.story && !canStory(card.dataset.story)) return;
  window.clearTimeout(hoverCtl.open);
  window.clearTimeout(hoverCtl.close);
  document.querySelectorAll("#selecao > div.open").forEach((el) => {
    if (el !== card) el.classList.remove("open");
  });
  const go = () => {
    card.classList.add("open");
    hoverCtl.card = card;
    if (focus && id === "card-curso") {
      const q = $("curso-q");
      if (q) setTimeout(() => q.focus(), 80);
    }
  };
  if (instant) go();
  else hoverCtl.open = window.setTimeout(go, 160);
}

function closeCardSoon(card) {
  window.clearTimeout(hoverCtl.open);
  hoverCtl.close = window.setTimeout(() => {
    if (hoverCtl.card === card) card.classList.remove("open");
  }, 720);
}

function bindStoryHover() {
  document.querySelectorAll("#selecao > div").forEach((card) => {
    card.addEventListener("mouseenter", () => openCard(card.id));
    card.addEventListener("mouseleave", () => closeCardSoon(card));
  });
}

function rememberCreds() {
  if (!state.portal) return;
  state.creds[state.portal] = {
    user: $("user").value,
    pass: $("pass").value,
  };
  try { sessionStorage.setItem(`aula-user-${state.portal}`, $("user").value); } catch {}
}

function restoreCreds(key) {
  const mem = state.creds[key] || {};
  let stored = "";
  try { stored = sessionStorage.getItem(`aula-user-${key}`) || ""; } catch {}
  $("user").value = mem.user || stored;
  $("pass").value = mem.pass || "";
}

function renderLogin() {
  const box = $("portal-picks");
  box.innerHTML = "";
  state.portals.forEach((p) => {
    const btn = document.createElement("button");
    btn.type = "button";
    const on = state.portal === p.key;
    const authed = isAuthed(p.key);
    btn.className = "btn ghost portal-btn" + (on ? " on" : "") + (authed ? " authed" : "");
    btn.textContent = p.label + (authed ? " · ok" : "");
    btn.onclick = () => switchPortal(p.key);
    box.appendChild(btn);
  });
  const chosen = state.portals.find((p) => p.key === state.portal);
  const authedHere = isAuthed(state.portal);
  const passInput = $("pass");
  const passLabel = passInput && passInput.closest("label");
  // Mesmo que exista arquivo de sessão salva, ele pode estar expirado/ inválido.
  // Para não te bloquear, só desabilitamos/escondemos a senha quando já estiver autenticado.
  if (passInput) passInput.disabled = authedHere;
  if (passLabel) passLabel.classList.toggle("hidden", authedHere);
  $("login-form").classList.toggle("hidden", authedHere);
  $("login-status").classList.toggle("hidden", isNew() && authedHere);
  $("login-ok").classList.toggle("hidden", isNew() || !authedHere);
  $("login-hint").classList.toggle("hidden", isNew());
  if (chosen && !isNew()) {
    if (authedHere) {
      const outros = (state.session.autenticados || []).filter((k) => k !== state.portal);
      $("login-hint").textContent = outros.length
        ? `Logado em ${chosen.label}. O outro portal continua autenticado.`
        : `Logado em ${chosen.label}.`;
    } else if (chosen.has_session) {
      $("login-hint").textContent =
        `Há sessão salva de ${chosen.label}. Se não logar, digite a senha e tente de novo.`;
    } else {
      $("login-hint").textContent =
        `E-mail e senha do admin de ${chosen.label}. Cada portal tem o seu.`;
    }
  }
  if (authedHere) {
    $("who").textContent = `${state.session.portal_label} · autenticado`;
    const a = $("portal-link");
    a.href = state.session.portal_url;
    a.textContent = "Abrir o portal";
  }
  if ($("persist") && chosen) {
    // Se já tinha sessão salva, mantém o checkbox marcado por padrão.
    if (chosen.has_session && !authedHere) $("persist").checked = true;
  }
}

function renderPortalMenu() {
  const box = $("portal-menu");
  if (!box) return;
  const portals = state.portals.length
    ? state.portals
    : [
        { key: "fullcycle", label: "Full Cycle" },
        { key: "devops", label: "DevOps Pro" },
      ];
  box.innerHTML = `<div class="portal-menu-inner">${portals.map((p) => {
    const on = state.portal === p.key;
    return `<button type="button" role="menuitem" class="${on ? "on" : ""}" data-portal="${escapeAttr(p.key)}">${escapeHtml(p.label)}</button>`;
  }).join("")}</div>`;
  box.querySelectorAll("[data-portal]").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      switchPortal(btn.dataset.portal, { fromCard: true });
    };
  });
}

async function switchPortal(key, { fromCard = false } = {}) {
  rememberCreds();
  if (fromCard && key === state.portal && isAuthed(key)) {
    if (isNew()) goStory("produto");
    return;
  }
  state.portal = key;
  restoreCreds(key);
  if (!isAuthed(key)) {
    state.view = "login";
    renderLogin();
    state.cursosAll = [];
    state.cursos = [];
    state.capitulos = [];
    renderCursos();
    renderCaps();
    setStep();
    if (isNew()) goStory("portal");
    return;
  }
  try {
    const session = await api("/api/portal/select", {
      method: "POST",
      body: JSON.stringify({ portal: key }),
    });
    await afterPortalSession(session, { stay: !fromCard });
    if (isNew() && fromCard) goStory("produto");
  } catch (err) {
    avisar("Deu problema", err.message);
    renderLogin();
  }
}

async function afterPortalSession(session, { stay = false } = {}) {
  state.session = session;
  if (session.portal) state.portal = session.portal;
  state.view = stay ? "login" : null;
  renderLogin();
  if (session.autenticado) {
    await loadCatalog();
    if (session.curso_id && state.catalogById[session.curso_id]) {
      state.capitulos = state.catalogById[session.curso_id];
    } else {
      state.capitulos = [];
    }
  } else {
    state.cursosAll = [];
    state.cursos = [];
    state.catalogById = {};
    state.capitulos = [];
  }
  renderCursos();
  renderCaps();
  setStep();
}

function renderCursos() {
  const ul = $("curso-list");
  ul.innerHTML = "";
  const productsById = Object.fromEntries(state.produtos.map((p) => [p.id, p]));
  state.cursos.forEach((c) => {
    const li = document.createElement("li");
    if (state.session.curso_id === c.id) li.classList.add("on");
    if (state.busyCurso === c.id) li.classList.add("busy");
    const chips = (c.produto_ids || [])
      .map((pid) => productsById[pid])
      .filter((p) => p && (!p.portal || p.portal === state.portal))
      .map((p) => `<span class="chip" data-unassign="${escapeAttr(p.id)}">${escapeHtml(p.nome_curto || p.nome)} <button type="button" aria-label="Desvincular">×</button></span>`)
      .join("");
    li.innerHTML = isNew()
      ? `${escapeHtml(c.nome)}<span class="meta">ID ${c.id}</span>`
      : `
      <div>
        ${escapeHtml(c.nome)}
        <span class="meta">ID ${c.id}${c.fonte === "portal" ? " · portal" : ""}</span>
        ${chips ? `<div class="chips">${chips}</div>` : ""}
      </div>
      <button type="button" class="btn ghost bind">vincular</button>
    `;
    li.onclick = () => selectCurso(c);
    if (!isNew()) {
      li.querySelector(".bind").onclick = (ev) => {
        ev.stopPropagation();
        assignProduto(c);
      };
      li.querySelectorAll("[data-unassign]").forEach((chip) => {
        chip.onclick = (ev) => {
          ev.stopPropagation();
          unassignProduto(c, chip.dataset.unassign);
        };
      });
    }
    ul.appendChild(li);
  });
}

function renderCaps() {
  const ul = $("cap-list");
  ul.innerHTML = "";
  const capId = state.session.capitulo && state.session.capitulo.id;
  const q = ($("cap-q") && $("cap-q").value) || "";
  state.capitulos.filter((c) => matches(`${c.nome} ${c.id}`, q)).forEach((c) => {
    const li = document.createElement("li");
    if (capId === c.id) li.classList.add("on");
    if (state.busyCap === c.id) li.classList.add("busy");
    li.innerHTML = `${escapeHtml(c.nome)}<span class="meta">${c.ordem || "—"} · ID ${c.id}</span>`;
    li.onclick = () => selectCapitulo(c);
    ul.appendChild(li);
  });
  sugerirOrdemCapitulo();
}

function sugerirOrdemCapitulo() {
  const input = $("cap-ordem");
  if (!input || input.dataset.touched) return;
  const maxOrdem = state.capitulos.reduce((m, c) => Math.max(m, Number(c.ordem) || 0), 0);
  input.value = String(maxOrdem + 1);
}

function normArquivo(nome) {
  return String(nome || "").normalize("NFC");
}

function statusRank(status) {
  return {
    ok: 50,
    pulada: 50,
    falhou: 40,
    processando: 30,
    salvando: 20,
    enviando: 10,
    pendente: 1,
  }[status] || 0;
}

function findJobItem(arquivo) {
  const alvo = normArquivo(arquivo);
  // 1) Sempre prefere o job aberto agora (evita "falhou" velho tapar "enviando").
  const atual = ((state.session && state.session.job) || {}).items || [];
  for (const i of atual) {
    if (normArquivo(i.arquivo) === alvo) return i;
  }
  // 2) Senão, o melhor entre os outros — ignora falha/cancelado se existir ok/processando.
  let best = null;
  for (const j of (state.session && state.session.jobs) || []) {
    if (!j || j.id === ((state.session.job || {}).id)) continue;
    for (const i of j.items || []) {
      if (normArquivo(i.arquivo) !== alvo) continue;
      if (!best || statusRank(i.status) > statusRank(best.status)) best = i;
    }
  }
  return best;
}

function renderAulas() {
  const tb = $("aulas");
  const hasList = state.aulas.length > 0;
  const sending = fileUploadBusy();
  const converting = Boolean(state.session.converting);
  $("drop").classList.toggle("has-list", hasList);
  $("drop-title").textContent = hasList
    ? "Solte mais arquivos para acrescentar"
    : "Arraste a pasta ou o .zip e solte aqui";
  $("drop-hint").textContent = hasList
    ? pathHints().dropHintList
    : pathHints().dropHint;
  $("btn-folder").textContent = hasList ? "Adicionar pasta" : "Escolher pasta";
  $("btn-files").textContent = hasList ? "Adicionar arquivos" : "Escolher arquivos";
  $("lista-head").classList.toggle("hidden", !hasList);
  $("fonte").textContent = state.session.fonte ? `Fonte: ${state.session.fonte}` : "";
  if (
    document.activeElement
    && document.activeElement.matches("[data-k=titulo]")
    && !state.session.converting
  ) return;
  tb.innerHTML = "";
  state.aulas.forEach((aula) => {
    const plan = state.planoByFile[aula.arquivo] || {};
    const jobItem = findJobItem(aula.arquivo);
    const convItem = ((state.session.convert || {}).items || []).find(
      (i) => normArquivo(i.arquivo) === normArquivo(aula.arquivo)
    );
    const status = (jobItem && jobItem.status) || "—";
    const formato = (aula.formato || (aula.arquivo.split(".").pop() || "")).toUpperCase();
    const convertido = (state.session.convertidos || {})[aula.arquivo];
    const aulaUrl = aulaLink(jobItem);
    const comprimindo = Boolean(convItem && convItem.status === "convertendo");
    const tr = document.createElement("tr");
    tr.dataset.arquivo = aula.arquivo;
    if (["enviando", "salvando", "processando"].includes(status)) tr.classList.add("sending");
    if (status === "ok") tr.classList.add("enviada");
    if (comprimindo) tr.classList.add("converting-row");
    if (aula.acima_do_teto) tr.classList.add("grande-demais");
    else if (aula.grande) tr.classList.add("grande");
    tr.innerHTML = `
      <td><input type="number" min="1" value="${aulaOrdem(aula)}" data-k="ordem"></td>
      <td class="cell-arquivo" title="${escapeAttr(aula.arquivo)}">${escapeHtml(aula.arquivo)}</td>
      <td class="cell-titulo"><input type="text" value="${escapeAttr(aulaTitulo(aula))}" data-k="titulo"></td>
      <td class="cell-tamanho">${escapeHtml(aula.tamanho_fmt)}${aula.grande ? ` <span class="tag-grande" title="Acima do limite do portal">grande</span>` : ""}</td>
      <td><span class="fmt">${escapeHtml(formato || "—")}</span></td>
      <td class="cell-res">${escapeHtml(aula.resolucao || "—")}</td>
      <td class="cell-acao">${escapeHtml(acaoLabel(plan, jobItem))}</td>
      <td class="cell-status">${statusCellHtml({ status, jobItem, convItem, aulaUrl, convertido })}</td>
      <td class="cell-acoes">
        <div class="cell-acoes-inner">
          ${comprimindo
            ? `<button type="button" class="btn ghost btn-conv-cancel" data-convert-cancel title="Cancelar compressão">Cancelar</button>`
            : (!convertido
              ? `<button type="button" class="btn ghost btn-conv" data-convert="${escapeAttr(aula.arquivo)}" title="Comprime para 1080p com áudio AAC 192 kbps (sem alterar o volume)" ${sending || converting ? "disabled" : ""}>Comprimir</button>
          <button type="button" class="btn ghost btn-remove" data-remove="${escapeAttr(aula.arquivo)}" ${sending ? "disabled" : ""}>Remover</button>`
              : `<button type="button" class="btn ghost btn-ver" data-preview="${escapeAttr(aula.arquivo)}">Ver</button>
          <button type="button" class="btn ghost btn-remove" data-remove="${escapeAttr(aula.arquivo)}" ${sending ? "disabled" : ""}>Remover</button>`)}
        </div>
      </td>
    `;
    tb.appendChild(tr);
  });
  renderGrandeWarn();
}

function acaoLabel(plan, jobItem) {
  if (plan && plan.acao_label) return plan.acao_label;
  return {
    criar: "criar",
    enviar: "enviar",
    pular: "pular",
    forcar: "reenviar",
  }[(jobItem && jobItem.acao) || ""] || "—";
}

function statusCellHtml({ status, jobItem, convItem, aulaUrl, convertido }) {
  if (convItem) {
    if (convItem.status === "convertendo") {
      const pct = Math.max(0, Math.min(100, Math.round(convItem.pct || 0)));
      const eta = convItem.eta_s ? fmtEta(convItem.eta_s) : "";
      return `<div class="conv-inline">
        <div class="conv-inline-top">
          <span class="st convertendo">comprimindo</span>
          <strong class="conv-pct">${pct}%</strong>
          ${eta ? `<span class="conv-eta">faltam ${escapeHtml(eta)}</span>` : ""}
        </div>
        <span class="conv-bar" aria-hidden="true"><i style="width:${pct}%"></i></span>
      </div>`;
    }
    if (convItem.status === "pronto" && !convertido) {
      return `<span class="st pronto">comprimido</span>
        <button type="button" class="aula-link btn-link" data-preview="${escapeAttr(convItem.arquivo)}">ver</button>`;
    }
    if (convItem.status === "aplicado" || (convItem.status === "pronto" && convertido)) {
      return `<span class="st pronto">comprimido</span>`;
    }
    if (convItem.status === "falhou") {
      return `<span class="st falhou" title="${escapeAttr(convItem.erro || "")}">falhou</span>`;
    }
    if (convItem.status === "pendente") {
      return `<span class="st pendente">na fila</span>`;
    }
  }
  if (status === "enviando") {
    const pct = Math.max(0, Math.min(100, Math.round((jobItem && jobItem.pct) || 0)));
    return `<div class="conv-inline up-inline">
      <div class="conv-inline-top">
        <span class="st enviando">enviando</span>
        <strong class="conv-pct">${pct}%</strong>
      </div>
      <span class="conv-bar" aria-hidden="true"><i style="width:${pct}%"></i></span>
    </div>`;
  }
  if (status === "salvando") {
    return `<div class="conv-inline up-inline">
      <div class="conv-inline-top">
        <span class="st salvando">salvando no portal</span>
      </div>
      <span class="conv-eta">amarrando a URL no conteúdo</span>
      <span class="conv-bar indet" aria-hidden="true"><i></i></span>
    </div>`;
  }
  if (status === "processando") {
    return `<div class="conv-inline up-inline">
      <div class="conv-inline-top">
        <span class="st processando">no Nivo</span>
      </div>
      <span class="conv-eta">aguardando o link da aula</span>
      <span class="conv-bar indet" aria-hidden="true"><i></i></span>
    </div>`;
  }
  const label = escapeHtml(statusLabel(status, jobItem));
  const st = `<span class="st ${cssStatus(status)}">${label}</span>`;
  const link = aulaUrl
    ? `<a class="aula-link" href="${escapeAttr(aulaUrl)}" target="_blank" rel="noopener">abrir</a>`
    : "";
  return `${st}${link}`;
}

function aulaLink(jobItem) {
  if (!jobItem) return "";
  if (jobItem.url) return jobItem.url;
  const cid = jobItem.conteudo_id;
  const base = (state.session && state.session.portal_url) || "";
  if (!cid || !base) return "";
  return `${String(base).replace(/\/$/, "")}/admin/curso/conteudo/${cid}/edit`;
}

function aulasGrandes() {
  const convertidos = state.session.convertidos || {};
  return state.aulas.filter((a) => a.grande && !convertidos[a.arquivo]);
}

function renderGrandeWarn() {
  const box = $("grande-warn");
  if (!box) return;
  const grandes = aulasGrandes();
  const limite = (state.session.limites || {}).alvo_fmt || "1.00 GB";
  box.classList.toggle("hidden", grandes.length === 0 || Boolean(state.session.converting));
  if (!grandes.length) return;
  const bloqueiam = grandes.filter((a) => a.acima_do_teto).length;
  $("grande-warn-title").textContent = grandes.length === 1
    ? `1 vídeo passa de ${limite}`
    : `${grandes.length} vídeos passam de ${limite}`;
  $("grande-warn-sub").textContent = bloqueiam
    ? "O portal recusa arquivos desse tamanho. Comprima para 1080p antes de enviar."
    : "Dá para enviar, mas o ideal é comprimir para 1080p e ficar perto de 1 GB.";
  $("btn-convert-all").textContent = grandes.length === 1
    ? "Comprimir agora"
    : `Comprimir os ${grandes.length}`;
}

function openTitulo(input) {
  if (input.classList.contains("titulo-open")) return;
  const td = input.closest("td");
  const rect = input.getBoundingClientRect();
  if (td) td.style.height = `${td.offsetHeight}px`;
  input.classList.add("titulo-open");
  input.style.position = "fixed";
  input.style.left = `${rect.left}px`;
  input.style.top = `${rect.top}px`;
  input.style.width = `${rect.width}px`;
  input.style.height = `${rect.height}px`;
  input.style.zIndex = "50";
  const grow = Math.min(320, Math.max(140, window.innerWidth - rect.left - 40));
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      input.style.width = `${rect.width + grow}px`;
      input.style.height = `${Math.max(rect.height + 16, 46)}px`;
      input.style.top = `${Math.max(12, rect.top - 8)}px`;
    });
  });
}

function closeTitulo(input) {
  if (!input.classList.contains("titulo-open") || input.dataset.closing === "1") return;
  input.dataset.closing = "1";
  const td = input.closest("td");
  const dest = td ? td.getBoundingClientRect() : input.getBoundingClientRect();
  input.style.width = `${Math.max(80, dest.width - 24)}px`;
  input.style.height = `${Math.max(32, dest.height - 18)}px`;
  input.style.top = `${dest.top + 9}px`;
  input.style.left = `${dest.left + 12}px`;
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    input.removeEventListener("transitionend", finish);
    input.classList.remove("titulo-open");
    delete input.dataset.closing;
    input.style.position = "";
    input.style.left = "";
    input.style.top = "";
    input.style.width = "";
    input.style.height = "";
    input.style.zIndex = "";
    if (td) td.style.height = "";
  };
  input.addEventListener("transitionend", finish);
  window.setTimeout(finish, 450);
}

const COL_DEFAULTS = {
  ordem: 72,
  arquivo: 200,
  formato: 84,
  titulo: 340,
  tamanho: 108,
  resolucao: 104,
  acao: 118,
  status: 92,
  remove: 190,
};

function applyColWidths() {
  let saved = {};
  try {
    saved = JSON.parse(sessionStorage.getItem("aula-col-widths") || "{}");
  } catch {
    saved = {};
  }
  document.querySelectorAll("#aulas-table col[data-col]").forEach((col) => {
    const key = col.dataset.col;
    const width = Number(saved[key] || COL_DEFAULTS[key]);
    if (width) col.style.width = `${width}px`;
  });
}

function saveColWidths() {
  const widths = {};
  document.querySelectorAll("#aulas-table col[data-col]").forEach((col) => {
    widths[col.dataset.col] = Math.round(col.getBoundingClientRect().width);
  });
  try {
    sessionStorage.setItem("aula-col-widths", JSON.stringify(widths));
  } catch {}
}

function bindColResize() {
  const table = $("aulas-table");
  if (!table || table.dataset.resizeBound) return;
  table.dataset.resizeBound = "1";
  applyColWidths();
  table.querySelectorAll("thead th[data-col]").forEach((th) => {
    const handle = document.createElement("span");
    handle.className = "col-resizer";
    handle.title = "Arraste para ajustar a coluna";
    th.appendChild(handle);
    handle.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const key = th.dataset.col;
      const col = table.querySelector(`col[data-col="${key}"]`);
      if (!col) return;
      const startX = ev.clientX;
      const startW = col.getBoundingClientRect().width;
      document.body.classList.add("col-resizing");
      const onMove = (moveEv) => {
        const next = Math.max(56, startW + (moveEv.clientX - startX));
        col.style.width = `${next}px`;
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.classList.remove("col-resizing");
        saveColWidths();
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });
}

async function removeAula(arquivo) {
  const data = await api("/api/videos/remove", {
    method: "POST",
    body: JSON.stringify({ arquivo }),
  });
  state.aulas = data.aulas;
  state.session.fonte = data.fonte;
  delete state.planoByFile[arquivo];
  delete state.editsByFile[arquivo];
  renderAulas();
  setStep();
  await refreshPlan();
}

function collectEdits() {
  const rows = [...$("aulas").querySelectorAll("tr")];
  if (rows.length) {
    return rows.map((tr) => {
      const arquivo = tr.dataset.arquivo;
      const tituloEl = tr.querySelector('[data-k="titulo"]');
      const ordemEl = tr.querySelector('[data-k="ordem"]');
      const cached = state.editsByFile[arquivo] || {};
      const base = state.aulas.find((a) => a.arquivo === arquivo);
      return {
        arquivo,
        ordem: Number(ordemEl ? ordemEl.value : (cached.ordem ?? base?.ordem ?? 1)),
        titulo: tituloEl ? tituloEl.value : (cached.titulo ?? base?.titulo ?? ""),
      };
    });
  }
  return state.aulas.map((aula) => ({
    arquivo: aula.arquivo,
    ordem: aulaOrdem(aula),
    titulo: aulaTitulo(aula),
  }));
}

function cssStatus(status) {
  if (["ok", "enviando", "salvando", "falhou", "pulada", "processando", "pendente"].includes(status)) {
    return status;
  }
  return "";
}
function statusLabel(status, jobItem) {
  if (status === "enviando" && jobItem && jobItem.pct > 0 && jobItem.pct < 100) {
    return `enviando ${jobItem.pct}%`;
  }
  return {
    pendente: "pendente",
    enviando: "enviando",
    salvando: "salvando no portal",
    processando: "no Nivo…",
    ok: "✓ pronta",
    falhou: "falhou",
    pulada: "pulada",
    pular: "vai pular",
    "—": "—",
  }[status] || status;
}

function appendLog(entry) {
  const ol = $("log");
  const li = document.createElement("li");
  const link = entry.url
    ? ` · <a href="${entry.url}" target="_blank" rel="noopener">${escapeHtml(entry.url_label || "abrir")}</a>`
    : "";
  li.innerHTML = `<span class="ts">${escapeHtml(entry.ts)}</span><span class="${entry.level}">${escapeHtml(entry.message)}${link}</span>`;
  ol.appendChild(li);
  ol.scrollTop = ol.scrollHeight;
}

function jobSummary() {
  const job = state.session.job || {};
  const el = $("job-sum");
  if (!job.status) {
    el.textContent = "Nada enviado ainda.";
  } else if (job.status === "running") {
    el.textContent = "Enviando agora… acompanhe cada aula na tabela.";
  } else {
    el.textContent = `Último envio: ${job.ok || 0} certo · ${job.pulados || 0} pulada(s) · ${(job.falhas || []).length} falha(s).`;
  }
  renderXfer();
}

function fmtBytes(n) {
  const bytes = Number(n) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

function jobCapLabel(job) {
  const cap = (job && job.capitulo_nome) || "";
  if (cap) return cap;
  const cid = job && job.capitulo_id;
  if (cid) return `Capítulo ${cid}`;
  return (job && job.curso_nome) || "envio";
}

function jobFaseLabel(fase) {
  return {
    uploading: "enviando",
    processando: "no Nivo",
    queued: "na fila",
    done: "concluído",
    error: "com falha",
    comprimindo: "comprimindo",
    preparando: "na lista",
    idle: "",
  }[fase] || fase || "";
}

function fileUploadBusy() {
  const s = state.session || {};
  const job = s.job || {};
  if (job.fase === "uploading") {
    const items = job.items || [];
    // "salvando" = arquivo já no S3; não trava novo envio / UI.
    if (items.length && items.every((i) =>
      ["ok", "falhou", "pulada", "processando", "salvando"].includes(i.status)
    )) {
      return false;
    }
    if (items.some((i) => i.status === "enviando")) return true;
    return true;
  }
  if (s.uploading && job.fase !== "processando" && job.fase !== "done") {
    const items = job.items || [];
    if (items.some((i) => i.status === "enviando")) return true;
    if (items.length && items.every((i) =>
      ["ok", "falhou", "pulada", "processando", "salvando"].includes(i.status)
    )) {
      return false;
    }
    return Boolean(s.uploading);
  }
  return false;
}

function workspaceProject() {
  const cap = state.session && state.session.capitulo;
  if (!cap || !cap.id) return null;
  const n = state.aulas.length;
  const convert = (state.session && state.session.convert) || {};
  const convItems = convert.items || [];
  const converting = Boolean(state.session.converting)
    || convItems.some((i) => ["convertendo", "pendente"].includes(i.status));
  if (!n && !converting) return null;
  const jobs = (state.session && state.session.jobs) || [];
  const temJobAtivo = jobs.some((j) =>
    Number(j.capitulo_id) === Number(cap.id)
    && ["uploading", "processando", "queued"].includes(j.fase || "")
  );
  if (temJobAtivo) return null;
  const atual = ((state.session.job || {}).capitulo_id);
  if (atual && Number(atual) === Number(cap.id)
    && ["uploading", "processando", "queued"].includes((state.session.job || {}).fase || "")) {
    return null;
  }
  const atualConv = convItems.find((i) => i.status === "convertendo");
  let fase = "preparando";
  let faseTxt = n ? `${n} aula(s) · na lista` : "preparando";
  if (converting && atualConv) {
    fase = "comprimindo";
    faseTxt = `comprimindo ${Math.round(atualConv.pct || 0)}%`;
  } else if (converting) {
    fase = "comprimindo";
    faseTxt = "comprimindo…";
  }
  return {
    id: `workspace-${cap.id}`,
    synthetic: true,
    capitulo_id: cap.id,
    capitulo_nome: cap.nome || `Capítulo ${cap.id}`,
    curso_nome: (state.session && state.session.curso_nome) || "",
    fase,
    faseTxt,
    bucket: "andamento",
    items: state.aulas.map((a) => ({ arquivo: a.arquivo })),
  };
}

function jobFalhas(job) {
  return ((job && job.items) || []).filter((i) => i.status === "falhou").length;
}

function jobTemFalha(job) {
  if (!job) return false;
  if (jobFalhas(job)) return true;
  return ["error", "done_with_errors"].includes(job.status || "")
    || (job.fase === "error" && job.status !== "cancelado");
}

function projectBucket(job) {
  if (job.archived) return "historico";
  // Cancelado / erro antigo: não mistura com o envio que está rodando agora.
  if (["cancelado", "error", "done_with_errors"].includes(job.status || "")) {
    return "concluido";
  }
  if (jobTemFalha(job)) return "concluido";
  if (job.bucket) return job.bucket;
  if (job.synthetic) return "andamento";
  const fase = job.fase || "";
  if (["uploading", "processando", "queued", "comprimindo", "preparando"].includes(fase)) {
    return "andamento";
  }
  if (["done", "error"].includes(fase)) return "concluido";
  if ((job.items || []).length) return "andamento";
  return "concluido";
}

function collectProjects() {
  const jobs = [...((state.session && state.session.jobs) || [])];
  const atual = (state.session && state.session.job) || {};
  if (atual.id && !jobs.some((j) => j.id === atual.id)) {
    jobs.unshift(atual);
  }
  const ws = workspaceProject();
  if (ws && !jobs.some((j) => j.id === ws.id)) {
    jobs.unshift(ws);
  }
  const seenIds = new Set();
  const seenCapActive = new Set();
  const out = [];
  for (const j of jobs) {
    const fase = j.fase || "";
    if (!(
      ["uploading", "processando", "queued", "done", "error", "comprimindo", "preparando"].includes(fase)
      || (j.items || []).length
      || j.synthetic
    )) continue;
    const idKey = j.synthetic ? j.id : String(j.id || "");
    if (idKey && seenIds.has(idKey)) continue;
    // Um capítulo ativo = um card (evita dois "no Nivo" do mesmo envio).
    const cap = j.capitulo_id;
    const bucket = projectBucket(j);
    if (!j.synthetic && cap != null && bucket === "andamento") {
      const capKey = `${j.portal || ""}-${cap}`;
      if (seenCapActive.has(capKey)) continue;
      seenCapActive.add(capKey);
    }
    if (idKey) seenIds.add(idKey);
    out.push({ ...j, bucket });
  }
  return out;
}

function jobPctGeral(j) {
  const items = (j && j.items) || [];
  if (!items.length) return null;
  const soma = items.reduce((acc, i) => {
    if (["ok", "pulada", "processando", "salvando"].includes(i.status)) return acc + 100;
    if (i.status === "enviando") return acc + Math.max(0, Math.min(100, i.pct || 0));
    return acc;
  }, 0);
  return Math.round(soma / items.length);
}

function renderProjects() {
  const list = $("projects-list");
  if (!list) return;
  const filter = state.projectsFilter || "andamento";
  document.querySelectorAll("[data-projects-filter]").forEach((btn) => {
    const on = btn.dataset.projectsFilter === filter;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const projects = collectProjects().filter((j) => j.bucket === filter);
  const atual = (state.session && state.session.job) || {};
  const ws = workspaceProject();
  const atualId = (ws && (state.aulas.length || state.session.converting)) ? ws.id : atual.id;
  const loadingId = state.jobOpeningId || "";

  if (!projects.length) {
    const empty = {
      andamento: "Nenhum envio em andamento. Use + Novo envio para começar outro projeto neste capítulo.",
      concluido: "Nada concluído ainda. Quando um envio terminar, ele aparece aqui.",
      historico: "Arquive um projeto concluído para guardá-lo aqui, fora da fila ativa.",
    }[filter] || "Nenhum projeto.";
    list.innerHTML = `<p class="projects-empty">${escapeHtml(empty)}</p>`;
    return;
  }

  list.innerHTML = projects.map((j) => {
    const n = (j.items || []).length;
    const fase = jobFaseLabel(j.fase);
    const on = j.id && j.id === atualId ? " on" : "";
    const work = ["uploading", "processando", "queued", "comprimindo", "preparando"].includes(j.fase || "")
      ? " work"
      : "";
    const loading = j.id && j.id === loadingId;
    const pct = j.fase === "uploading" ? jobPctGeral(j) : null;
    const falhou = jobTemFalha(j);
    const nFalhas = jobFalhas(j);
    const txtFalha = nFalhas
      ? `${nFalhas} de ${n} com falha`
      : "parou antes de terminar";
    const faseTxt = loading
      ? "abrindo…"
      : (j.faseTxt
        || (pct != null
          ? `${n ? `${n} aula(s) · ` : ""}enviando ${pct}%`
          : (falhou
            ? `${txtFalha} · dá para continuar`
            : `${n ? `${n} aula(s)` : ""}${fase ? ` · ${fase}` : ""}`)));
    const curso = j.curso_nome ? `${j.curso_nome} · ` : "";
    const canArchive = !j.synthetic && filter !== "historico" && j.fase !== "uploading";
    const canRestore = !j.synthetic && filter === "historico";
    const canDelete = !j.synthetic && j.fase !== "uploading";
    const actions = [];
    if (canArchive) {
      actions.push(`<button type="button" data-archive="${escapeAttr(j.id || "")}">Arquivar</button>`);
    }
    if (canRestore) {
      actions.push(`<button type="button" data-unarchive="${escapeAttr(j.id || "")}">Restaurar</button>`);
    }
    if (canDelete) {
      actions.push(`<button type="button" class="danger" data-delete="${escapeAttr(j.id || "")}">Excluir do app</button>`);
    }
    return `<article class="project-row${on}${work}${falhou ? " falha" : ""}${loading ? " loading" : ""}" data-project="${escapeAttr(j.id || "")}">
      <div class="project-main">
        <div class="project-copy">
          <p class="project-title">${escapeHtml(jobCapLabel(j))}</p>
          <p class="project-meta">${falhou ? `<span class="project-falha">falhou</span> ` : ""}${escapeHtml(`${curso}${faseTxt}`)}</p>
        </div>
        <button type="button" class="project-open" data-job="${escapeAttr(j.id || "")}" data-synthetic="${j.synthetic ? "1" : "0"}" ${loading ? "disabled" : ""}>
          ${loading ? `<span class="chip-spin" aria-hidden="true"></span>` : ""}
          Abrir
        </button>
      </div>
      ${actions.length ? `<div class="project-actions">${actions.join("")}</div>` : ""}
    </article>`;
  }).join("");
}

function renderJobChips() {
  renderProjects();
}

async function archiveProject(jobId) {
  const ok = await confirmar(
    "Arquivar projeto",
    "Sai da fila ativa e fica no Histórico. Só some daqui — o portal não muda.",
    { ok: "Arquivar", cancelar: "Cancelar" },
  );
  if (!ok) return;
  try {
    const data = await api("/api/jobs/archive", {
      method: "POST",
      body: JSON.stringify({ id: jobId }),
    });
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    if (data.job && state.session.job && state.session.job.id === jobId) {
      state.session.job = data.job;
    }
    state.projectsFilter = "historico";
    renderProjects();
  } catch (err) {
    avisar("Não deu para arquivar", err.message);
  }
}

async function unarchiveProject(jobId) {
  try {
    const data = await api("/api/jobs/unarchive", {
      method: "POST",
      body: JSON.stringify({ id: jobId }),
    });
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    if (data.job && state.session.job && state.session.job.id === jobId) {
      state.session.job = data.job;
    }
    state.projectsFilter = "concluido";
    renderProjects();
  } catch (err) {
    avisar("Não deu para restaurar", err.message);
  }
}

async function deleteProject(jobId) {
  const ok = await confirmar(
    "Excluir do app",
    "Apaga este envio só deste computador (fila e histórico local). As aulas no portal continuam.",
    { ok: "Excluir do app", cancelar: "Cancelar" },
  );
  if (!ok) return;
  try {
    const data = await api("/api/jobs/delete", {
      method: "POST",
      body: JSON.stringify({ id: jobId }),
    });
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    if (data.session) state.session = { ...state.session, ...data.session };
    if (Array.isArray(data.aulas)) state.aulas = mergeServerAulas(data.aulas);
    if (state.session.job && state.session.job.id === jobId) {
      state.session.job = {};
    }
    renderAulas();
    jobSummary();
    renderProjects();
    setStep();
  } catch (err) {
    avisar("Não deu para excluir", err.message);
  }
}

function renderXfer() {
  const el = $("xfer");
  if (!el) return;
  const x = state.xfer;
  const job = (state.session && state.session.job) || {};
  const items = job.items || [];
  let title = "";
  let detail = "";
  let pct = null;
  let mode = "";
  const cap = jobCapLabel(job);
  const total = items.length || state.aulas.length;
  if (x) {
    title = x.title;
    detail = x.detail || "";
    pct = x.pct;
    mode = "work";
  } else {
    // Envio ao portal aparece na coluna Status de cada aula e no card do
    // projeto — o card flutuante só atrapalhava, repetindo a mesma coisa.
    mode = "idle";
  }
  const showBar = Boolean(x) || mode === "work";
  el.classList.toggle("hidden", !showBar);
  el.classList.toggle("done", false);
  el.classList.toggle("idle", mode === "idle");
  el.classList.toggle("indeterminate", pct == null && mode === "work");
  if (showBar) {
    $("xfer-title").textContent = title;
    $("xfer-detail").textContent = detail;
    $("xfer-fill").style.width = pct == null ? "34%" : `${Math.max(4, Math.min(100, pct))}%`;
    $("xfer-pct").textContent = pct == null ? "" : `${Math.round(pct)}%`;
  }
  renderJobChips();
}

async function openJobDetails(jobId, { synthetic = false } = {}) {
  if (synthetic || String(jobId || "").startsWith("workspace-")) {
    state.jobOpeningId = jobId || "workspace";
    renderJobChips();
    if (isNew()) goStory("videos");
    else {
      state.view = "videos";
      setStep();
    }
    renderAulas();
    renderXfer();
    const midia = $("midia");
    if (midia) midia.scrollIntoView({ block: "start", behavior: "smooth" });
    state.jobOpeningId = "";
    renderJobChips();
    return;
  }
  const id = jobId || ((state.session.job || {}).id || "");
  state.jobOpeningId = id || "pending";
  renderJobChips();
  try {
    const data = await api("/api/jobs/open", {
      method: "POST",
      body: JSON.stringify({ id }),
    });
    if (data.job) state.session.job = data.job;
    if (data.session) {
      state.session = { ...state.session, ...data.session, job: data.job || data.session.job };
    }
    if (data.aulas) {
      state.aulas = mergeServerAulas(data.aulas);
      state.session.fonte = data.fonte || state.session.fonte;
    }
    if (isNew()) goStory("videos");
    else {
      state.view = "videos";
      setStep();
    }
    renderAulas();
    jobSummary();
    renderXfer();
    setStep();
    const midia = $("midia");
    if (midia) midia.scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (err) {
    avisar("Deu problema", err.message);
  } finally {
    state.jobOpeningId = "";
    renderJobChips();
  }
}

function avisar(titulo, texto) {
  return abrirModalAviso(titulo, texto, { confirmar: false });
}

function confirmar(titulo, texto, { ok = "Enviar", cancelar = "Cancelar" } = {}) {
  return abrirModalAviso(titulo, texto, { confirmar: true, ok, cancelar });
}

function abrirModalAviso(titulo, texto, { confirmar = false, ok = "Entendi", cancelar = "Cancelar" } = {}) {
  const modal = $("modal-aviso");
  if (!modal) return Promise.resolve(confirmar ? false : undefined);
  $("modal-aviso-title").textContent = titulo;
  $("modal-aviso-body").textContent = texto || "";
  const btnOk = $("modal-aviso-ok");
  const btnCancel = $("modal-aviso-cancel");
  if (btnOk) btnOk.textContent = ok;
  if (btnCancel) {
    btnCancel.textContent = cancelar;
    btnCancel.classList.toggle("hidden", !confirmar);
  }
  modal.classList.remove("hidden");
  return new Promise((resolve) => {
    const fechar = (valor) => {
      modal.classList.add("hidden");
      if (btnOk) btnOk.onclick = null;
      if (btnCancel) btnCancel.onclick = null;
      modal.querySelectorAll("[data-aviso-close]").forEach((el) => el.onclick = null);
      document.removeEventListener("keydown", onKey);
      resolve(confirmar ? Boolean(valor) : undefined);
    };
    function onKey(ev) {
      if (ev.key === "Escape") fechar(false);
      if (ev.key === "Enter") fechar(true);
    }
    document.addEventListener("keydown", onKey);
    if (btnOk) btnOk.onclick = () => fechar(true);
    if (btnCancel) btnCancel.onclick = () => fechar(false);
    modal.querySelectorAll("[data-aviso-close]").forEach((el) => {
      el.onclick = () => fechar(false);
    });
  });
}

function renderConvert() {
  const box = $("convert-xfer");
  if (box) box.classList.add("hidden");
  // Progresso na linha do vídeo + chip do projeto na faixa.
  renderJobChips();
  renderAulas();
}

function fmtEta(segundos) {
  const s = Math.max(0, Math.round(Number(segundos) || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}min${s % 60 ? ` ${s % 60}s` : ""}`;
}

async function converterAulas(arquivos) {
  if (state.session.converting) return;
  try {
    const data = await api("/api/videos/convert", {
      method: "POST",
      body: JSON.stringify({ arquivos: arquivos || [] }),
    });
    state.session.convert = data.convert;
    state.session.converting = true;
    renderConvert();
    renderAulas();
    setStep();
  } catch (err) {
    await avisar("Não deu para comprimir", err.message);
  }
}

async function cancelarConversao() {
  try {
    await api("/api/videos/convert/cancel", { method: "POST" });
  } catch (err) {
    await avisar("Não deu para cancelar", err.message);
  }
}

function abrirPreview(arquivo) {
  const modal = $("modal-preview");
  const convertidos = state.session.convertidos || {};
  const destino = convertidos[arquivo];
  if (!modal || !destino) return;
  const nomeNovo = destino.split("/").pop();
  const item = ((state.session.convert || {}).items || []).find((i) => i.arquivo === arquivo);
  const aula = state.aulas.find((a) => a.arquivo === arquivo) || {};

  $("modal-preview-title").textContent = "Conferir o vídeo comprimido";
  $("modal-preview-body").textContent = "Veja se a imagem e o áudio ficaram bons antes de trocar o arquivo do envio.";
  $("preview-cmp").innerHTML = `
    <div><span class="cmp-lab">antes</span>${escapeHtml(item ? item.tamanho_antes_fmt : aula.tamanho_fmt || "")}${aula.resolucao ? ` · ${escapeHtml(aula.resolucao)}` : ""}</div>
    <div><span class="cmp-lab">depois</span><strong>${escapeHtml(item ? item.tamanho_novo_fmt : "")}</strong>${item && item.resolucao_nova ? ` · ${escapeHtml(item.resolucao_nova)}` : ""}</div>
  `;
  const player = $("preview-player");
  player.src = `/api/videos/preview?arquivo=${encodeURIComponent(nomeNovo)}`;
  modal.classList.remove("hidden");
  modal.dataset.arquivo = arquivo;
}

function fecharPreview() {
  const modal = $("modal-preview");
  if (!modal) return;
  const player = $("preview-player");
  player.pause();
  player.removeAttribute("src");
  player.load();
  modal.classList.add("hidden");
  delete modal.dataset.arquivo;
}

async function usarConvertido(arquivo) {
  try {
    const data = await api("/api/videos/convert/apply", {
      method: "POST",
      body: JSON.stringify({ arquivos: arquivo ? [arquivo] : [] }),
    });
    const nomesAntes = new Set(state.aulas.map((a) => a.arquivo));
    state.aulas = mergeServerAulas(data.aulas);
    if (arquivo && !state.aulas.some((a) => a.arquivo === arquivo)) {
      const novo = state.aulas.find((a) => !nomesAntes.has(a.arquivo));
      if (novo) migrateEditKey(arquivo, novo.arquivo);
    }
    if (arquivo && state.session.convertidos) delete state.session.convertidos[arquivo];
    renderAulas();
    setStep();
    await refreshPlan();
  } catch (err) {
    await avisar("Não deu para trocar o arquivo", err.message);
  }
}

function uploadForm(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) onProgress(ev.loaded, ev.total);
      };
    }
    xhr.onload = () => {
      let data = {};
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : {};
      } catch {
        data = { detail: xhr.responseText };
      }
      if (xhr.status >= 400) {
        const detail = data.detail || xhr.statusText;
        reject(new Error(typeof detail === "string" ? detail : JSON.stringify(detail)));
        return;
      }
      resolve(data);
    };
    xhr.onerror = () => reject(new Error("Não consegui enviar os arquivos."));
    xhr.send(form);
  });
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}
function escapeAttr(value) {
  return escapeHtml(value);
}

async function bootstrap() {
  const data = await api("/api/bootstrap");
  state.portals = data.portals;
  state.platform = data.platform || state.platform;
  state.session = data.session;
  state.portal = data.session.portal || (data.portals[0] && data.portals[0].key);
  restoreCreds(state.portal);
  state.aulas = data.session.aulas || [];
  (data.logs || []).forEach(appendLog);
  atualizarConvertidos();
  applyPlatformUi();
  renderLogin();
  renderCursos();
  renderCaps();
  renderAulas();
  renderConvert();
  jobSummary();
  setStep();
  if (state.session.autenticado) {
    await loadCatalog();
    if (state.session.curso_id && state.catalogById[state.session.curso_id]) {
      state.capitulos = state.catalogById[state.session.curso_id];
      renderCaps();
    }
    setStep();
    await refreshNivo();
  }
}

async function refreshNivo() {
  try {
    const data = await api("/api/nivo/refresh", { method: "POST", body: "{}" });
    if (data.job) state.session.job = data.job;
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    jobSummary();
    renderAulas();
    setStep();
  } catch {
    /* sessão/portal ainda não prontos */
  }
}

async function syncJobFromServer() {
  try {
    const data = await api("/api/job");
    if (data.job) state.session.job = data.job;
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    if (typeof data.uploading === "boolean") state.session.uploading = data.uploading;
    jobSummary();
    renderAulas();
    renderXfer();
    renderProjects();
    setStep();
  } catch {
    /* ignore */
  }
}

function jobNeedsSync() {
  const job = (state.session && state.session.job) || {};
  const items = job.items || [];
  if (["running", "processando", "queued"].includes(job.status || "")) return true;
  if (["uploading", "processando", "queued"].includes(job.fase || "")) return true;
  if (items.some((i) =>
    ["enviando", "salvando", "processando", "pendente"].includes(i.status)
  )) return true;
  // Também olha a lista de projetos: job antigo pode estar processando.
  for (const j of (state.session && state.session.jobs) || []) {
    if (["running", "processando", "queued"].includes(j.status || j.fase || "")) return true;
    if ((j.items || []).some((i) =>
      ["enviando", "salvando", "processando", "pendente"].includes(i.status)
    )) return true;
  }
  return false;
}

async function loadCatalog() {
  const data = await api("/api/catalog");
  applyCatalog(data, { replace: true });
}

function applyCatalog(data, { replace = false } = {}) {
  state.produtos = data.produtos || state.produtos || [];
  state.catalogById = replace ? {} : state.catalogById;
  const next = [];
  const seen = new Set();
  (data.cursos || []).forEach((c) => {
    seen.add(c.id);
    if (c.capitulos) state.catalogById[c.id] = c.capitulos;
    next.push({
      id: c.id,
      nome: c.nome,
      fonte: "mapeado",
      produto_ids: c.produto_ids || [],
    });
  });
  if (!replace) {
    state.cursosAll.forEach((c) => {
      if (!seen.has(c.id)) next.push(c);
    });
  }
  state.cursosAll = next;
  renderProdutoFiltro();
  filterCursosLocal();
}

function cursoNoFiltro(cursoId) {
  if (!cursoId) return true;
  const course = state.cursosAll.find((c) => c.id === cursoId);
  const ids = (course && course.produto_ids) || [];
  const productId = state.produtoFiltro || "";
  if (productId) return ids.includes(productId);
  const idsPortal = new Set(produtosDoPortal().map((p) => p.id));
  if (!ids.length) return true;
  return ids.some((id) => idsPortal.has(id));
}

function limparCursoSeForaDoProduto() {
  if (cursoNoFiltro(state.session.curso_id)) return;
  state.session.curso_id = null;
  state.session.curso_nome = "";
  state.session.curso_url = "";
  state.session.capitulo = null;
  state.capitulos = [];
  state.busyCurso = null;
  state.busyCap = null;
  state.view = "curso";
}

function produtosDoPortal() {
  const portal = state.portal;
  return state.produtos.filter((p) => !p.portal || p.portal === portal);
}

function renderProdutoFiltro() {
  const host = $("produto-picks");
  if (!host) return;
  const visiveis = produtosDoPortal();
  const ids = new Set(visiveis.map((p) => p.id));
  if (state.produtoFiltro && !ids.has(state.produtoFiltro)) {
    state.produtoFiltro = "";
  }
  limparCursoSeForaDoProduto();
  const current = state.produtoFiltro || "";
  if (isNew()) {
    const items = visiveis.map((p) => {
      const on = current === p.id;
      return `<button type="button" class="story-item${on ? " on" : ""}" data-produto="${escapeAttr(p.id)}">${escapeHtml(p.nome_curto || p.nome)}<span class="meta">${on ? "atual" : ""}</span></button>`;
    }).join("");
    host.innerHTML = `<div class="story-menu">${items}</div>`;
  } else {
    const parts = [`<div class="produto-grupo"><div class="row">${produtoPill("", "Todos", current === "")}`];
    visiveis.forEach((p) => {
      parts.push(produtoPill(p.id, p.nome_curto || p.nome, current === p.id));
    });
    parts.push("</div></div>");
    host.innerHTML = parts.join("");
  }
  host.querySelectorAll("[data-produto]").forEach((btn) => {
    btn.onclick = (ev) => {
      ev.stopPropagation();
      state.produtoFiltro = btn.dataset.produto || "";
      limparCursoSeForaDoProduto();
      renderProdutoFiltro();
      filterCursosLocal();
      setStep();
      if (isNew() && state.produtoFiltro) goStory("curso");
    };
  });
}

function produtoPill(id, label, on) {
  return `<button type="button" class="tab${on ? " on" : ""}" data-produto="${escapeAttr(id)}">${escapeHtml(label)}</button>`;
}

function courseBlob(curso) {
  const names = (curso.produto_ids || [])
    .map((pid) => state.produtos.find((p) => p.id === pid))
    .filter((p) => p && (!p.portal || p.portal === state.portal))
    .map((p) => `${p.nome} ${p.nome_curto || ""} ${p.id}`)
    .join(" ");
  return `${curso.nome} ${curso.id} ${names}`;
}

function filterCursosLocal() {
  const q = $("curso-q").value;
  const productId = state.produtoFiltro || "";
  const visiveis = produtosDoPortal();
  const idsPortal = new Set(visiveis.map((p) => p.id));
  const doPortal = (c) => {
    const ids = c.produto_ids || [];
    // Se um produto está selecionado, mostramos também cursos “soltos” que vieram
    // da busca no portal (pra você poder clicar e atribuir manualmente).
    if (!ids.length) return !productId || c.fonte === "portal";
    return ids.some((id) => idsPortal.has(id));
  };
  const queryHits = state.cursosAll.filter((c) => matches(courseBlob(c), q) && doPortal(c));
  const otherProductHit = q.trim() && visiveis.some(
    (p) => p.id !== productId && matches(`${p.nome} ${p.nome_curto || ""} ${p.id}`, q)
  );
  if (productId && !otherProductHit) {
    state.cursos = queryHits.filter((c) => {
      const ids = c.produto_ids || [];
      if (ids.includes(productId)) return true;
      // Curso que veio da busca no portal ainda não tem produto: mostra para poder vincular.
      return !ids.length && c.fonte === "portal";
    });
  } else {
    state.cursos = queryHits;
  }
  const n = state.cursos.length;
  if (q.trim()) {
    $("curso-status").textContent = otherProductHit && productId
      ? `${n} resultado(s) — o filtro de produto foi ignorado na busca`
      : `${n} na lista local`;
  } else if (productId) {
    const product = state.produtos.find((p) => p.id === productId) || {};
    const nome = product.nome_curto || product.nome || productId;
    $("curso-status").textContent = n
      ? `${n} curso(s) em ${nome}`
      : `Nenhum curso neste produto ainda. Busque e vincule.`;
  } else {
    $("curso-status").textContent = `${state.cursos.length} curso(s)`;
  }
  renderCursos();
  renderSelecao();
}

async function assignProduto(curso) {
  const produto = state.produtoFiltro;
  if (!produto) {
    $("curso-status").textContent = "Escolha um produto acima para vincular.";
    return;
  }
  await assignProdutoIds(curso, [produto]);
}

async function assignProdutoIds(curso, produtoIds) {
  const ids = (produtoIds || []).filter(Boolean);
  if (!ids.length) return;
  let data = null;
  try {
    for (const produto of ids) {
      data = await api("/api/produtos/assign", {
        method: "POST",
        body: JSON.stringify({ curso: String(curso.id), produto, nome: curso.nome || "" }),
      });
    }
  } catch (err) {
    $("curso-status").textContent = err.message;
    return;
  }
  if (data) applyCatalog(data);
  const nomes = ids.map((id) => {
    const p = state.produtos.find((x) => x.id === id);
    return p ? (p.nome_curto || p.nome) : id;
  });
  $("curso-status").textContent = `Vinculou ${curso.nome} a ${nomes.join(", ")} (só neste computador).`;
}

async function unassignProduto(curso, produto) {
  try {
    const data = await api("/api/produtos/unassign", {
      method: "POST",
      body: JSON.stringify({ curso: String(curso.id), produto }),
    });
    applyCatalog(data);
  } catch (err) {
    $("curso-status").textContent = err.message;
  }
}

async function confirmAtribuirCursos({ cursoNome = "", qtd = 1, preselect = [] } = {}) {
  const modal = $("modal-atribuir");
  const host = $("modal-atribuir-prods");
  const produtos = produtosDoPortal();
  if (!modal || !host || !produtos.length) return [];
  const marcados = new Set(preselect.filter((id) => produtos.some((p) => p.id === id)));
  $("modal-atribuir-title").textContent = qtd === 1 ? "Atribuir curso" : "Atribuir cursos";
  $("modal-atribuir-body").textContent = cursoNome && qtd === 1
    ? `Em quais produtos entra “${cursoNome}”? Pode marcar mais de um.`
    : `Em quais produtos entram os ${qtd} curso(s) buscados? Pode marcar mais de um.`;
  host.innerHTML = produtos
    .map((p) => {
      const on = marcados.has(p.id);
      return `<label class="${on ? "on" : ""}"><input type="checkbox" value="${escapeAttr(p.id)}"${on ? " checked" : ""}><span>${escapeHtml(p.nome_curto || p.nome)}</span></label>`;
    })
    .join("");
  const inputs = [...host.querySelectorAll("input")];
  const btnOk = $("modal-atribuir-ok");
  const btnCancel = $("modal-atribuir-cancel");
  const sync = () => {
    inputs.forEach((i) => i.closest("label").classList.toggle("on", i.checked));
    if (btnOk) btnOk.disabled = !inputs.some((i) => i.checked);
  };
  inputs.forEach((i) => { i.onchange = sync; });
  sync();
  modal.classList.remove("hidden");
  return new Promise((resolve) => {
    const onClose = (confirmou) => {
      modal.classList.add("hidden");
      if (btnOk) { btnOk.onclick = null; btnOk.disabled = false; }
      if (btnCancel) btnCancel.onclick = null;
      inputs.forEach((i) => { i.onchange = null; });
      modal.querySelectorAll("[data-modal-close]").forEach((el) => el.onclick = null);
      document.removeEventListener("keydown", onKey);
      resolve(confirmou ? inputs.filter((i) => i.checked).map((i) => i.value) : []);
    };
    function onKey(ev) {
      if (ev.key === "Escape") onClose(false);
    }
    document.addEventListener("keydown", onKey);
    if (btnOk) btnOk.onclick = () => onClose(true);
    if (btnCancel) btnCancel.onclick = () => onClose(false);
    modal.querySelectorAll("[data-modal-close]").forEach((el) => {
      el.onclick = () => onClose(false);
    });
  });
}

async function searchCursos() {
  const q = $("curso-q").value.trim();
  filterCursosLocal();
  if (!q || !state.session.autenticado) return;
  $("curso-status").textContent = "Buscando no portal…";
  try {
    const data = await api("/api/cursos/search", {
      method: "POST",
      body: JSON.stringify({ q }),
    });
    const seen = new Set(state.cursosAll.map((c) => c.id));
    data.cursos.forEach((c) => {
      if (!seen.has(c.id)) {
        state.cursosAll.push({
          id: c.id,
          nome: c.nome,
          fonte: c.fonte || "portal",
          produto_ids: c.produto_ids || [],
        });
      }
    });
    filterCursosLocal();
    $("curso-status").textContent = `${state.cursos.length} resultado(s)`;
  } catch (err) {
    $("curso-status").textContent = err.message;
  }
}

async function selectCurso(curso) {
  const id = typeof curso === "object" ? curso.id : Number(curso);
  const nome = typeof curso === "object" ? curso.nome : "";
  const produtoId = state.produtoFiltro || "";
  const pids = (typeof curso === "object" && curso.produto_ids) ? curso.produto_ids : [];
  const temProduto = Boolean(produtoId && pids.includes(produtoId));
  if (produtoId && typeof curso === "object" && curso.fonte === "portal" && !temProduto) {
    const escolhidos = await confirmAtribuirCursos({
      cursoNome: nome || String(id),
      preselect: [produtoId],
    });
    if (escolhidos.length) await assignProdutoIds(curso, escolhidos);
  }
  state.busyCurso = id;
  state.session.curso_id = id;
  state.session.curso_nome = nome || state.session.curso_nome;
  state.session.capitulo = null;
  state.view = "curso";
  if (state.catalogById[id]) {
    state.capitulos = state.catalogById[id];
    $("cap-status").textContent = `${state.capitulos.length} capítulo(s) salvos · atualizando…`;
  } else {
    state.capitulos = [];
    $("cap-status").textContent = "Carregando capítulos…";
  }
  renderCursos();
  renderCaps();
  setStep();
  try {
    const data = await api("/api/cursos/select", {
      method: "POST",
      body: JSON.stringify({ curso: String(id), nome }),
    });
    if (state.busyCurso !== id) return;
    state.session.curso_id = data.curso.id;
    state.session.curso_nome = data.curso.nome;
    state.session.curso_url = data.curso.url;
    state.capitulos = data.capitulos;
    state.catalogById[id] = data.capitulos;
    $("curso-status").textContent = `${data.capitulos.length} capítulo(s) · pode continuar`;
    if (isNew()) goStory("capitulo");
    else state.view = "curso";
  } catch (err) {
    $("curso-status").textContent = err.message;
    avisar("Deu problema", err.message);
  } finally {
    if (state.busyCurso === id) state.busyCurso = null;
    renderCursos();
    renderCaps();
    setStep();
  }
}

async function selectCapitulo(capitulo) {
  const id = typeof capitulo === "object" ? capitulo.id : capitulo;
  const nome = typeof capitulo === "object" ? capitulo.nome : "";
  state.busyCap = typeof id === "number" ? id : null;
  if (typeof capitulo === "object") {
    state.session.capitulo = {
      id: capitulo.id,
      nome: capitulo.nome,
      curso_id: state.session.curso_id,
      url: "",
    };
  }
  $("cap-status").textContent = "Confirmando no portal…";
  state.view = "capitulo";
  renderCaps();
  if (isNew() && state.session.capitulo) goStory("videos");
  else setStep();
  try {
    const data = await api("/api/capitulos/select", {
      method: "POST",
      body: JSON.stringify({ capitulo: String(id) }),
    });
    applyCapitulo(data);
    $("cap-status").textContent = `${(data.existentes || []).length} aula(s) já no capítulo`;
  } catch (err) {
    $("cap-status").textContent = err.message;
    avisar("Deu problema", err.message);
  } finally {
    state.busyCap = null;
    renderCaps();
    setStep();
  }
}

async function login() {
  if (state.busyLogin) return false;
  const status = $("login-status");
  state.busyLogin = true;
  setStep();
  status.className = "status-line";
  status.textContent = "Entrando…";
  try {
    rememberCreds();
    const session = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        portal: state.portal,
        username: $("user").value,
        password: $("pass").value,
        persist: $("persist").checked,
      }),
    });
    $("pass").value = "";
    if (state.creds[state.portal]) state.creds[state.portal].pass = "";
    const chosen = state.portals.find((p) => p.key === state.portal);
    if (chosen) chosen.has_session = $("persist").checked || chosen.has_session;
    status.textContent = "";
    await afterPortalSession(session, { stay: true });
    return true;
  } catch (err) {
    status.className = "status-line warn";
    status.textContent = err.message;
    return false;
  } finally {
    state.busyLogin = false;
    setStep();
  }
}

async function advanceFromLogin() {
  if (!isAuthed(state.portal)) {
    const ok = await login();
    if (!ok) return;
  }
  if (isNew()) goStory("produto");
  else goView("curso");
}

async function logout() {
  const session = await api("/api/logout", { method: "POST" });
  if (session.portal) state.portal = session.portal;
  restoreCreds(state.portal);
  await afterPortalSession(session, { stay: true });
}

function applyCapitulo(data) {
  state.session.capitulo = data.capitulo;
  if (data.capitulo.curso_id) {
    state.session.curso_id = data.capitulo.curso_id;
    state.session.curso_nome = data.capitulo.curso_nome;
  }
  if (data.job) state.session.job = data.job;
  if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
  if (typeof data.uploading === "boolean") state.session.uploading = data.uploading;
  if (Array.isArray(data.aulas)) {
    state.aulas = mergeServerAulas(data.aulas);
    state.session.fonte = data.fonte || "";
  }
  renderCaps();
  renderAulas();
  jobSummary();
  if (isNew()) goStory("videos");
  else {
    state.view = "capitulo";
    setStep();
  }
  refreshPlan();
}

async function createCapitulo() {
  const status = $("cap-create-status");
  const btn = $("btn-cap-create");
  const nome = $("cap-nome").value.trim();
  const bunny = $("cap-bunny").value.trim();
  if (!nome) {
    status.className = "status-line warn";
    status.textContent = "Informe o nome do capítulo.";
    return;
  }
  if (!bunny) {
    status.className = "status-line warn";
    status.textContent = "Cole a URL da pasta no Bunny, ou o ID dela.";
    return;
  }
  btn.disabled = true;
  status.className = "status-line";
  status.textContent = "Criando no portal…";
  try {
    const data = await api("/api/capitulos/create", {
      method: "POST",
      body: JSON.stringify({
        nome,
        ordem: Number($("cap-ordem").value),
        bunny,
      }),
    });
    status.className = "status-line ok";
    status.textContent = `Criado: ${data.capitulo.nome} (ID ${data.capitulo.id})`;
    applyCapitulo(data);
  } catch (err) {
    status.className = "status-line warn";
    status.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function refreshPlan() {
  if (!state.session.capitulo || !state.aulas.length) return;
  try {
    const data = await api("/api/plan", {
      method: "POST",
      body: JSON.stringify({ force: $("force").checked, aulas: collectEdits() }),
    });
    state.planoByFile = {};
    data.plano.forEach((item) => {
      state.planoByFile[item.arquivo] = item;
    });
    const dup = Object.entries(data.duplicados || {});
    const warn = $("dup-warn");
    if (dup.length) {
      warn.classList.remove("hidden");
      warn.textContent = "Títulos repetidos: " + dup.map(([t, files]) => `${t} (${files.join(", ")})`).join(" · ");
    } else {
      warn.classList.add("hidden");
    }
    renderAulas();
  } catch {
    renderAulas();
  }
}

async function sendVideos() {
  const ok = await confirmar(
    "Enviar aulas",
    "Enviar essas aulas para o portal agora?",
    { ok: "Enviar", cancelar: "Cancelar" },
  );
  if (!ok) return;
  $("btn-send").disabled = true;
  try {
    const data = await api("/api/upload", {
      method: "POST",
      body: JSON.stringify({
        publicar: $("publicar").checked,
        force: $("force").checked,
        aulas: collectEdits(),
      }),
    });
    state.session.job = data.job;
    state.session.uploading = true;
    jobSummary();
    renderAulas();
    setStep();
    $("xfer").scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch (err) {
    avisar("Deu problema", err.message);
    setStep();
  }
}

async function applyVideoList(data) {
  state.aulas = mergeServerAulas(data.aulas);
  state.session.fonte = data.fonte;
  // Compressões de uma lista antiga não valem mais para a lista nova.
  const listados = new Set(state.aulas.map((a) => a.arquivo));
  const convertidos = state.session.convertidos || {};
  Object.keys(convertidos).forEach((nome) => {
    if (!listados.has(nome)) delete convertidos[nome];
  });
  const items = ((state.session.convert || {}).items || []).filter((i) => listados.has(i.arquivo));
  if (state.session.convert) state.session.convert.items = items;
  renderAulas();
  renderConvert();
  setStep();
  await refreshPlan();
}

async function pickFolder(append) {
  const buttons = ["btn-folder", "btn-add-folder"].map($).filter(Boolean);
  buttons.forEach((btn) => { btn.disabled = true; });
  state.xfer = {
    title: "Lendo a pasta",
    detail: pathHints().folderDetail,
    pct: null,
  };
  renderXfer();
  $("xfer").scrollIntoView({ block: "nearest", behavior: "smooth" });
  try {
    const data = await api("/api/videos/pick-folder", {
      method: "POST",
      body: JSON.stringify({ append: Boolean(append) }),
    });
    if (data.cancelled) return;
    await applyVideoList(data);
    if (isNew()) goStory("videos");
  } catch (err) {
    avisar("Deu problema", err.message);
  } finally {
    state.xfer = null;
    renderAulas();
    renderXfer();
    setStep();
    buttons.forEach((btn) => { btn.disabled = false; });
  }
}

async function uploadCollected(entries, reset) {
  if (!entries.length) return;
  const totalBytes = entries.reduce((sum, item) => sum + (item.file.size || 0), 0);
  state.xfer = {
    title: "Carregando os arquivos",
    detail: `${entries.length} arquivo(s) · ${fmtBytes(totalBytes)}`,
    pct: 0,
  };
  renderXfer();
  $("xfer").scrollIntoView({ block: "nearest", behavior: "smooth" });
  try {
    const form = new FormData();
    form.append("reset", reset ? "1" : "0");
    entries.forEach(({ file }) => {
      form.append("files", file, file.name);
    });
    entries.forEach(({ rel }) => {
      form.append("rels", rel);
    });
    const data = await uploadForm("/api/videos/upload", form, (loaded, total) => {
      state.xfer = {
        title: "Carregando os arquivos",
        detail: `${fmtBytes(loaded)} de ${fmtBytes(total)}`,
        pct: total ? (loaded / total) * 100 : 0,
      };
      renderXfer();
    });
    state.xfer = { title: "Montando a lista", detail: "Lendo os vídeos…", pct: 96 };
    renderXfer();
    await applyVideoList(data);
  } finally {
    state.xfer = null;
    renderXfer();
    setStep();
  }
}

function fileSpecs(files) {
  return files.map((file) => ({ name: file.name, size: file.size || 0 }));
}

async function tryMatchLocal(specs, reset) {
  const hint = ($("local-path") && $("local-path").value) || "";
  const data = await api("/api/videos/match-local", {
    method: "POST",
    body: JSON.stringify({
      files: specs,
      hint,
      reset: Boolean(reset),
    }),
  });
  const missing = data.missing || [];
  const matched = data.matched || [];
  if (matched.length && missing.length === 0 && Array.isArray(data.aulas)) {
    await applyVideoList(data);
    return true;
  }
  return false;
}

async function ingestFiles(files, reset) {
  const list = [...files];
  if (!list.length) return;
  if (await tryMatchLocal(fileSpecs(list), reset)) return;
  const totalBytes = list.reduce((sum, file) => sum + (file.size || 0), 0);
  if (totalBytes > 80 * 1024 * 1024) {
    await avisar(
      "Arquivos grandes demais para copiar",
      "O app abre os vídeos direto no disco. Cole o caminho da pasta abaixo e clique em Abrir — arrastar pelo navegador copiaria vários GB.",
    );
    return;
  }
  await uploadCollected(
    list.map((file) => ({ file, rel: file.webkitRelativePath || file.name })),
    reset,
  );
}

function walkEntry(entry, prefix) {
  return new Promise((resolve, reject) => {
    if (entry.isFile) {
      entry.file((file) => resolve([{ file, rel: prefix + file.name }]), reject);
      return;
    }
    if (!entry.isDirectory) {
      resolve([]);
      return;
    }
    const reader = entry.createReader();
    const all = [];
    const read = () => {
      reader.readEntries(async (batch) => {
        if (!batch.length) {
          const nested = [];
          for (const child of all) {
            nested.push(...await walkEntry(child, prefix + entry.name + "/"));
          }
          resolve(nested);
          return;
        }
        all.push(...batch);
        read();
      }, reject);
    };
    read();
  });
}

async function fromDrop(event) {
  event.preventDefault();
  event.stopPropagation();
  document.querySelectorAll(".over").forEach((el) => el.classList.remove("over"));
  const dt = event.dataTransfer;
  const files = [...(dt.files || [])];
  const items = [...(dt.items || [])];
  const entries = items
    .filter((item) => item.kind === "file")
    .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null));
  const reset = state.aulas.length === 0;
  const specs = fileSpecs(files);
  if (await tryMatchLocal(specs, reset)) {
    if (isNew()) goStory("videos");
    return;
  }
  const hasDir = entries.some((entry) => entry && entry.isDirectory);
  if (hasDir) {
    const collected = [];
    for (const entry of entries.filter(Boolean)) {
      collected.push(...await walkEntry(entry, ""));
    }
    await ingestFiles(collected.map((item) => item.file), reset);
  } else {
    await ingestFiles(files, reset);
  }
  if (isNew()) goStory("videos");
}

function isFileDrag(ev) {
  return [...(ev.dataTransfer.types || [])].includes("Files");
}

function bindDropTarget(el, { needChapter = false } = {}) {
  if (!el) return;
  el.addEventListener("dragover", (ev) => {
    if (!isFileDrag(ev)) return;
    if (needChapter && !canStory("videos")) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    el.classList.add("over");
  });
  el.addEventListener("dragleave", (ev) => {
    if (el.contains(ev.relatedTarget)) return;
    el.classList.remove("over");
  });
  el.addEventListener("drop", (ev) => {
    el.classList.remove("over");
    if (needChapter && !canStory("videos")) {
      ev.preventDefault();
      avisar("Falta escolher o capítulo", "Escolha o capítulo antes de soltar os vídeos.");
      return;
    }
    fromDrop(ev).catch((err) => avisar("Deu problema", err.message));
  });
}

async function fromInput(input, reset) {
  const files = [...input.files];
  input.value = "";
  await ingestFiles(files, reset);
}

async function pickFiles(append) {
  if (!(state.platform && state.platform.folder_picker)) {
    $("file-input").click();
    return;
  }
  const buttons = ["btn-files", "btn-add-more"].map($).filter(Boolean);
  buttons.forEach((btn) => { btn.disabled = true; });
  state.xfer = { title: "Escolhendo arquivos", detail: "Seletor nativo…", pct: null };
  renderXfer();
  try {
    const data = await api("/api/videos/pick-files", {
      method: "POST",
      body: JSON.stringify({ append: Boolean(append) }),
    });
    if (data.cancelled) return;
    await applyVideoList(data);
    if (isNew()) goStory("videos");
  } catch (err) {
    avisar("Deu problema", err.message);
  } finally {
    state.xfer = null;
    renderXfer();
    setStep();
    buttons.forEach((btn) => { btn.disabled = false; });
  }
}

function listenEvents() {
  let src = null;
  let retryMs = 1500;

  function connect() {
    if (src) {
      try { src.close(); } catch { /* ignore */ }
    }
    src = new EventSource("/api/events");
    src.onopen = () => { retryMs = 1500; };
    src.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.type === "log") {
        appendLog(data);
        if (state.session.uploading && !state.xfer) {
          renderXfer();
          $("xfer-detail").textContent = data.message;
        }
      }
      if (data.type === "progress" || data.type === "job" || data.type === "hello") {
        if (data.job) state.session.job = data.job;
        if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
        if (typeof data.uploading === "boolean") state.session.uploading = data.uploading;
        jobSummary();
        renderAulas();
        renderProjects();
        setStep();
      }
      if (data.type === "probe" && Array.isArray(data.aulas)) {
        state.aulas = mergeServerAulas(data.aulas);
        renderAulas();
      }
      if (data.type === "convert" || data.type === "hello") {
        if (data.convert) state.session.convert = data.convert;
        if (typeof data.converting === "boolean") state.session.converting = data.converting;
        atualizarConvertidos();
        renderConvert();
        renderAulas();
        setStep();
      }
    };
    src.onerror = () => {
      try { src.close(); } catch { /* ignore */ }
      src = null;
      window.setTimeout(connect, retryMs);
      retryMs = Math.min(10000, retryMs * 1.5);
      syncJobFromServer();
    };
  }

  connect();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") syncJobFromServer();
  });
  window.addEventListener("focus", () => syncJobFromServer());
}

// Espelha no estado local quais arquivos já têm versão comprimida pronta.
function atualizarConvertidos() {
  const items = ((state.session.convert || {}).items) || [];
  const mapa = state.session.convertidos || {};
  items.forEach((item) => {
    if (item.status === "pronto" && item.saida) mapa[item.arquivo] = item.saida;
  });
  state.session.convertidos = mapa;
}

$("login-form").onsubmit = (ev) => {
  ev.preventDefault();
  advanceFromLogin();
};
$("btn-logout").onclick = logout;
let cursoSearchTimer = 0;
$("curso-q").addEventListener("input", () => {
  filterCursosLocal();
  window.clearTimeout(cursoSearchTimer);
  cursoSearchTimer = window.setTimeout(() => { searchCursos(); }, 400);
});
$("curso-q").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    window.clearTimeout(cursoSearchTimer);
    searchCursos();
  }
});
if ($("cap-q")) {
  $("cap-q").addEventListener("input", renderCaps);
}
$("btn-cap-create").onclick = createCapitulo;
$("cap-ordem").addEventListener("input", () => { $("cap-ordem").dataset.touched = "1"; });
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === tab));
    $("tab-existente").classList.toggle("hidden", tab.dataset.tab !== "existente");
    $("tab-novo").classList.toggle("hidden", tab.dataset.tab !== "novo");
    if (tab.dataset.tab === "novo") sugerirOrdemCapitulo();
  };
});
$("btn-folder").onclick = () => pickFolder(state.aulas.length > 0);
$("btn-files").onclick = () => pickFiles(state.aulas.length > 0);
$("btn-add-more").onclick = () => pickFiles(true);
$("btn-add-folder").onclick = () => pickFolder(true);
$("file-input").onchange = () => fromInput($("file-input"), state.aulas.length === 0);
$("aulas").addEventListener("click", (ev) => {
  const remover = ev.target.closest("[data-remove]");
  if (remover && !remover.disabled) {
    removeAula(remover.dataset.remove).catch((err) => avisar("Não deu para remover", err.message));
    return;
  }
  const converter = ev.target.closest("[data-convert]");
  if (converter && !converter.disabled) {
    converterAulas([converter.dataset.convert]);
    return;
  }
  const cancelConv = ev.target.closest("[data-convert-cancel]");
  if (cancelConv) {
    cancelarConversao();
    return;
  }
  const ver = ev.target.closest("[data-preview]");
  if (ver) abrirPreview(ver.dataset.preview);
});
$("btn-convert-all").onclick = () => converterAulas(aulasGrandes().map((a) => a.arquivo));
$("xfer").addEventListener("click", () => {
  const job = state.session.job || {};
  if (job.id || (job.items || []).length) openJobDetails(job.id || "");
});
$("xfer").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    $("xfer").click();
  }
});
$("btn-new-upload").addEventListener("click", async (ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  if (fileUploadBusy()) {
    avisar("Aguarde", "Espere o upload de arquivo terminar antes de começar outro neste capítulo.");
    return;
  }
  const ok = await confirmar(
    "Novo envio",
    "Limpa a lista de vídeos e deixa o capítulo atual pronto para outra pasta. Projetos anteriores ficam na lista acima.",
    { ok: "Limpar e começar", cancelar: "Cancelar" },
  );
  if (!ok) return;
  try {
    const data = await api("/api/workspace/new", { method: "POST", body: "{}" });
    state.aulas = [];
    state.session.fonte = "";
    state.planoByFile = {};
    if (data.job) state.session.job = data.job;
    if (Array.isArray(data.jobs)) state.session.jobs = data.jobs;
    if (typeof data.uploading === "boolean") state.session.uploading = data.uploading;
    state.projectsFilter = "andamento";
    renderAulas();
    jobSummary();
    renderProjects();
    setStep();
    if (isNew()) goStory("videos");
  } catch (err) {
    avisar("Deu problema", err.message);
  }
});
document.querySelector(".projects-filters")?.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-projects-filter]");
  if (!btn) return;
  state.projectsFilter = btn.dataset.projectsFilter || "andamento";
  renderProjects();
});
$("projects-list").addEventListener("click", (ev) => {
  const open = ev.target.closest("[data-job]");
  if (open) {
    openJobDetails(open.dataset.job || "", {
      synthetic: open.dataset.synthetic === "1",
    });
    return;
  }
  const archive = ev.target.closest("[data-archive]");
  if (archive) {
    archiveProject(archive.dataset.archive || "");
    return;
  }
  const unarchive = ev.target.closest("[data-unarchive]");
  if (unarchive) {
    unarchiveProject(unarchive.dataset.unarchive || "");
    return;
  }
  const del = ev.target.closest("[data-delete]");
  if (del) {
    deleteProject(del.dataset.delete || "");
  }
});
$("btn-preview-keep").onclick = fecharPreview;
$("btn-preview-use").onclick = () => {
  const arquivo = $("modal-preview").dataset.arquivo;
  fecharPreview();
  if (arquivo) usarConvertido(arquivo);
};
$("modal-preview").querySelectorAll("[data-preview-close]").forEach((el) => {
  el.onclick = fecharPreview;
});
$("aulas").addEventListener("focusin", (ev) => {
  const input = ev.target.closest("[data-k=titulo]");
  if (input) openTitulo(input);
});
$("aulas").addEventListener("focusout", (ev) => {
  const input = ev.target.closest("[data-k=titulo], [data-k=ordem]");
  if (!input) return;
  const tr = input.closest("tr");
  if (!tr) return;
  if (input.matches("[data-k=titulo]")) closeTitulo(input);
  const arquivo = tr.dataset.arquivo;
  if (input.matches("[data-k=titulo]")) {
    captureEdit(arquivo, "titulo", input.value);
  } else {
    captureEdit(arquivo, "ordem", Number(input.value) || 1);
  }
  persistEditsDebounced();
});
$("aulas").addEventListener("keydown", (ev) => {
  if (ev.key !== "Enter" && ev.key !== "Escape") return;
  const input = ev.target.closest("[data-k=titulo]");
  if (!input) return;
  ev.preventDefault();
  input.blur();
  if (ev.key === "Enter") refreshPlan();
});
bindColResize();
$("btn-path").onclick = async () => {
  state.xfer = { title: "Lendo o caminho", detail: $("local-path").value, pct: null };
  renderXfer();
  try {
    const data = await api("/api/videos/path", {
      method: "POST",
      body: JSON.stringify({ path: $("local-path").value }),
    });
    await applyVideoList(data);
  } catch (err) {
    avisar("Deu problema", err.message);
  } finally {
    state.xfer = null;
    renderXfer();
    setStep();
  }
};
$("btn-send").onclick = sendVideos;
$("force").onchange = refreshPlan;
document.querySelectorAll("[data-next]").forEach((btn) => {
  btn.onclick = () => goNext(btn.dataset.next);
});
document.querySelectorAll("[data-back]").forEach((btn) => {
  btn.onclick = () => goView(btn.dataset.back);
});
document.querySelectorAll("#steps li").forEach((li) => {
  li.onclick = () => goView(li.dataset.step);
});
$("selecao").onclick = (ev) => {
  if (ev.target.closest("a")) return;
  if (ev.target.closest(".flyout")) return;
  const card = ev.target.closest("#selecao > div");
  if (!card) return;
  if (isNew()) {
    const story = card.dataset.story;
    if (story && canStory(story)) goStory(story);
    return;
  }
  if (ev.target.closest("#portal-menu")) return;
  if (card.dataset.jump) goView(card.dataset.jump);
};
bindDropTarget($("drop"));
bindDropTarget($("card-videos"), { needChapter: true });

document.querySelectorAll("#layout-toggle [data-layout]").forEach((btn) => {
  btn.onclick = () => applyLayout(btn.dataset.layout);
});
bindStoryHover();
placeLayoutDom();
state.story = currentStory();

stripTokenFromUrl();
bootstrap().catch((err) => {
  document.body.innerHTML = `<p style="padding:2rem;font-family:sans-serif">${escapeHtml(err.message)}</p>`;
});
listenEvents();
setInterval(() => {
  if (jobNeedsSync()) {
    syncJobFromServer();
    const job = (state.session && state.session.job) || {};
    const items = job.items || [];
    if (
      items.some((i) => i.status === "processando" || i.status === "salvando")
      || job.fase === "processando"
      || job.status === "processando"
    ) {
      refreshNivo();
    }
  }
}, 2000);
