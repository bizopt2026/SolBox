/* 작은 화면 보강. 모두 선택 사항이라 스크립트가 없어도 화면은 쓸 수 있다. */
(function () {
  "use strict";

  function hashId() {
    var h = location.hash.slice(1);
    try { h = decodeURIComponent(h); } catch (e) { /* 깨진 조각은 그대로 둔다 */ }
    return h;
  }

  // 좁은 화면 위쪽 메뉴: 현재 항목이 보이게 메뉴 안에서만 가로로 옮기고, 끝에 닿으면 끝 표시를 감춘다
  var topNav = document.querySelector(".side-nav");
  if (topNav) {
    var syncNav = function () { topNav.classList.toggle("at-end", topNav.scrollLeft + topNav.clientWidth >= topNav.scrollWidth - 2); };
    var placeNav = function () {
      var curItem = topNav.querySelector('[aria-current="page"]');
      if (curItem && topNav.scrollWidth > topNav.clientWidth) {
        topNav.scrollLeft += curItem.getBoundingClientRect().left - topNav.getBoundingClientRect().left - (topNav.clientWidth - curItem.offsetWidth) / 2;
      }
    };
    placeNav();
    topNav.addEventListener("scroll", syncNav);
    window.addEventListener("resize", syncNav);
    syncNav();
  }

  // 옆으로 미는 줄(단계 막대·제안 단계·구분 탭): 현재 항목이 보이게 그 줄 안에서만 옮기고, 더 밀 곳이 있는 쪽 가장자리를 흐리게 한다 (HTMX 로 바뀐 줄도)
  var SCROLLERS = ".stagebar .steps, .stepbar ol, .tools > .seg, .tool-more-body .seg, .tablewrap";
  function initScrollers(root) {
    (root || document).querySelectorAll(SCROLLERS).forEach(function (el) {
      if (el.dataset.sf) return;
      el.dataset.sf = "1";
      el.classList.add("fade-x");
      var sync = function () {
        el.classList.toggle("can-left", el.scrollLeft > 2);
        el.classList.toggle("can-right", el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
      };
      var cur = el.querySelector('[aria-current], .current');
      if (cur && el.scrollWidth > el.clientWidth) {
        el.scrollLeft += cur.getBoundingClientRect().left - el.getBoundingClientRect().left - (el.clientWidth - cur.offsetWidth) / 2;
      }
      el.addEventListener("scroll", sync);
      window.addEventListener("resize", sync);
      sync();
    });
  }
  initScrollers();
  document.body.addEventListener("htmx:oobAfterSwap", function (e) { initScrollers(e.target.parentNode || document); });
  document.body.addEventListener("htmx:afterSwap", function (e) { initScrollers(e.target); });

  // 전체 검색: "/" 를 누르면 검색칸으로 (입력칸에 글을 쓰는 중에는 그냥 "/" 가 입력된다)
  var globalSearch = document.getElementById("global-search");
  if (globalSearch) {
    document.addEventListener("keydown", function (e) {
      var cmdK = (e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K"); // Ctrl+K(⌘K): 입력 중에도 검색칸으로 (UX 개선 2026-11-26)
      if (cmdK && !e.defaultPrevented) { e.preventDefault(); globalSearch.focus(); globalSearch.select(); return; }
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
      if (e.isComposing || e.keyCode === 229) return; // 한글 조합 중에는 가로채지 않는다
      var t = e.target, tag = t && t.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
      e.preventDefault();
      globalSearch.focus();
      globalSearch.select();
    });
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

  // 제안 대화: 답을 기다리는 동안은 맨 끝 말풍선(#chat-tail)만 갱신된다. 읽던 자리를 존중한다:
  // 사용자가 끝에서 40px 안에 있었을 때만 끝으로 따라가고, 위로 올려 읽는 중이면 그대로 둔다.
  // 답이 끝나면(대화 구역 전체 교체) 이전 대화 펼침·쓰던 글을 되살리고, 따라가던 중이면 새 답변의 첫머리가 보이게 한다.
  (function () {
    var NEAR = 40;
    var box = function () { return document.querySelector("[data-chat-scroll]"); };
    var tailState = function () {
      var t = document.getElementById("chat-tail");
      return t ? t.dataset.state : null;
    };
    var waiting = function (state) { return state === "pending" || state === "running"; };
    var prev = tailState();
    var atBottom = function (b) { return b.scrollHeight - b.scrollTop - b.clientHeight <= NEAR; };
    var pin = function () { var b = box(); if (b) b.scrollTop = b.scrollHeight; };
    pin();
    var chatSection = document.getElementById("chat");
    if (chatSection) chatSection.addEventListener("toggle", function () { if (chatSection.open) pin(); });
    var snap = null;
    document.body.addEventListener("htmx:beforeSwap", function () {
      var b = box();
      if (!b) { snap = null; return; }
      var ta = document.getElementById("chat-message");
      snap = { follow: atBottom(b), top: b.scrollTop, more: !!document.querySelector("#chat-live details.more[open]"), draft: ta ? ta.value : "" };
    });
    // afterSwap: 바뀐 직후(그리기 전)에 되돌려, 펼침이 잠깐 닫혔다 열리는 깜빡임이 없다
    document.body.addEventListener("htmx:afterSwap", function () {
      var b = box();
      if (!b || !snap) return;
      var state = tailState();
      var finished = waiting(prev) && !waiting(state);
      var s = snap;
      snap = null;
      if (finished) {
        if (s.more) { var more = document.querySelector("#chat-live details.more"); if (more) more.open = true; }
        var ta = document.getElementById("chat-message");
        if (ta && s.draft && !ta.value) ta.value = s.draft;
        if (s.follow) {
          var answers = b.querySelectorAll(".chat-msg.assistant:not(.live):not(.partial)");
          var last = answers[answers.length - 1];
          if (state === "idle" && last) b.scrollTop += last.getBoundingClientRect().top - b.getBoundingClientRect().top - 8; // 새 답변의 첫머리부터
          else pin();
        } else {
          b.scrollTop = s.top;
        }
        var a = document.activeElement;
        if (ta && !ta.disabled && (!a || a === document.body || a.closest("#chat-live"))) ta.focus({ preventScroll: true });
      } else if (s.follow) {
        pin();
      } else {
        b.scrollTop = s.top;
      }
      prev = state;
    });
  })();

  // 활동 기록 폼: "+ 줄 추가" 는 마지막 줄을 복사해 비운 채 덧붙인다 (후속 조치·고객 니즈 줄)
  document.querySelectorAll("[data-add-row]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var box = document.getElementById(btn.dataset.addRow);
      if (!box || !box.children.length) return;
      var rows = box.children;
      var copy = rows[rows.length - 1].cloneNode(true);
      copy.querySelectorAll(".need-linked").forEach(function (a) { a.remove(); });
      copy.querySelectorAll("input").forEach(function (i) {
        if (i.type === "checkbox") { i.checked = false; i.value = rows.length; } else { i.value = ""; }
      });
      box.appendChild(copy);
    });
  });

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
  var getCards = function () { return Array.prototype.slice.call(document.querySelectorAll("details.slide-card")); };
  if (getCards().length) {
    var OPEN_KEY = "solbox.outline.open." + location.pathname;
    var saveOpen = function () {
      var ids = getCards().filter(function (c) { return c.open; }).map(function (c) { return c.id; });
      try { sessionStorage.setItem(OPEN_KEY, JSON.stringify(ids)); } catch (e) { /* 기억은 선택 사항 */ }
    };
    // 기억해 둔 장을 다시 펼친다. 장 번호가 바뀐 처리(이동·삭제·추가, ?renumber=1)는 기억을 버리고 서버가 연 장만 남긴다
    var params = new URLSearchParams(location.search);
    if (!params.has("renumber")) {
      try {
        var saved = JSON.parse(sessionStorage.getItem(OPEN_KEY) || "[]");
        getCards().forEach(function (c) { if (saved.indexOf(c.id) >= 0) c.open = true; });
      } catch (e) { /* 저장소를 못 쓰면 서버가 정한 상태 그대로 */ }
    }
    saveOpen();
    // 기억해 둔 장을 펼치면 위쪽 장의 높이가 달라져 브라우저가 이미 맞춰 둔 앵커 위치가 어긋난다: 펼친 뒤 방금 만진 장으로 다시 맞춘다
    (function () {
      var target = document.getElementById(hashId());
      if (target && target.matches && target.matches("details.slide-card")) {
        target.open = true;
        target.scrollIntoView({ block: "start" });
      }
    })();
    // 주소의 ?open=, ?renumber= 는 한 번 쓰고 지운다. 새로고침하면 기억해 둔 상태를 따른다
    if (params.has("open") || params.has("renumber") || params.has("notice")) {
      params.delete("open"); params.delete("renumber"); params.delete("notice");
      var rest = params.toString();
      try { history.replaceState(null, "", location.pathname + (rest ? "?" + rest : "") + location.hash); } catch (e) { /* 그대로 둔다 */ }
    }
    document.addEventListener("toggle", function (e) { if (e.target.matches && e.target.matches("details.slide-card")) saveOpen(); }, true);
    document.querySelectorAll(".toggle-slides").forEach(function (box) {
      box.hidden = false;
      box.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b) return;
        getCards().forEach(function (c) { c.open = b.dataset.open === "1"; });
        saveOpen();
      });
    });
    var revealSlide = function () {
      var c = document.getElementById(hashId());
      if (c && c.matches("details.slide-card")) c.open = true;
    };
    window.addEventListener("hashchange", revealSlide);
    revealSlide();

    // 확인함 표시는 화면을 다시 불러오지 않고 그 장만 바꾼다. 장의 항목을 다 확인하면 접고 다음 남은 장을 연다
    var swapChecked = function (form) {
      var card = form.closest("details.slide-card");
      return fetch(form.action, { method: "POST", credentials: "same-origin", body: new URLSearchParams(new FormData(form)) })
        .then(function (r) { if (!r.ok) throw new Error("status " + r.status); return r.text(); })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          var fresh = doc.getElementById(card.id);
          card = document.getElementById(card.id); // 응답을 기다리는 사이 다른 확인이 카드를 바꿨을 수 있어 바꿀 때 다시 찾는다
          if (!fresh || !card) throw new Error("no card");
          var wasOpen = card.open;
          fresh.open = wasOpen;
          var pressed = document.querySelector(".remaining-toggle[aria-pressed=true]");
          fresh.hidden = !!pressed && !fresh.dataset.remaining;
          card.replaceWith(fresh);
          [".count-evidence", ".count-decide", ".count-done"].forEach(function (sel) {
            var from = doc.querySelector(sel), to = document.querySelector(sel);
            if (from && to) to.textContent = from.textContent;
          });
          var deck = doc.getElementById("deck"), curDeck = document.getElementById("deck");
          if (deck && curDeck) curDeck.innerHTML = deck.innerHTML;
          initSlideForms(fresh);
          if (!fresh.dataset.remaining && wasOpen) {
            fresh.open = false;
            var all = getCards(), at = all.indexOf(fresh);
            var next = all.slice(at + 1).concat(all.slice(0, at)).filter(function (c) { return c.dataset.remaining; })[0];
            if (next) { next.open = true; next.scrollIntoView({ block: "nearest" }); }
          }
          saveOpen();
        });
    };
    // 확인 요청은 하나씩 차례로 보낸다 (서버의 읽고-고치고-쓰기와 화면 바꾸기가 겹쳐 한 쪽 확인이 사라지지 않게)
    var checkQueue = Promise.resolve();
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!window.fetch || !window.DOMParser || !form.matches || !form.matches("form[data-ajax-check]")) return;
      e.preventDefault();
      var btn = form.querySelector("button");
      if (btn) btn.disabled = true;
      var run = function () {
        return swapChecked(form).catch(function () { form.submit(); }); // 안 되면 보통 제출로 되돌린다
      };
      checkQueue = checkQueue.then(run, run);
    });
  }

  // 구성안 편집 폼: 종류에 맞는 칸만 보인다
  function initSlideForms(root) {
    root.querySelectorAll(".slide-form").forEach(function (form) {
      var sel = form.querySelector(".kind-select");
      if (!sel) return;
      var dsel = form.querySelector(".dtype-select");
      var sync = function () {
        form.querySelectorAll("[data-kinds]").forEach(function (el) { el.hidden = el.dataset.kinds.split(" ").indexOf(sel.value) < 0; });
        // 도식 장: 고른 도식 종류의 칸만 보인다 (data-dtypes). 다른 종류의 장에서는 위의 data-kinds 만 본다
        if (sel.value === "diagram" && dsel) {
          form.querySelectorAll("[data-dtypes]").forEach(function (el) { el.hidden = el.dataset.dtypes.split(" ").indexOf(dsel.value) < 0; });
        }
      };
      sync();
      sel.addEventListener("change", sync);
      if (dsel) dsel.addEventListener("change", sync);
    });
  }
  initSlideForms(document);

  // 리서치 상세: 모두 펼치기/접기, 분야 탭
  document.querySelectorAll(".toggle-all").forEach(function (box) {
    box.hidden = false;
    box.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      var target = document.getElementById(box.dataset.target);
      if (!b || !target) return;
      target.querySelectorAll("details.trend-card, details.plan-edit").forEach(function (d) { d.open = b.dataset.open === "1"; });
    });
  });
  // 날짜 글: "2026-11-17" -> "11월 17일" (올해가 아니면 "2025년 11월 17일"). 서버의 ui.short_date 와 같은 모양
  function kdateText(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((text || "").trim());
    if (!m) return text;
    var day = (+m[2]) + "월 " + (+m[3]) + "일";
    return +m[1] === new Date().getFullYear() ? day : m[1] + "년 " + day;
  }
  // 올리기 확인 화면: 고치기를 닫을 때 요약 갱신, 제외 체크에 따라 저장 막대의 건수 갱신
  document.querySelectorAll(".plan-item").forEach(function (card) {
    var edit = card.querySelector("details.plan-edit");
    if (!edit) return;
    var key = card.id.replace(/^item-/, "");
    edit.addEventListener("toggle", function () {
      if (edit.open) return;
      var summary = edit.querySelector("summary"), head = card.querySelector(".plan-title");
      if (summary && head) summary.setAttribute("aria-label", head.textContent.trim() + " 고치기");
      card.querySelectorAll("[data-sum]").forEach(function (el) {
        if (el.dataset.sum === "line") { // 연락처 덧붙이기: 칸 값으로 덧붙일 줄을 다시 만든다
          var val = function (n) { var i = card.querySelector('[name="' + key + "__" + n + '"]'); return i ? i.value.replace(/\s+/g, " ").trim() : ""; };
          var parts = [[val("person"), val("role")].filter(Boolean).join(" "), val("phone"), val("email"), val("address"), val("note")].filter(Boolean);
          if (parts.length) el.textContent = parts.join(" · ") + (el.dataset.suffix ? " (" + el.dataset.suffix : "");
          else if (el.dataset.empty) el.textContent = el.dataset.empty;
          return;
        }
        var input = card.querySelector('[name="' + key + "__" + el.dataset.sum + '"]');
        if (!input) return;
        var text = input.tagName === "SELECT" ? (input.selectedOptions[0] || {}).textContent : input.value;
        if (el.dataset.sum === "date") text = kdateText(text); // 서버와 같은 날짜 모양
        if (text && text.trim()) el.textContent = text.trim();
        else if (el.dataset.empty) el.textContent = el.dataset.empty; // 칸을 비우면 처음 서버가 그린 자리 표시 글로
      });
    });
  });
  var planBar = document.querySelector(".plan-savebar");
  if (planBar) {
    var planForm = planBar.closest("form") || document.getElementById(planBar.dataset.form || "");
    var planCount = function () {
      var total = parseInt(planBar.dataset.total, 10) || 0;
      var skipped = planForm.querySelectorAll(".plan-skip input:checked").length;
      var saved = total - skipped;
      planBar.querySelector("[data-save-button]").textContent = "이대로 저장 (" + saved + "건)";
      planBar.querySelector("[data-save-count]").textContent = saved + "건 저장" + (skipped ? " · " + skipped + "건 제외" : "");
    };
    var markSkipped = function () {
      planForm.querySelectorAll(".plan-item").forEach(function (card) {
        var box = card.querySelector(".plan-skip input");
        card.classList.toggle("is-skipped", !!(box && box.checked)); // :has 를 못 쓰는 브라우저용 (CSS 는 둘 다 있다)
      });
    };
    planForm.addEventListener("change", function (e) {
      if (e.target.closest && e.target.closest(".plan-skip")) { planCount(); markSkipped(); }
    });
    // 모두 포함 / 모두 제외 (명함 수백 장처럼 항목이 많을 때)
    document.querySelectorAll(".skip-all").forEach(function (box) {
      box.hidden = false;
      box.addEventListener("click", function (e) {
        var b = e.target.closest("button");
        if (!b) return;
        planForm.querySelectorAll(".plan-skip input").forEach(function (c) { c.checked = b.dataset.skip === "1"; });
        planCount();
        markSkipped();
      });
    });
    planCount();
    markSkipped();
  }
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
  // 올리기: 끌어다 놓은 파일을 같은 파일 입력칸에 더하고, 고른 파일 목록·개수·합계를 보여 준다. 없어도 파일 선택 버튼으로 올릴 수 있다.
  function sizeText(bytes) {
    if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + "KB";
    return (bytes / (1024 * 1024)).toFixed(1) + "MB";
  }
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector('input[type="file"]');
    if (!input) return;
    var list = zone.querySelector(".dropzone-list");
    var summary = zone.querySelector(".dropzone-summary");
    var warn = zone.querySelector(".dropzone-warn");
    var maxFiles = parseInt(zone.getAttribute("data-max-files"), 10) || 0;
    var maxMb = parseFloat(zone.getAttribute("data-max-mb")) || 0;
    var show = function () {
      var files = Array.prototype.slice.call(input.files || []);
      var total = 0;
      var tooBig = [];
      if (list) list.textContent = "";
      files.forEach(function (f, index) {
        total += f.size;
        if (maxMb && f.size > maxMb * 1024 * 1024) tooBig.push(f.name);
        if (!list) return;
        var li = document.createElement("li");
        li.appendChild(document.createTextNode(f.name + " (" + sizeText(f.size) + ") "));
        var rm = document.createElement("button"); // 골라 둔 파일을 하나씩 뺀다 (붙여넣은 사진을 잘못 넣었을 때도)
        rm.type = "button";
        rm.className = "small dropzone-remove";
        rm.textContent = "빼기";
        rm.setAttribute("aria-label", f.name + " 빼기");
        rm.addEventListener("click", function () { removeAt(index); });
        li.appendChild(rm);
        list.appendChild(li);
      });
      if (summary) {
        summary.hidden = files.length === 0;
        summary.textContent = files.length ? files.length + "개 선택 · 합계 " + sizeText(total) : "";
      }
      var messages = [];
      if (maxFiles && files.length > maxFiles) messages.push("한 번에 최대 " + maxFiles + "개까지 올릴 수 있어요. " + files.length + "개를 골랐으니 " + (files.length - maxFiles) + "개를 빼 주세요.");
      if (tooBig.length) messages.push("파일 하나는 최대 " + maxMb + "MB까지예요. 너무 큰 파일: " + tooBig.join(", "));
      if (warn) {
        warn.hidden = messages.length === 0;
        warn.textContent = messages.join(" ");
      }
    };
    // 끌어다 놓은 파일은 이미 고른 파일에 더한다 (이름·크기·수정 시각이 같으면 한 번만)
    var add = function (dropped) {
      var merged;
      try {
        merged = new DataTransfer();
        var seen = {};
        Array.prototype.concat.call(Array.prototype.slice.call(input.files || []), Array.prototype.slice.call(dropped)).forEach(function (f) {
          var key = f.name + "|" + f.size + "|" + f.lastModified;
          if (seen[key]) return;
          seen[key] = true;
          merged.items.add(f);
        });
      } catch (err) {
        input.files = dropped; // DataTransfer 를 쓸 수 없는 브라우저: 놓은 파일로 바꾼다
        show();
        return;
      }
      input.files = merged.files;
      show();
    };
    var removeAt = function (index) {
      try {
        var kept = new DataTransfer();
        Array.prototype.slice.call(input.files || []).forEach(function (f, i) { if (i !== index) kept.items.add(f); });
        input.files = kept.files;
      } catch (err) { input.value = ""; }
      show();
    };
    // 클립보드의 사진(카카오톡 PC 에서 복사한 사진, 화면 캡처 …)을 끌어다 놓은 것과 똑같이 더한다. 글 붙여넣기는 건드리지 않는다.
    var EXT = { "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp" };
    var PASTED = "붙여넣은 사진 ";
    var pastedFiles = function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [];
      var out = [];
      Array.prototype.forEach.call(items, function (it) {
        if (it.kind === "file" && /^image\//.test(it.type)) { var f = it.getAsFile(); if (f) out.push(f); }
      });
      if (!out.length && e.clipboardData && e.clipboardData.files) {
        Array.prototype.forEach.call(e.clipboardData.files, function (f) { if (/^image\//.test(f.type)) out.push(f); });
      }
      return out;
    };
    var editable = function (el) {
      if (!el || !el.closest) return false;
      var t = el.closest("textarea, input, [contenteditable=''], [contenteditable='true']");
      if (!t) return false;
      return t.tagName !== "INPUT" || !/^(file|checkbox|radio|button|submit)$/.test(t.type);
    };
    document.addEventListener("paste", function (e) {
      if (e.__dropzoneHandled) return;
      var other = e.target && e.target.closest ? e.target.closest("[data-dropzone]") : null;
      if (other && other !== zone) return; // 다른 올리기 칸 안에서 붙였다
      if (!other && zone !== document.querySelector("[data-dropzone]")) return; // 칸 밖에서 붙이면 쪽의 첫 칸으로
      var images = pastedFiles(e);
      if (!images.length) return; // 사진이 없으면 (글 붙여넣기 등) 그대로 둔다
      if (editable(e.target) && e.clipboardData.getData("text/plain")) return; // 글 칸에 글과 사진이 함께 오면 글을 붙인다
      e.__dropzoneHandled = true;
      e.preventDefault();
      var used = 0;
      Array.prototype.forEach.call(input.files || [], function (f) {
        var m = f.name.indexOf(PASTED) === 0 ? parseInt(f.name.slice(PASTED.length), 10) : 0;
        if (m > used) used = m;
      });
      var named = images.map(function (f) {
        used += 1;
        var ext = EXT[f.type] || (/\.([a-z0-9]{1,5})$/i.exec(f.name || "") || [0, "png"])[1].toLowerCase();
        try { return new File([f], PASTED + used + "." + ext, { type: f.type, lastModified: f.lastModified || Date.now() }); } catch (err) { return f; }
      });
      add(named);
    });
    var hint = zone.querySelector("[data-dropzone-hint]");
    if (hint) hint.hidden = false;
    input.addEventListener("change", show);
    zone.classList.add("enhanced"); // 파일 입력칸은 감추고 영역 전체(label)를 누르게 한다
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add("over"); });
    });
    zone.addEventListener("dragleave", function (e) {
      if (!e.relatedTarget || !zone.contains(e.relatedTarget)) zone.classList.remove("over");
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      zone.classList.remove("over");
      if (e.dataTransfer && e.dataTransfer.files.length) add(e.dataTransfer.files);
    });
    show();
  });

  // 올리기: 글 붙여넣기 글자 수 (없어도 서버가 한도를 확인한다)
  document.querySelectorAll("[data-paste]").forEach(function (box) {
    var ta = box.querySelector("[data-paste-text]");
    var n = box.querySelector("[data-paste-n]");
    var count = box.querySelector("[data-paste-count]");
    var max = parseInt(box.getAttribute("data-paste-max"), 10) || 0;
    if (!ta || !n) return;
    var update = function () {
      var len = ta.value.replace(/\r\n?/g, "\n").length;
      n.textContent = len.toLocaleString("en-US");
      if (count && max) count.classList.toggle("over", len > max);
    };
    ta.addEventListener("input", update);
    update();
  });

  // 파일 고르기 칸: 고른 파일 이름을 보여 준다 (없어도 고르기는 된다)
  document.querySelectorAll("[data-filepick]").forEach(function (box) {
    var input = box.querySelector('input[type="file"]');
    var name = box.querySelector(".filepick-name");
    if (!input || !name) return;
    var first = name.textContent;
    input.addEventListener("change", function () {
      var n = input.files ? input.files.length : 0;
      name.textContent = n === 0 ? first : (n === 1 ? input.files[0].name : input.files[0].name + " 외 " + (n - 1) + "개");
      box.classList.toggle("has-file", n > 0);
    });
  });

  // ---- 사용성 보강 (모두 선택 사항: 스크립트가 없으면 서버가 그린 화면 그대로 쓴다) ----
  var reduceMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  function isComposing(e) { return e.isComposing || e.keyCode === 229; }

  // 결과 알림(.flash): 닫기 링크는 스크립트 없이도 동작한다. 스크립트가 있으면 주소에서 notice 를 지워 새로 고쳐도 다시 나오지 않게 하고,
  // 성공 알림은 잠시 뒤 서서히 사라진다 (움직임을 줄이는 설정이면 사라지지 않고 닫기를 눌러야 한다. 오류·주의 알림은 늘 남는다).
  (function () {
    var flash = document.querySelector("[data-flash]");
    if (!flash) return;
    try {
      var u = new URL(location.href);
      if (u.searchParams.has("notice")) {
        u.searchParams.delete("notice");
        history.replaceState(null, "", u.pathname + u.search + u.hash);
      }
    } catch (e) { /* 주소를 못 고쳐도 알림은 보인다 */ }
    var gone = function () { if (flash.parentNode) flash.parentNode.removeChild(flash); };
    var hide = function () {
      if (reduceMotion) { gone(); return; }
      flash.classList.add("fading");
      setTimeout(gone, 400);
    };
    var x = flash.querySelector(".flash-x");
    if (x) x.addEventListener("click", function (e) { e.preventDefault(); hide(); });
    if (flash.classList.contains("ok") && !reduceMotion) setTimeout(hide, 6000);
  })();

  // 되돌릴 수 없는 동작의 그 자리 되묻기: "정말 삭제할까요? [삭제] [취소]". 키보드로 쓸 수 있고(처음 초점은 취소, Esc 로 취소) 브라우저 기본 확인창을 쓰지 않는다.
  // 스크립트가 없으면 링크(data-confirm-post)가 서버의 확인 화면으로 가고, 폼(data-confirm)은 바로 보낸다.
  function askInline(trigger, ask, ok, onOk) {
    var header = trigger.closest ? trigger.closest(".page-header") : null; // 제목 줄의 삭제: 확인은 제목 줄 밑 따로 선 줄에 놓는다 (제목을 좁히지 않는다)
    if (header ? header.querySelector(":scope > .confirm-inline") : (trigger.nextElementSibling && trigger.nextElementSibling.classList.contains("confirm-inline"))) return;
    var box = document.createElement("span");
    box.className = "confirm-inline";
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", ask);
    var q = document.createElement("span");
    q.className = "confirm-q";
    q.textContent = ask;
    var yes = document.createElement("button");
    yes.type = "button";
    yes.className = "small danger";
    yes.textContent = ok;
    var no = document.createElement("button");
    no.type = "button";
    no.className = "small";
    no.textContent = "취소";
    box.appendChild(q); box.appendChild(yes); box.appendChild(no);
    trigger.hidden = true;
    if (header) header.appendChild(box); else trigger.parentNode.insertBefore(box, trigger.nextSibling);
    var close = function () { if (box.parentNode) box.parentNode.removeChild(box); trigger.hidden = false; trigger.focus(); };
    no.addEventListener("click", close);
    yes.addEventListener("click", function () { yes.disabled = true; onOk(); });
    box.addEventListener("keydown", function (e) { if (e.key === "Escape") { e.preventDefault(); close(); } });
    no.focus();
  }
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-confirm-post]");
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    askInline(a, a.dataset.ask || "정말 삭제할까요?", a.dataset.ok || "삭제", function () {
      var f = document.createElement("form");
      f.method = "post";
      f.action = a.dataset.confirmPost;
      f.hidden = true;
      document.body.appendChild(f);
      f.submit();
    });
  });
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-confirm]") || form.dataset.confirmed === "1") return;
    e.preventDefault();
    var trigger = e.submitter || form.querySelector('button[type="submit"]');
    if (!trigger) return;
    askInline(trigger, form.dataset.confirm, form.dataset.ok || "계속", function () { form.dataset.confirmed = "1"; form.submit(); });
  });

  // 글 칸 높이: 쓰는 만큼 늘어나고 (CSS 의 최대 높이에서 멈추고 그 뒤로는 스크롤), 접힌 칸이 열릴 때·HTMX 로 바뀔 때도 맞춘다
  function fit(t) {
    if (!t || t.tagName !== "TEXTAREA" || !t.offsetParent) return; // 숨겨진 칸은 열릴 때 맞춘다
    t.style.height = "auto";
    t.style.height = t.scrollHeight + 2 + "px";
  }
  function fitAll(root) { (root || document).querySelectorAll("textarea").forEach(fit); }
  document.addEventListener("input", function (e) { if (e.target.tagName === "TEXTAREA") fit(e.target); });
  document.addEventListener("toggle", function (e) { if (e.target.tagName === "DETAILS") fitAll(e.target); }, true);
  document.body.addEventListener("htmx:afterSwap", function (e) { fitAll(e.target); });
  window.addEventListener("resize", function () { fitAll(); });
  fitAll();

  // 오류 화면의 "뒤로 가기": 브라우저가 폼에 쓰던 내용을 되살린다 (저장하지 않은 409·503 화면)
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-history-back]");
    if (b) { e.preventDefault(); history.back(); }
  });
  // 단추는 JS 가 있을 때만 보인다 (없으면 "브라우저의 뒤로 가기" 안내 글이 대신 보인다: error.html)
  document.querySelectorAll("[data-history-back][hidden]").forEach(function (b) { b.hidden = false; });

  // 제안 대화: 입력칸에서 Ctrl/⌘+Enter 로 보낸다 (Enter 는 줄바꿈. 한글 조합 중에는 가로채지 않는다). 시작 질문 칩은 누르면 바로 보낸다 (칩이 곧 제출 버튼이라 따로 할 일이 없다).
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (!t || t.id !== "chat-message" || e.key !== "Enter" || !(e.ctrlKey || e.metaKey) || isComposing(e)) return;
    e.preventDefault();
    var send = t.form && t.form.querySelector('button[type="submit"], button:not([type])');
    if (t.value.trim() && !t.disabled && t.form && !(send && send.disabled)) t.form.requestSubmit(); // 보내기 버튼이 막혀 있으면(AI 꺼짐) 보내지 않는다
  });

  // 목록 표: 줄의 빈 곳을 눌러도 제목 링크로 간다 (링크·버튼·입력칸을 누른 것과 글자를 끌어 고른 것은 그대로). 키보드는 제목 링크를 쓴다.
  document.querySelectorAll("table.list-table").forEach(function (t) { if (t.querySelector("a.row-title")) t.classList.add("row-click"); });
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var tr = e.target.closest && e.target.closest("table.row-click tbody tr");
    if (!tr || e.target.closest("a, button, input, select, textarea, label, summary, details")) return;
    var link = tr.querySelector("a.row-title");
    if (!link || (window.getSelection && String(window.getSelection()).length)) return;
    link.click();
  });

  // 저장하지 않은 변경 지킴이: 폼 칸을 고친 채 다른 곳으로 가려 하면 브라우저가 한 번 묻는다 (저장 버튼으로 보낼 때·검색용 GET 폼은 제외)
  (function () {
    var forms = Array.prototype.slice.call(document.querySelectorAll("form.form-page"));
    var plan = document.querySelector(".plan-savebar");
    var planOwner = plan && (plan.closest("form") || document.getElementById(plan.dataset.form || ""));
    if (planOwner) forms.push(planOwner);
    forms = forms.filter(function (f) { return f.method !== "get"; });
    if (!forms.length) return;
    var snap = function (f) {
      var parts = [];
      new FormData(f).forEach(function (v, k) { parts.push(k + "=" + (typeof v === "string" ? v : "file:" + v.name + ":" + v.size)); });
      return parts.join("&");
    };
    var base = forms.map(snap);
    var submitting = false;
    forms.forEach(function (f) { f.addEventListener("submit", function () { submitting = true; }); });
    window.addEventListener("pageshow", function () { submitting = false; });
    window.addEventListener("beforeunload", function (e) {
      if (submitting || !forms.some(function (f, i) { return snap(f) !== base[i]; })) return;
      e.preventDefault();
      e.returnValue = "";
    });
  })();

  // 같은 폼을 두 번 보내지 않게: 제출을 시작하면 그 폼의 제출 버튼을 잠근다 (연타·더블클릭). 뒤로 가기로 돌아오면(pageshow) 다시 풀린다.
  // 버튼 값(이름·값)은 제출 때 이미 모였으므로 한 박자 뒤에 잠근다. 서버도 한 번 쓰는 폼 표로 중복을 막는다 (JS 없이도 동작).
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form || !form.matches || e.defaultPrevented || (form.method || "").toLowerCase() !== "post") return;
    if (form.hasAttribute("hx-post") || form.hasAttribute("data-ajax-check") || form.target === "_blank") return;
    var buttons = Array.prototype.slice.call(form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]'));
    if (e.submitter && buttons.indexOf(e.submitter) < 0) buttons.push(e.submitter);
    window.setTimeout(function () {
      buttons.forEach(function (b) {
        if (b.disabled) return;
        b.disabled = true; b.setAttribute("data-busy", "1");
        var label = b.getAttribute("data-busy-label"); // 오래 걸리는 동작(AI·PPT 만들기)은 글자도 "만드는 중…" 으로
        if (label) { b.setAttribute("data-orig-label", b.textContent); b.textContent = label; }
      });
    }, 0);
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("[data-busy]").forEach(function (b) {
      b.disabled = false; b.removeAttribute("data-busy");
      if (b.hasAttribute("data-orig-label")) { b.textContent = b.getAttribute("data-orig-label"); b.removeAttribute("data-orig-label"); }
    });
  });

  // 제안 만들기: 사업방식 칸을 직접 고치지 않았으면 고른 영업기회·BM 의 사업방식으로 미리 체크한다 (서버도 같은 규칙으로 정한다)
  var btBox = document.querySelector("[data-bt-picker]");
  var btData = document.getElementById("bt-defaults");
  var btAuto = document.querySelector("[data-bt-auto]");
  if (btBox && btData && btAuto) {
    var btDefs = {};
    try { btDefs = JSON.parse(btData.textContent) || {}; } catch (e) { /* 자료가 없으면 아무것도 하지 않는다 */ }
    var btBoxes = btBox.querySelectorAll('input[type="checkbox"]');
    var btVal = function (name) { var el = document.querySelector('select[name="' + name + '"]'); return el ? el.value : ""; };
    var btSync = function () {
      if (btAuto.value !== "1") return;
      var kind = document.querySelector('input[name="target_kind"]:checked');
      var codes = [];
      if (kind && kind.value === "group") {
        var gb = (btDefs.bms || {})[btVal("bm_id")];
        if (gb) codes = [gb];
      } else {
        var opp = (btDefs.opportunities || {})[btVal("opportunity_id")];
        if (opp && opp.types && opp.types.length) codes = opp.types;
        else {
          var bmId = opp ? opp.bm : ((btDefs.projects || {})[btVal("project_id")] || "");
          var one = (btDefs.bms || {})[bmId];
          if (one) codes = [one];
        }
      }
      btBoxes.forEach(function (b) { b.checked = codes.indexOf(b.value) >= 0; });
    };
    btBoxes.forEach(function (b) { b.addEventListener("change", function () { btAuto.value = "0"; }); });
    ["opportunity_id", "project_id", "bm_id"].forEach(function (n) {
      var el = document.querySelector('select[name="' + n + '"]');
      if (el) el.addEventListener("change", btSync);
    });
    document.querySelectorAll('input[name="target_kind"]').forEach(function (r) { r.addEventListener("change", btSync); });
  }

  // 글 복사 단추 (베타 AM, 구성안 내보내기): JS 가 있을 때만 보인다. 없으면 글 칸에서 직접 고르거나 파일로 받는다
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    var src = document.querySelector(btn.getAttribute("data-copy"));
    if (!src) return;
    btn.hidden = false;
    var label = btn.textContent;
    btn.addEventListener("click", function () {
      var done = function () {
        btn.textContent = btn.getAttribute("data-copied") || label;
        setTimeout(function () { btn.textContent = label; }, 2000);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(src.value).then(done, function () { src.select(); document.execCommand("copy"); done(); });
      } else {
        var box = src.closest("details");
        if (box) box.open = true;
        src.select();
        document.execCommand("copy");
        done();
      }
    });
  });

  // 목록의 구분 탭: 현재 탭이 가려져 있으면 탭 줄 안에서만 가로로 옮겨 보이게 한다
  document.querySelectorAll(".seg").forEach(function (seg) {
    var cur = seg.querySelector('[aria-current="page"]');
    if (cur && seg.scrollWidth > seg.clientWidth) seg.scrollLeft = Math.max(0, cur.offsetLeft - 16);
  });
})();

// 영업기회 보드 끌어 놓기 (영업 C): 카드를 다른 열(또는 수주·실패 칸)에 놓으면 그 단계로 바꾸는 폼을 보낸다.
// 서버가 보드로 돌려보내거나(알림), 수주·실패면 사유 화면으로 보낸다. 스크립트가 없어도 카드의 "단계 바꾸기" 단추로 같은 일을 한다.
(function () {
  var board = document.querySelector("[data-board]");
  if (!board) return;
  var qs = board.getAttribute("data-qs") || "";
  var dragId = null, fromStage = null;
  function clearMarks() { board.querySelectorAll(".drop-on").forEach(function (x) { x.classList.remove("drop-on"); }); }
  function targetOf(e) { return e.target.closest ? e.target.closest(".board-col, .board-drop") : null; }
  board.addEventListener("dragstart", function (e) {
    var card = e.target.closest ? e.target.closest(".kcard") : null;
    if (!card) return;
    dragId = card.getAttribute("data-id");
    fromStage = card.getAttribute("data-stage");
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragId);
  });
  board.addEventListener("dragend", function (e) {
    var card = e.target.closest ? e.target.closest(".kcard") : null;
    if (card) card.classList.remove("dragging");
    clearMarks();
    dragId = null;
  });
  board.addEventListener("dragover", function (e) {
    var t = targetOf(e);
    if (!t || !dragId) return;
    e.preventDefault();
    if (!t.classList.contains("drop-on")) { clearMarks(); t.classList.add("drop-on"); }
  });
  board.addEventListener("drop", function (e) {
    var t = targetOf(e);
    if (!t || !dragId) return;
    e.preventDefault();
    var stage = t.getAttribute("data-stage");
    clearMarks();
    if (!stage || stage === fromStage) return;
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/opportunities/" + encodeURIComponent(dragId) + "/stage";
    [["stage", stage], ["back", "board"], ["qs", qs]].forEach(function (kv) {
      var input = document.createElement("input");
      input.type = "hidden"; input.name = kv[0]; input.value = kv[1];
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
  });
})();
