const $ = (selector, root = document) => root.querySelector(selector);
const state = { catalog: null, campaigns: [], streams: [], drafts: new Map(), busy: false, selectedCampaignId: null };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.style.background = error ? "#8e2633" : "#101925";
  el.classList.add("visible");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => el.classList.remove("visible"), 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail || `Ошибка HTTP ${response.status}`);
  return data;
}

function fillSelect(select, items, placeholder) {
  select.replaceChildren(new Option(placeholder, ""));
  for (const item of items) select.add(new Option(`${item.name} · ID ${item.id}`, item.id));
}

function renderOfferSuggestions(query) {
  const list = $("#offer-options");
  if (!state.catalog) return;
  const needle = query.trim().toLowerCase();
  const matches = state.catalog.offers.filter((offer) => !needle || String(offer.id).includes(needle) || String(offer.name).toLowerCase().includes(needle)).slice(0, 12);
  list.replaceChildren();
  for (const offer of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.innerHTML = `<span>${escapeHtml(offer.name)}</span><small>ID ${escapeHtml(offer.id)}</small>`;
    button.addEventListener("click", () => {
      const input = $("#create-offer");
      input.value = `${offer.name} (#${offer.id})`;
      input.dataset.offerId = String(offer.id);
      list.classList.add("hidden");
    });
    list.append(button);
  }
  list.classList.toggle("hidden", matches.length === 0);
}

async function loadCatalog() {
  state.catalog = await api("/api/catalog");
  fillSelect($("#domain-select"), state.catalog.domains, "Выберите домен");
  fillSelect($("#group-select"), state.catalog.groups, "Выберите группу");
  fillSelect($("#source-select"), state.catalog.traffic_sources, "Выберите источник");
  renderOfferSuggestions("");
  $("#offer-options").classList.add("hidden");
  $("#prefix").textContent = state.catalog.campaign_prefix;
  renderOfferLibrary();
}

async function loadHealth() {
  const health = await api("/api/health");
  $("#health").textContent = health.keitaro_configured ? "● Keitaro настроен" : "● Укажите API-ключ в .env";
  $("#health").style.color = health.keitaro_configured ? "#84e3bb" : "#ffd083";
}

async function loadCampaigns() {
  state.campaigns = await api("/api/campaigns");
  const select = $("#campaign-select");
  select.replaceChildren(new Option("Выберите кампанию", ""));
  for (const campaign of state.campaigns) {
    select.add(new Option(`${campaign.name} · #${campaign.id}${campaign.state ? ` · ${campaign.state}` : ""}`, campaign.id));
  }
}

function streamDraft(stream) {
  if (!state.drafts.has(stream.id)) {
    state.drafts.set(stream.id, {
      originalVersion: stream.version,
      originalOffers: stream.offers.map((offer) => ({ ...offer })),
      offers: stream.offers.map((offer) => ({ ...offer, removed: false })),
      dirty: false,
    });
  }
  return state.drafts.get(stream.id);
}

function normalizedRows(rows) {
  return rows.map((offer) => [Number(offer.offer_id), Number(offer.share), String(offer.state || "active")])
    .sort((a, b) => a[0] - b[0]);
}

function rebalanceDraft(stream, draft) {
  const available = draft.offers.filter((offer) => !offer.removed);
  const base = available.length ? Math.floor(100 / available.length) : 0;
  const remainder = available.length ? 100 % available.length : 0;
  available.forEach((offer, index) => { offer.share = base + (index >= available.length - remainder ? 1 : 0); });
  draft.offers.filter((offer) => offer.removed).forEach((offer) => { offer.share = 0; });
  draft.dirty = JSON.stringify(normalizedRows(available)) !== JSON.stringify(normalizedRows(draft.originalOffers));
}

function renderOfferLibrary() {
  const list = $("#offer-library-list");
  if (!list || !state.catalog) return;
  const needle = String($("#offer-search")?.value || "").trim().toLowerCase();
  const offers = state.catalog.offers.filter((offer) => !needle || String(offer.id).includes(needle) || String(offer.name).toLowerCase().includes(needle));
  list.innerHTML = offers.length
    ? offers.map((offer) => `<div class="library-offer"><span>${escapeHtml(offer.name)}</span><small>ID ${escapeHtml(offer.id)}</small></div>`).join("")
    : `<p class="muted">Совпадений нет.</p>`;
}

function renderStreams() {
  const panel = $("#stream-panel");
  if (!state.streams.length) {
    panel.innerHTML = `<div class="muted">В этой кампании потоки не найдены.</div>`;
    return;
  }
  panel.innerHTML = state.streams.map((stream) => {
    if (!stream.editable) {
      return `<article class="stream-card readonly"><div class="stream-heading"><strong>${escapeHtml(stream.name)} (#${stream.id})</strong><span>${escapeHtml(stream.type)} · только просмотр</span></div><p class="muted">${escapeHtml(stream.edit_reason || "В этом потоке нет списка офферов для редактирования.")}</p></article>`;
    }
    const draft = streamDraft(stream);
    const availableCount = draft.offers.filter((offer) => !offer.removed).length;
    const rows = draft.offers.map((offer) => `<tr class="${offer.removed ? "removed-row" : ""}"><td>${escapeHtml(offer.name)}</td><td>#${offer.offer_id}</td><td class="share">${offer.share}%</td><td><button class="${offer.removed ? "restore-button" : "remove-button"}" data-action="${offer.removed ? "restore" : "remove"}" data-offer-id="${offer.offer_id}" type="button">${offer.removed ? "↶ Вернуть" : "Удалить"}</button></td></tr>`).join("");
    const existingIds = new Set(draft.offers.map((offer) => offer.offer_id));
    const addOptions = state.catalog.offers.filter((offer) => !existingIds.has(Number(offer.id)));
    const controls = draft.dirty
      ? `<div class="draft-actions"><button class="primary push-button" data-action="push" type="button">PUSH TO KT</button><button class="secondary" data-action="cancel" type="button">CANCEL</button><span class="draft-note">Изменения пока только в черновике</span></div>`
      : "";
    return `<article class="stream-card" data-stream-id="${stream.id}"><div class="stream-heading"><strong>Stream: ${escapeHtml(stream.name)} (#${stream.id})</strong><span>${escapeHtml(stream.type)} · ${draft.offers.length} оффер(а)</span></div>${controls}<table class="offer-table"><thead><tr><th>Offer</th><th>ID</th><th>Share</th><th>Actions</th></tr></thead><tbody>${rows || `<tr><td colspan="4" class="muted">В потоке пока нет офферов.</td></tr>`}</tbody></table><div class="sum-row">Доля активных: ${draft.offers.filter((offer) => !offer.removed).reduce((sum, offer) => sum + offer.share, 0)}%</div><form class="stream-add-form" data-action="add-form"><select data-add-select required><option value="">Выберите оффер для добавления</option>${addOptions.map((offer) => `<option value="${Number(offer.id)}">${escapeHtml(offer.name)} · ID ${escapeHtml(offer.id)}</option>`).join("")}</select><button class="secondary" data-action="add" type="submit" ${addOptions.length ? "" : "disabled"}>ADD</button></form>${availableCount <= 1 ? `<small class="field-hint">В потоке должен остаться хотя бы один оффер.</small>` : ""}</article>`;
  }).join("");
}

async function loadStreams(campaignId) {
  state.streams = await api(`/api/campaigns/${campaignId}/streams`);
  state.drafts.clear();
  state.selectedCampaignId = Number(campaignId);
  const link = $("#view-in-keitaro");
  link.href = state.catalog?.admin_campaign_url ? `${state.catalog.admin_campaign_url}${campaignId}` : "#";
  link.classList.toggle("disabled-link", !campaignId);
  renderStreams();
}

function hasDirtyDrafts() {
  return [...state.drafts.values()].some((draft) => draft.dirty);
}

function changeDraft(stream, action, offerId) {
  const draft = streamDraft(stream);
  const offer = draft.offers.find((row) => Number(row.offer_id) === Number(offerId));
  if (action === "remove" && offer) {
    if (draft.offers.filter((row) => !row.removed).length <= 1) return toast("Нельзя убрать последний активный оффер", true);
    offer.removed = true;
  } else if (action === "restore" && offer) {
    offer.removed = false;
  } else if (action === "add") {
    const catalogOffer = state.catalog.offers.find((row) => Number(row.id) === Number(offerId));
    if (!catalogOffer || draft.offers.some((row) => Number(row.offer_id) === Number(offerId))) return;
    draft.offers.push({ offer_id: Number(catalogOffer.id), name: catalogOffer.name, share: 0, state: "active", removed: false });
  }
  rebalanceDraft(stream, draft);
  renderStreams();
}

async function pushDraft(stream, draft) {
  const campaignId = Number($("#campaign-select").value);
  const offerIds = draft.offers.filter((offer) => !offer.removed).map((offer) => Number(offer.offer_id));
  if (!offerIds.length) return toast("В потоке должен остаться хотя бы один оффер", true);
  state.busy = true;
  try {
    await api(`/api/campaigns/${campaignId}/streams/${stream.id}/offers/push`, {
      method: "POST", body: JSON.stringify({ offer_ids: offerIds, expected_version: draft.originalVersion }),
    });
    toast("Изменения отправлены в Keitaro");
    await loadStreams(campaignId);
    await loadOperations();
  } catch (error) {
    toast(error.message, true);
    if (error.message.toLowerCase().includes("изменился")) await loadStreams(campaignId);
  } finally { state.busy = false; }
}

async function loadOperations() {
  const rows = await api("/api/operations");
  const panel = $("#operations");
  if (!rows.length) { panel.textContent = "Операции появятся после первого действия."; return; }
  panel.innerHTML = rows.map((row) => {
    const detail = row.summary?.name || row.summary?.action || row.action;
    return `<div class="operation"><span>${escapeHtml(detail)}${row.campaign_id ? ` · #${row.campaign_id}` : ""}</span><strong class="status-${escapeHtml(row.status)}">${escapeHtml(row.status)}</strong></div>`;
  }).join("");
}

$("#campaign-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const result = $("#create-result");
  const body = Object.fromEntries(new FormData(form).entries());
  body.country_codes = String(body.country_codes || "").replaceAll(";", ",").split(",").map((code) => code.trim()).filter(Boolean);
  const offerInput = $("#create-offer").value.trim();
  const selectedOffer = state.catalog?.offers.find((offer) => String(offer.id) === $("#create-offer").dataset.offerId && offerInput === `${offer.name} (#${offer.id})`);
  if (!selectedOffer) { toast("Выберите оффер из подсказок по названию или ID", true); return; }
  body.offer_id = Number(selectedOffer.id);
  for (const key of ["domain_id", "group_id", "traffic_source_id"]) body[key] = Number(body[key]);
  if (!window.confirm(`Создать в подключённом Keitaro тестовую кампанию «${body.name}» с выбранными доменом, группой, источником, GEO-потоком и оффером?`)) return;
  state.busy = true; button.disabled = true; result.className = "notice";
  result.textContent = "Создаём выключенную кампанию и проверяем оба потока…";
  try {
    const created = await api("/api/campaigns", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(body) });
    result.className = "notice";
    result.textContent = `Готово: ${created.name} · кампания #${created.campaign_id}, потоки ${created.stream_ids.join(" и ")}. Статус: ${created.state}.`;
    await Promise.all([loadCampaigns(), loadOperations()]);
    $("#campaign-select").value = String(created.campaign_id);
    await loadStreams(created.campaign_id);
  } catch (error) {
    result.className = "notice error";
    result.textContent = `${error.message} Проверьте состояние кампании в Keitaro: при неопределённом ответе на включение приложение пытается выключить её, но при сетевой ошибке итог нужно подтвердить в панели.`;
    await loadOperations().catch(() => {});
  } finally { state.busy = false; button.disabled = false; }
});

$("#campaign-select").addEventListener("change", async (event) => {
  const id = Number(event.target.value);
  if (hasDirtyDrafts() && !window.confirm("Отменить несохранённые изменения редактора и сменить кампанию?")) {
    event.target.value = String(state.selectedCampaignId || "");
    return;
  }
  try { if (id) await loadStreams(id); else { state.streams = []; state.drafts.clear(); state.selectedCampaignId = null; renderStreams(); } }
  catch (error) { toast(error.message, true); }
});

$("#stream-panel").addEventListener("submit", (event) => {
  const form = event.target.closest("[data-action=add-form]");
  if (!form) return;
  event.preventDefault();
  const card = form.closest("[data-stream-id]");
  const stream = state.streams.find((row) => row.id === Number(card.dataset.streamId));
  const offerId = Number($("[data-add-select]", form).value);
  if (stream && offerId) changeDraft(stream, "add", offerId);
});

$("#stream-panel").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || button.dataset.action === "add") return;
  const card = button.closest("[data-stream-id]");
  const stream = state.streams.find((row) => row.id === Number(card?.dataset.streamId));
  const draft = stream && streamDraft(stream);
  if (!stream || !draft || state.busy) return;
  const { action } = button.dataset;
  if (action === "push") await pushDraft(stream, draft);
  else if (action === "cancel") { state.drafts.delete(stream.id); renderStreams(); }
  else if (action === "remove" || action === "restore") changeDraft(stream, action, Number(button.dataset.offerId));
});

$("#create-offer").addEventListener("input", (event) => {
  delete event.currentTarget.dataset.offerId;
  renderOfferSuggestions(event.currentTarget.value);
});
$("#create-offer").addEventListener("focus", (event) => renderOfferSuggestions(event.currentTarget.value));
document.addEventListener("click", (event) => {
  if (!event.target.closest(".autocomplete")) $("#offer-options").classList.add("hidden");
});

$("#fetch-streams").addEventListener("click", async () => {
  const campaignId = Number($("#campaign-select").value);
  if (!campaignId) return toast("Сначала выберите кампанию", true);
  if (hasDirtyDrafts() && !window.confirm("Загрузить данные из Keitaro и отменить несохранённые изменения?")) return;
  try { await loadStreams(campaignId); await loadOperations(); toast("Потоки загружены из Keitaro"); }
  catch (error) { toast(error.message, true); }
});

$("#toggle-offers").addEventListener("click", (event) => {
  const library = $("#offer-library");
  library.classList.toggle("hidden");
  event.currentTarget.textContent = library.classList.contains("hidden") ? "SHOW ALL OFFERS" : "HIDE ALL OFFERS";
  renderOfferLibrary();
});
$("#offer-search").addEventListener("input", renderOfferLibrary);

Promise.all([loadHealth(), loadCatalog(), loadCampaigns(), loadOperations()]).catch((error) => {
  $("#health").textContent = "Не удалось подключиться к Keitaro";
  $("#health").style.color = "#ff9c9c";
  toast(error.message, true);
});
