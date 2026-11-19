/* 작은 화면 보강. 모두 선택 사항이라 스크립트가 없어도 화면은 쓸 수 있다. */
(function () {
  "use strict";

  function hashId() {
    var h = location.hash.slice(1);
    try { h = decodeURIComponent(h); } catch (e) { /* 깨진 조각은 그대로 둔다 */ }
    return h;
  }

  // 체크 상자 목록: 선택 수 표시와 걸러 보기
  document.querySelectorAll(".picker").forEach(function (box) {
    var count = box.querySelector("[data-count]");
    var items = box.querySelectorAll(".picker-list label");
    var filter = box.querySelector(".picker-filter");
    box.addEventListener("change", function () {
      var n = box.querySelectorAll(".picker-list input:checked").length;
      if (count) count.textContent = n ? " · " + n + "개 선택" : "";
    });
    if (!filter) return;
    filter.hidden = false;
    filter.addEventListener("keydown", function (e) {
      if (e.key === "Enter") e.preventDefault(); // 걸러 보기에서 Enter 로 폼이 저장되지 않게
    });
    filter.addEventListener("input", function () {
      var q = filter.value.trim().toLowerCase();
      items.forEach(function (l) {
        var name = l.querySelector(".pname");
        l.hidden = q !== "" && (name ? name.textContent : l.textContent).toLowerCase().indexOf(q) < 0;
      });
    });
  });

  // 제안 상세: 주소 조각의 구역을 펼친다
  var bar = document.querySelector(".stepbar ol");
  if (bar) {
    var sections = { target: 1, directions: 1, chat: 1, outline: 1, deck: 1 };
    var reveal = function (id) {
      if (!Object.prototype.hasOwnProperty.call(sections, id)) return;
      var el = document.getElementById(id);
      if (!el) return;
      for (var d = el; d; d = d.parentElement) { if (d.tagName === "DETAILS") d.open = true; }
      el.scrollIntoView();
    };
    window.addEventListener("hashchange", function () { reveal(hashId()); });
    bar.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        history.replaceState(null, "", a.getAttribute("href"));
        reveal(a.getAttribute("href").slice(1));
      });
    });
    var cur = bar.querySelector(".current");
    if (cur) bar.scrollLeft += cur.getBoundingClientRect().left - bar.getBoundingClientRect().left - (bar.clientWidth - cur.offsetWidth) / 2;
    reveal(hashId());
  }

  // 리서치 상세: 모두 펼치기/접기, 분야 탭
  document.querySelectorAll(".toggle-all").forEach(function (box) {
    box.hidden = false;
    box.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      var target = document.getElementById(box.dataset.target);
      if (!b || !target) return;
      target.querySelectorAll("details.trend-card").forEach(function (d) { d.open = b.dataset.open === "1"; });
    });
  });
  var wrap = document.getElementById("area-tabs");
  if (wrap) {
    var tablist = wrap.querySelector(".tabs");
    var tabs = Array.prototype.slice.call(wrap.querySelectorAll(".tabs a"));
    var panels = Array.prototype.slice.call(wrap.querySelectorAll(".tabpanel"));
    var ids = panels.map(function (p) { return p.id; });
    tablist.setAttribute("role", "tablist");
    tabs.forEach(function (t, i) {
      t.setAttribute("role", "tab");
      t.setAttribute("aria-controls", ids[i]);
      panels[i].setAttribute("role", "tabpanel");
      panels[i].setAttribute("aria-labelledby", t.id);
    });
    var select = function (id, focus) {
      var at = ids.indexOf(id);
      if (at < 0) return;
      tabs.forEach(function (t, i) {
        t.setAttribute("aria-selected", i === at ? "true" : "false");
        t.tabIndex = i === at ? 0 : -1;
        panels[i].hidden = i !== at;
      });
      if (focus) tabs[at].focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function (e) {
        e.preventDefault();
        history.replaceState(null, "", "#" + ids[i]);
        select(ids[i]);
      });
      t.addEventListener("keydown", function (e) {
        var step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        e.preventDefault();
        var next = (i + step + ids.length) % ids.length;
        history.replaceState(null, "", "#" + ids[next]);
        select(ids[next], true);
      });
    });
    window.addEventListener("hashchange", function () { select(hashId()); });
    select(ids.indexOf(hashId()) >= 0 ? hashId() : ids[0]);
  }
})();
