// Self-contained Mermaid loader.
//
// We deliberately do NOT use Material's built-in mermaid handling (it expects the
// `.mermaid` class and renders unreliably in this setup). Instead the fenced
// ```mermaid blocks are emitted with class `mermaid-diagram`, which Material leaves
// alone. We pull each diagram's raw text out via textContent (already decoded by the
// browser), render it as a string with mermaid.render(), and inject the SVG.
//
// Runs on initial load and re-runs on Material's instant-navigation page swaps.
(function () {
  var loader = null; // cached import promise

  function load() {
    if (!loader) {
      loader = import(
        "https://unpkg.com/mermaid@10.9.6/dist/mermaid.esm.min.mjs"
      ).then(function (mod) {
        var mermaid = mod.default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "loose",
          theme: "default",
          // Use a font that is present immediately so Mermaid measures label sizes
          // against the same font it displays - otherwise boxes come out too short
          // and the text is clipped.
          fontFamily:
            '"Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif',
          flowchart: { htmlLabels: true, useMaxWidth: true, padding: 12 },
        });
        return mermaid;
      });
    }
    return loader;
  }

  async function render() {
    var blocks = Array.prototype.slice.call(
      document.querySelectorAll("pre.mermaid-diagram")
    );
    if (!blocks.length) return;
    // Wait for web fonts so label measurement matches what is rendered.
    if (document.fonts && document.fonts.ready) {
      try { await document.fonts.ready; } catch (e) {}
    }
    var mermaid = await load();
    for (var i = 0; i < blocks.length; i++) {
      var el = blocks[i];
      var code = el.querySelector("code");
      var def = (code ? code.textContent : el.textContent).trim();
      try {
        var out = await mermaid.render("mermaid-svg-" + Date.now() + "-" + i, def);
        var div = document.createElement("div");
        div.className = "mermaid-rendered";
        div.innerHTML = out.svg;
        el.replaceWith(div);
      } catch (err) {
        console.error("Mermaid render failed:", err);
      }
    }
  }

  function start() {
    render().catch(function (err) {
      console.error("Mermaid failed to load:", err);
    });
  }

  // Material exposes an RxJS `document$` that fires on every (instant) navigation.
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(start);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
