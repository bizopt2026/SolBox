/* 작은 화면 보강. 모두 선택 사항이라 스크립트가 없어도 화면은 쓸 수 있다. */
(function () {
  "use strict";

  function hashId() {
    var h = location.hash.slice(1);
    try { h = decodeURIComponent(h); } catch (e) { /* 깨진 조각은 그대로 둔다 */ }
    return h;
  }

  // 좁은 화면 상단 메뉴: 현재 항목이 보이게 메뉴 안에서만 가로로 옮기고, 끝에 닿으면 끝 표시를 감춘다
  var topNav = document.querySelector(".topbar nav");
  if (topNav) {
    var syncNav = function () { topNav.classList.toggle("at-end", topNav.scrollLeft + topNav.clientWidth >= topNav.scrollWidth - 2); };
    var curItem = topNav.querySelector('[aria-current="page"]');
    if (curItem && topNav.scrollWidth > topNav.clientWidth) {
      topNav.scrollLeft += curItem.getBoundingClientRect().left - topNav.getBoundingClientRect().left - (topNav.clientWidth - curItem.offsetWidth) / 2;
    }
    topNav.addEventListener("scroll", syncNav);
    window.addEventListener("resize", syncNav);
    syncNav();
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

  // 구성안: 확인이 남은 장만 보기
  document.querySelectorAll(".remaining-toggle").forEach(function (btn) {
    btn.hidden = false;
    var KEY = "solbox.outline.remaining";
    var apply = function (on) {
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "전체 보기" : "남은 장만 보기";
      document.querySelectorAll(".slide-card").forEach(function (c) { c.hidden = on && !c.dataset.remaining; });
    };
    btn.addEventListener("click", function () {
      var on = btn.getAttribute("aria-pressed") !== "true";
      apply(on);
      try { sessionStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* 기억은 선택 사항 */ }
    });
    try { if (sessionStorage.getItem(KEY) === "1") apply(true); } catch (e) { /* 저장소를 못 쓰면 전체 보기 */ }
  });

  // 방향 카드: 출처 번호를 누르면 접힌 부분을 펼치고 그 출처로 이동한다
  document.querySelectorAll(".dir-card sup a").forEach(function (a) {
    a.addEventListener("click", function () {
      var t = document.getElementById(a.getAttribute("href").slice(1));
      for (var d = t; d; d = d.parentElement) { if (d.tagName === "DETAILS") d.open = true; }
    });
  });

  // 구성안: 모두 펼치기/접기, 펼친 장 기억
  var slideCards = Array.prototype.slice.call(document.querySelectorAll("details.slide-card"));
  if (slideCards.length) {
    var OPEN_KEY = "solbox.outline.open." + location.pathname;
    var saveOpen = function () {
      var ids = slideCards.filter(function (c) { return c.open; }).map(function (c) { return c.id; });
      try { sessionStorage.setItem(OPEN_KEY, JSON.stringify(ids)); } catch (e) { /* 기억은 선택 사항 */ }
    };
    // 기억해 둔 장을 다시 펼친다. 장 번호가 바뀐 처리(이동·삭제·추가, ?renumber=1)는 기억을 버리고 서버가 연 장만 남긴다
    var params = new URLSearchParams(location.search);
    if (!params.has("renumber")) {
      try {
        var saved = JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]");
        slideCards.forEach(function (c) { if (saved.indexOf(c.id) >= 0) c.open = true; });
      } catch (e) { /* 저장소를 못 쓰면 서버가 정한 상태 그대로 */ }
    }
    saveOpen();
    // 주소의 ?open=, ?renumber= 는 한 번 쓰고 지운다. 새로고침하면 기억해 둔 상태를 따른다
    if (params.has("open") || params.has("renumber")) {
      params.delete("open"); params.delete("renumber");
      var rest = params.toString();
      try { history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash); } catch (e) { /* 그대로 둔다 */ }
    }
    slideCards.forEach(function (c) { c.addEventListener("toggle", saveOpen); });
    document.querySelectorAll(".toggle-slides").forEach(function (box) {
      box.hidden = false;
      box.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b) return;
        slideCards.forEach(function (c) { c.open = b.dataset.open === "1"; });
        saveOpen();
      });
    });
    var revealSlide = function () {
      var c = document.getElementById(hashId());
      if (c && c.matches("details.slide-card")) c.open = true;
    };
    window.addEventListener("hashchange", revealSlide);
    revealSlide();
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
  // 올리기: 고른 파일 이름을 보여 주고, 끌어다 놓은 파일을 입력칸에 담는다
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector('input[type="file"]');
    var list = zone.querySelector(".dropzone-list");
    var hint = zone.querySelector(".dropzone-hint");
    if (!input) return;
    var show = function () {
      if (!list) return;
      list.textContent = "";
      Array.prototype.forEach.call(input.files, function (f) {
        var li = document.createElement("li");
        li.textContent = f.name;
        list.appendChild(li);
      });
    };
    input.addEventListener("change", show);
    if (hint) hint.hidden = false;
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function () { zone.classList.remove("over"); });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      if (e.dataTransfer && e.dataTransfer.files.length) {
        input.files = e.dataTransfer.files;
        show();
      }
    });
  });
})();
