// Profile owns its presentation; shared services/actions are injected by the page adapter.
export function renderProfile(data, { $, safeCount, displayCount, escapeHtml, ago, socialWrite, followFromDashboard }) {
function profileUrl(username) { return `https://civitai.red/user/${encodeURIComponent(username || "")}`; }
function followerText(creator) { return creator.followers === null || creator.followers === undefined ? "" : ` · ${displayCount(creator.followers)} followers`; }
function emergingPill(creator) { return creator.emerging ? '<span class="pill emerging" title="Fewer than 1,000 followers">EMERGING</span>' : ""; }
// Widths are applied through CSSOM afterwards because the page's Content Security
// Policy forbids inline style attributes.
function barRow(label, value, fraction) { return `<div class="bar-row"><span class="bar-label">${escapeHtml(label)}</span><span class="bar-track"><span class="bar-fill" data-fill="${Math.max(0, Math.min(100, fraction * 100)).toFixed(1)}"></span></span><span class="bar-value">${escapeHtml(value)}</span></div>`; }
function applyBarWidths(container) { container.querySelectorAll(".bar-fill").forEach(fill => { fill.style.width = `${fill.dataset.fill}%`; }); }
function rankList(items, empty, extra = "") { return items.length ? `<div class="rank-list ${extra}">${items.join("")}</div>` : `<p class="empty-note">${escapeHtml(empty)}</p>`; }
function metricCard(label, value, hint, accent) { return `<div class="metric-card${accent ? " accent" : ""}"><span class="label">${escapeHtml(label)}</span><span class="value">${escapeHtml(displayCount(value))}</span><span class="hint">${escapeHtml(hint)}</span></div>`; }
// Categorical slots validated against this dark surface: adjacent-pair separation
// holds for normal vision and for colour-vision deficiency. Fixed order, never cycled.
const reactionColors = { Like: "#3987e5", Heart: "#d95926", Laugh: "#199e70", Cry: "#c98500", Dislike: "#9085e9" };
const DONUT_RADIUS = 38, DONUT_GAP = 2;
function donut(mix, totalRecords) {
  const circumference = 2 * Math.PI * DONUT_RADIUS;
  const drawn = mix.filter(entry => entry.count > 0);
  let offset = 0;
  const arcs = drawn.map(entry => {
    const raw = (entry.count / (totalRecords || 1)) * circumference;
    // Keep a hairline for tiny shares; the legend beside it carries the exact count.
    const length = drawn.length > 1 ? Math.max(raw - DONUT_GAP, 1) : circumference;
    const arc = `<circle class="donut-arc" cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="${reactionColors[entry.reaction] || "#9085e9"}" stroke-width="13" stroke-dasharray="${length.toFixed(2)} ${(circumference - length).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"><title>${escapeHtml(entry.reaction)}: ${displayCount(entry.count)} (${entry.percent}%)</title></circle>`;
    offset += raw;
    return arc;
  }).join("");
  const chart = drawn.length
    ? `<svg class="donut" viewBox="0 0 100 100" role="img" aria-label="Reaction mix"><g transform="rotate(-90 50 50)"><circle cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="#2b2d34" stroke-width="13"></circle>${arcs}</g></svg>`
    : `<svg class="donut" viewBox="0 0 100 100" role="img" aria-label="No reactions yet"><circle cx="50" cy="50" r="${DONUT_RADIUS}" fill="none" stroke="#2b2d34" stroke-width="13"></circle></svg>`;
  const legend = mix.map(entry => `<div class="donut-legend-row"><span class="swatch" data-swatch="${escapeHtml(entry.reaction)}"></span><span class="donut-legend-name">${escapeHtml(entry.reaction)}</span><span class="donut-legend-value">${displayCount(entry.count)} · ${entry.percent}%</span></div>`).join("");
  return `<div class="metric-card reaction-card"><span class="label">Reaction mix</span><div class="donut-body"><div class="donut-wrap">${chart}<span class="donut-centre"><b>${displayCount(totalRecords)}</b><small>reactions</small></span></div><div class="donut-legend">${legend}</div></div></div>`;
}

  const body = $("discoveryBody"), has = !!data.hasData;
  body.classList.toggle("hidden", !has);
  $("resetDiscovery").classList.toggle("hidden", !has);
  $("syncDiscovery").textContent = has ? "Refresh from Civitai" : "Analyse my reactions";
  if (!has) return;
  const total = safeCount(data.reactedImages);
  $("summaryRow").innerHTML = [
    metricCard("Creators you follow", data.followedCreators, "Exact count from your Civitai account", true),
    metricCard("Images you reacted to", total, "Your complete reaction history"),
    metricCard("Creators you reacted to", data.creatorsReactedTo, "Distinct artists in that history"),
    metricCard("Not yet followed", data.creatorsNotFollowed, `Reacted to ${data.worthFollowingThreshold || 10}+ images, but you do not follow them`),
    donut(data.reactionMix || [], data.reactionRecords),
  ].join("");
  let fingerprintPanel = $("creativeFingerprint");
  if (!fingerprintPanel) {
    fingerprintPanel = document.createElement("section");
    fingerprintPanel.id = "creativeFingerprint";
    fingerprintPanel.className = "panel wide fingerprint-panel";
    fingerprintPanel.innerHTML = `<div class="panel-head"><div><h3>Your creative fingerprint</h3><p id="fingerprintNote" class="panel-note"></p></div></div><div class="fingerprint-grid"><div><h4>Strong visual signals</h4><div id="fingerprintTags" class="fingerprint-tags"></div></div><div><h4>Model signals</h4><div id="fingerprintModels"></div></div></div>`;
    document.querySelector(".panel-grid").prepend(fingerprintPanel);
  }
  const fingerprint = data.recentWork || {};
  fingerprintPanel.classList.toggle("hidden", !safeCount(fingerprint.images));
  if (safeCount(fingerprint.images)) {
    const coverage = fingerprint.complete ? "your public upload history" : "the uploads collected so far";
    $("fingerprintNote").textContent = `Built from ${displayCount(fingerprint.images)} images across ${coverage}. Future refreshes stop at the first known upload and add only new images. Strong tags appear in at least 10% of the archive and occur at least 50% more often here than in the Civitai comparison sample; generic and one-off tags are left out.`;
    $("fingerprintTags").innerHTML = (fingerprint.strongTags || []).map(tag =>
      `<span class="fingerprint-tag"><b>${escapeHtml(tag.name)}</b><small>${displayCount(tag.images)} of ${displayCount(fingerprint.images)}${tag.lift ? ` · ×${tag.lift} distinctive` : ""}</small></span>`).join("") || '<span class="empty-note">More comparison data is needed to identify strong tags.</span>';
    $("fingerprintModels").innerHTML = rankList((fingerprint.models || []).slice(0, 8).map(model => {
      const version = model.versionName && model.versionName !== model.modelName ? ` · ${model.versionName}` : "";
      const label = model.modelName ? `${model.modelName}${version}` : (model.versionName || `Model version ${model.id}`);
      const name = model.modelId
        ? `<a href="https://civitai.red/models/${escapeHtml(model.modelId)}?modelVersionId=${escapeHtml(model.id)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`
        : escapeHtml(label);
      return `<div class="rank-item"><span class="rank-name">${name}</span><span class="rank-value">${displayCount(model.images)} images · ${model.percent}%</span></div>`;
    }), "No model information was published with these images.");
  }
  const topTagMax = Math.max(1, ...(data.topTags || []).map(tag => tag.images));
  $("topTags").innerHTML = (data.topTags || []).map(tag => barRow(tag.name, `${displayCount(tag.images)} · ${tag.percent}%`, tag.images / topTagMax)).join("");
  applyBarWidths($("topTags"));
  $("distinctiveNote").textContent = data.baselineImages ? `Tags you react to far more often than a ${displayCount(data.baselineImages)}-image sample of Civitai shows. “×4” means four times the usual rate.` : "A Civitai comparison sample is not available yet.";
  $("distinctiveTags").innerHTML = rankList((data.distinctiveTags || []).map(tag => `<div class="rank-item"><span class="rank-name">${escapeHtml(tag.name)}</span><span class="rank-value"><span class="pill lift">×${tag.lift}</span> ${displayCount(tag.images)} images</span></div>`), "No tag stands out from the sample yet.");
  $("topCreators").innerHTML = rankList((data.topCreators || []).map(creator => `<div class="rank-item"><span class="rank-name"><a href="${escapeHtml(profileUrl(creator.username))}" target="_blank" rel="noopener">@${escapeHtml(creator.username)}</a>${creator.following ? '<span class="pill">FOLLOWING</span>' : ""}${emergingPill(creator)}</span><span class="rank-value">${displayCount(creator.images)} images${followerText(creator)}</span></div>`), "No creators recorded yet.");
  const notFollowed = data.reactedNotFollowed || [];
  const worthThreshold = data.worthFollowingThreshold || 10;
  const heartThreshold = data.galleryHeartThreshold || 5;
  $("notFollowed").innerHTML = rankList(notFollowed.map(creator => `<div class="rank-item" data-user-id="${escapeHtml(creator.id)}" data-username="${escapeHtml(creator.username)}"><span class="rank-name"><a href="${escapeHtml(profileUrl(creator.username))}" target="_blank" rel="noopener">@${escapeHtml(creator.username)}</a>${emergingPill(creator)}</span><span class="rank-value">${displayCount(creator.images)} images${followerText(creator)}</span><button class="follow-button" ${socialWrite ? "" : "disabled"} title="${socialWrite ? "" : "Civitai did not grant follow and reaction access."}">+ Follow</button></div>`), `No creator you react to ${worthThreshold} or more times goes unfollowed.`, "dense");
  $("notFollowed").querySelectorAll(".rank-item").forEach(row => { row.querySelector(".follow-button").onclick = () => followFromDashboard(row); });
  $("notFollowedNote").textContent = notFollowed.length
    ? `Creators whose work you have reacted to on ${worthThreshold} or more distinct images without following. Showing the top ${notFollowed.length} of ${displayCount(data.creatorsNotFollowed)}. A ♥ in the daily gallery marks unfollowed artists at the broader ${heartThreshold}+ image threshold.`
    : `Creators you've reacted to on ${worthThreshold} or more distinct images without following.`;
  // Suggestions moved into the For You feed, where browsing happens.
  const age = data.lastSyncAt ? (Date.now() - new Date(data.lastSyncAt).getTime()) / 1000 : 0;
  const when = age < 120 ? "moments ago" : ago(data.lastSyncAt);
  $("discoverySubtitle").textContent = `Updated ${when} · ${displayCount(total)} reactions · ${displayCount(data.distinctTags)} tags`;

}
