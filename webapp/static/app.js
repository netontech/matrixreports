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

  // Esc anywhere outside a field clears the filter too - the fastest way back
  // to the whole sheet after hunting for one person.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var box = document.getElementById("filter");
    if (box && box.value && document.activeElement !== box) {
      box.value = "";
      box.dispatchEvent(new Event("input"));
    }
  });

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
  var clearFilter = document.getElementById("clear-filter");

  function applyFilter() {
    var needle = filter.value.trim().toLowerCase();
    document.querySelectorAll("[data-sheet]").forEach(function (sheet) {
      var rows = sheet.querySelectorAll("tbody tr");
      var shown = 0;
      rows.forEach(function (row) {
        var cells = row.querySelectorAll("td");
        var hay = "";
        for (var i = 0; i < Math.min(3, cells.length); i++) hay += " " + cells[i].textContent;
        var match = !needle || hay.toLowerCase().indexOf(needle) !== -1;
        row.hidden = !match;
        if (match) shown++;
      });
      var count = sheet.querySelector("[data-count]");
      if (count) {
        count.textContent = shown === rows.length
          ? rows.length + " rows"
          : shown + " of " + rows.length + " rows";
        count.classList.toggle("filtered", shown !== rows.length);
      }
    });
    if (clearFilter) clearFilter.hidden = needle === "";
  }

  function resetFilter() {
    filter.value = "";
    applyFilter();
    filter.focus();
  }

  if (filter) {
    filter.addEventListener("input", applyFilter);
    filter.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { event.preventDefault(); resetFilter(); }
    });
    if (clearFilter) clearFilter.addEventListener("click", resetFilter);
    // A browser may restore a typed value on back-navigation.
    if (filter.value) applyFilter();
  }

  // --- clear all -----------------------------------------------------------
  // Always present, so there is one obvious way back to an untouched sheet.
  // Sorting and the live filter are client-side, so clear those in place and
  // only reload when the query string actually carries a selection.
  var clearAll = document.getElementById("clear-selections");
  if (clearAll) clearAll.addEventListener("click", function (event) {
    // Whether the URL carries a selection decides if a reload is needed. It
    // does NOT decide whether to clear: someone may have typed into the form
    // without submitting, and that text has to go either way.
    var dirtyQuery = clearAll.dataset.dirty === "1";

    ["employee", "groups"].forEach(function (id) {
      var field = document.getElementById(id);
      if (field) field.value = "";
    });

    if (filter) { filter.value = ""; applyFilter(); }

    document.querySelectorAll("thead th[aria-sort]").forEach(function (header) {
      header.removeAttribute("aria-sort");
      var arrow = header.querySelector(".arrow");
      if (arrow) arrow.textContent = "\u25BE";
    });
    document.querySelectorAll("tbody").forEach(function (body) {
      if (body._originalOrder) {
        body._originalOrder.forEach(function (row) { body.appendChild(row); });
      }
    });

    if (!dirtyQuery) event.preventDefault();   // nothing in the URL to drop
  });

  // --- sort ----------------------------------------------------------------
  // Keep the served order so "Clear all" can put it back. Sorting reorders the
  // DOM in place, so without this the original sequence is gone for good.
  document.querySelectorAll("tbody").forEach(function (body) {
    body._originalOrder = Array.prototype.slice.call(body.querySelectorAll("tr"));
  });

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
