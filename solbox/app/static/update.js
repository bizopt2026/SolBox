// 업데이트 진행 화면: 진행을 보여 주고, 마친 뒤 서버가 새 커밋으로 다시 올라오면 시스템 화면으로 간다 (스크립트가 없어도 업데이트 자체는 돈다).
(function () {
  var steps = document.getElementById("up-steps"), head = document.getElementById("up-head"), err = document.getElementById("up-error");
  var restart = document.getElementById("up-restart"), slow = document.getElementById("up-slow");
  var target = "", seenRunning = false, waiting = false, startedWaiting = 0;
  var ICON = { run: "… ", ok: "✓ ", fail: "✗ " };
  function render(list) {
    steps.textContent = "";
    list.forEach(function (s) { var li = document.createElement("li"); li.textContent = (ICON[s.status] || "") + s.text; steps.appendChild(li); });
  }
  function waitForServer() {
    if (!waiting) { waiting = true; startedWaiting = Date.now(); head.hidden = true; restart.hidden = false; }
    function again() {
      if (Date.now() - startedWaiting > 180000) slow.hidden = false;
      setTimeout(waitForServer, 1500);
    }
    fetch("/healthz", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (j) {
      if (j && j.ok && (!target || j.commit === target)) { location.href = "/admin/system"; return; }
      again();
    }).catch(again);
  }
  function poll() {
    fetch("/admin/system/update/status", { cache: "no-store", headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    }).then(function (st) {
      if (st.to) target = st.to;
      render(st.steps || []);
      if (st.state === "running") { seenRunning = true; setTimeout(poll, 1000); return; }
      if (st.state === "failed") { head.textContent = "업데이트하지 못했어요."; err.textContent = st.error; err.hidden = false; return; }
      if (st.state === "done") { seenRunning = true; waitForServer(); return; }
      location.href = "/admin/system"; // idle: 서버가 다시 시작됐다
    }).catch(function () {
      if (seenRunning) waitForServer(); else setTimeout(poll, 1500);
    });
  }
  poll();
})();
