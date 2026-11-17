"use strict";

const form = document.getElementById("query-form");
const results = document.getElementById("results");
const tree = document.getElementById("tree");
const pathSelect = document.getElementById("path");
const statusLabels = {active: "Действует", archived: "Архив", draft: "Черновик"};
let latestTreeRequest = 0;

function element(tag, text = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function docLink(value, title) {
  const link = element("a", title);
  link.href = "/docs/" + encodeURIComponent(value.doc_id) + "/" + encodeURIComponent(value.version);
  if (value.anchor) link.hash = encodeURIComponent(value.anchor);
  if (value.url) link.href = value.url;
  return link;
}

function description(card) {
  return [card.doc_id, "вер. " + card.version, statusLabels[card.status] ?? card.status, card.path].join(" · ");
}

async function loadTree() {
  const requestNumber = ++latestTreeRequest;
  const params = new URLSearchParams({
    include_archive: form.elements.include_archive.checked,
    include_drafts: form.elements.include_drafts.checked,
  });
  try {
    const response = await fetch("/api/sections?" + params);
    if (!response.ok) throw new Error("Дерево документов недоступно.");
    const data = await response.json();
    if (requestNumber !== latestTreeRequest) return;
    const selectedPath = pathSelect.value;
    pathSelect.replaceChildren(new Option("Все разделы", ""));
    tree.replaceChildren();
    for (const section of data.sections) {
      const branch = element("details");
      branch.append(element("summary", section.title));
      for (const subsection of section.subsections) {
        pathSelect.add(new Option(subsection.path, subsection.path));
        const leaf = element("details");
        leaf.append(element("summary", subsection.title));
        const documents = element("ul");
        for (const document of subsection.documents) {
          const item = element("li");
          item.append(docLink(document, document.title), element("span", " · " + description(document)));
          documents.append(item);
        }
        leaf.append(documents);
        branch.append(leaf);
      }
      tree.append(branch);
    }
    if ([...pathSelect.options].some(option => option.value === selectedPath)) pathSelect.value = selectedPath;
  } catch (error) {
    if (requestNumber === latestTreeRequest) tree.replaceChildren(element("p", error.message));
  }
}

function showResult(data, action) {
  results.replaceChildren();
  if (action === "ask") {
    results.append(element("h2", "Ответ"));
    const route = element("p", [data.route, data.reason, data.request_id].join(" · "));
    route.className = "meta";
    const answer = element("p", data.answer);
    answer.className = "answer";
    results.append(route, answer);
    if (data.sources.length) {
      const sources = element("ul");
      for (const source of data.sources) {
        const item = element("li");
        item.append(docLink(source, "[" + source.number + "] " + source.title + " · " + source.heading));
        item.append(element("span", " · " + description(source)));
        sources.append(item);
      }
      results.append(element("h3", "Источники"), sources);
    }
  }
  results.append(element("h2", "Найденные документы"));
  if (!data.cards.length) results.append(element("p", "Подходящие документы не найдены."));
  for (const card of data.cards) {
    const item = element("article");
    const title = element("h3");
    title.append(docLink(card, card.title));
    const meta = element("p", description(card));
    meta.className = "meta";
    item.append(title, meta, element("p", card.excerpt));
    results.append(item);
  }
}

let activeRequest;
form.addEventListener("submit", async event => {
  event.preventDefault();
  activeRequest?.abort();
  const controller = new AbortController();
  activeRequest = controller;
  const values = form.elements;
  const payload = {
    query: values.query.value,
    path: values.path.value || null,
    version: values.version.value.trim() || null,
    include_archive: values.include_archive.checked,
    include_drafts: values.include_drafts.checked,
    private: values.private.checked,
  };
  const action = event.submitter?.value === "ask" ? "ask" : "search";
  results.replaceChildren(element("p", "Выполняется запрос…"));
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const response = await fetch("/api/" + action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail ?? "Запрос не выполнен.");
    if (activeRequest === controller) showResult(data, action);
  } catch (error) {
    if (activeRequest === controller) {
      results.replaceChildren(element("p", error.name === "AbortError" ? "Время ожидания истекло." : error.message));
    }
  } finally {
    clearTimeout(timer);
  }
});

form.elements.include_archive.addEventListener("change", loadTree);
form.elements.include_drafts.addEventListener("change", loadTree);
loadTree();
