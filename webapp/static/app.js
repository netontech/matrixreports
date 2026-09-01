/* Interaction for the attendance portal.

   Everything here is progressive: the report is complete and readable with
   JavaScript switched off. These only make a wide sheet easier to work with. */
(function () {
  "use strict";

  // --- theme ---------------------------------------------------------------
  var root = document.documentElement;
  var KEY = "matrixreports-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
  } catch (e) { /* private mode: fall back to the OS preference */ }

  var toggle = document.getElementById("theme");
  if (toggle) toggle.addEventListener("click", function () {
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.hasAttribute("data-theme") &&
        window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) { /* not essential */ }
  });

  var printBtn = document.getElementById("print");
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });

  // --- pin the second column beside the first ------------------------------
  // Its offset depends on the rendered width of column one, so measure it.
  function pin() {
    document.querySelectorAll("table").forEach(function (table) {
      var first = table.querySelector("thead tr.cols th.pin-1");
      if (!first) return;
      var left = first.getBoundingClientRect().width + "px";
      table.querySelectorAll(".pin-2").forEach(function (cell) { cell.style.left = left; });
    });
  }
  pin();
  window.addEventListener("resize", pin);

  // --- filter --------------------------------------------------------------
  var filter = document.getElementById("filter");
  if (filter) {
    filter.addEventListener("input", function () {
      var needle = filter.value.trim().toLowerCase();
      document.querySelectorAll("[data-sheet]").forEach(function (sheet) {
        var shown = 0;
        sheet.querySelectorAll("tbody tr").forEach(function (row) {
          var cells = row.querySelectorAll("td");
          var hay = "";
          for (var i = 0; i < Math.min(3, cells.length); i++) hay += " " + cells[i].textContent;
          var match = !needle || hay.toLowerCase().indexOf(needle) !== -1;
          row.hidden = !match;
          if (match) shown++;
        });
        var count = sheet.querySelector("[data-count]");
        if (count) {
          var total = sheet.querySelectorAll("tbody tr").length;
          count.textContent = shown === total
            ? total + " rows"
            : shown + " of " + total + " rows";
        }
      });
    });
  }

  // --- sort ----------------------------------------------------------------
  // Times and durations are HH:MM, so comparing minutes keeps them in order;
  // plain lexical sort would put 9:05 after 10:00.
  function value(text) {
    var t = text.trim();
    if (!t) return { empty: true, n: 0, s: "" };
    var hhmm = /^(\d{1,3}):([0-5]\d)$/.exec(t);
    if (hhmm) return { empty: false, n: parseInt(hhmm[1], 10) * 60 + parseInt(hhmm[2], 10), s: "" };
    var num = /^-?\d+(\.\d+)?$/.exec(t);
    if (num) return { empty: false, n: parseFloat(t), s: "" };
    return { empty: false, n: null, s: t.toLowerCase() };
  }

  document.querySelectorAll("thead th.sortable").forEach(function (header) {
    function run() {
      var table = header.closest("table");
      var body = table.querySelector("tbody");
      var index = parseInt(header.dataset.col, 10);
      var asc = header.getAttribute("aria-sort") !== "ascending";

      table.querySelectorAll("thead th").forEach(function (other) {
        if (other !== header) other.removeAttribute("aria-sort");
      });
      header.setAttribute("aria-sort", asc ? "ascending" : "descending");
      header.querySelector(".arrow").textContent = asc ? "▴" : "▾";

      var rows = Array.prototype.slice.call(body.querySelectorAll("tr"));
      rows.sort(function (a, b) {
        var x = value((a.children[index] || {}).textContent || "");
        var y = value((b.children[index] || {}).textContent || "");
        if (x.empty !== y.empty) return x.empty ? 1 : -1;   // blanks always last
        var d = (x.n !== null && y.n !== null) ? x.n - y.n : x.s.localeCompare(y.s);
        return asc ? d : -d;
      });
      rows.forEach(function (row) { body.appendChild(row); });
    }
    header.addEventListener("click", run);
    header.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); run(); }
    });
  });
})();
