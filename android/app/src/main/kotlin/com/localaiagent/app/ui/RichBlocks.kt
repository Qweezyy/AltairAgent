package com.localaiagent.app.ui

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp

/*
 * Formulas (KaTeX) and diagrams (Mermaid) inside answers, drawn by the bundled open-source libraries in
 * a small WebView that sizes itself to the content. Both run with their safe settings: KaTeX without
 * \href and friends (trust: false), Mermaid in strict mode; the page reaches nothing but app assets.
 */

private const val VENDOR = "file:///android_asset/vendor/"

/** A display formula on its own line. */
@Composable
fun MathBlock(tex: String, color: Color, fontSize: TextUnit) {
    val body = """<div class="m" data-d="1" data-t="${escapeHtml(tex.trim())}"></div>"""
    AssetWebBlock(page(body, color, fontSize, katex = true), minHeight = 32.dp)
}

/**
 * A line of text with inline formulas: [html] is the line's markup with each formula as a
 * `<span class="m" data-t="…">` placeholder (see [inlineMathHtml]).
 */
@Composable
fun MathLine(html: String, color: Color, fontSize: TextUnit, lineHeight: TextUnit) {
    AssetWebBlock(page(html, color, fontSize, katex = true, lineHeight = lineHeight), minHeight = 24.dp)
}

/** A Mermaid diagram (```mermaid). */
@Composable
fun MermaidBlock(code: String, color: Color, fontSize: TextUnit) {
    val dark = MaterialTheme.colorScheme.background.let { (it.red + it.green + it.blue) / 3 < 0.5f }
    val body = """<pre class="mermaid">${escapeHtml(code.trim())}</pre>""" +
        """<script src="mermaid/mermaid.min.js"></script><script>""" +
        """mermaid.initialize({startOnLoad:true,securityLevel:'strict',theme:'${if (dark) "dark" else "default"}'});""" +
        """</script>"""
    AssetWebBlock(page(body, color, fontSize, katex = false), minHeight = 48.dp)
}

private fun cssColor(c: Color): String = "#%06X".format(0xFFFFFF and c.toArgb())

@Composable
private fun page(body: String, color: Color, fontSize: TextUnit, katex: Boolean, lineHeight: TextUnit? = null): String {
    val density = LocalDensity.current
    // sp to CSS px: the page is laid out at device-width, where a CSS px is a dp.
    val px = with(density) { fontSize.toPx() / density.density }
    val lh = lineHeight?.let { with(density) { it.toPx() / density.density } }
    val accent = cssColor(MaterialTheme.colorScheme.primary)
    val head = if (katex) """<link rel="stylesheet" href="katex/katex.min.css"><script src="katex/katex.min.js"></script>""" else ""
    val render = if (katex) """
<script>
document.querySelectorAll('.m').forEach(function(e){
  try{katex.render(e.getAttribute('data-t'),e,{displayMode:e.getAttribute('data-d')==='1',throwOnError:false,trust:false,strict:'ignore'})}
  catch(x){e.textContent=e.getAttribute('data-t')}
});
</script>""" else ""
    return """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">$head
<style>
html,body{margin:0;padding:0;background:transparent;color:${cssColor(color)};font-family:-apple-system,Roboto,system-ui,sans-serif;font-size:${px}px;${lh?.let { "line-height:${it}px;" } ?: "line-height:1.5;"}overflow-x:auto;overflow-y:hidden}
.katex-display{margin:.3em 0;overflow-x:auto;overflow-y:hidden}
code{font-family:monospace;font-size:.9em;background:rgba(127,127,127,.15);border-radius:4px;padding:0 3px}
a{color:$accent}
pre.mermaid{margin:0;display:flex;justify-content:center}
</style></head><body>$body$render
<script>
(function(){function r(){try{AgentBridge.setHeight(Math.ceil(document.documentElement.getBoundingClientRect().height))}catch(e){}}
window.addEventListener('load',r);try{new ResizeObserver(r).observe(document.body)}catch(e){}
[50,200,600,1500].forEach(function(t){setTimeout(r,t)})})();
</script></body></html>"""
}

/** A WebView page from app assets that takes its content's height. */
@Composable
private fun AssetWebBlock(doc: String, minHeight: Dp) {
    var heightDp by remember { mutableStateOf(0.dp) }
    androidx.compose.ui.viewinterop.AndroidView(
        factory = { ctx ->
            android.webkit.WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
                isVerticalScrollBarEnabled = false
                overScrollMode = android.view.View.OVER_SCROLL_NEVER
                webViewClient = object : android.webkit.WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        view: android.webkit.WebView,
                        request: android.webkit.WebResourceRequest,
                    ): Boolean = true
                }
                // The bridge only reports the height.
                addJavascriptInterface(object {
                    @android.webkit.JavascriptInterface
                    fun setHeight(px: Int) { post { heightDp = px.dp.coerceIn(0.dp, 3000.dp) } }
                }, "AgentBridge")
            }
        },
        update = { wv ->
            if (wv.tag != doc) {
                wv.tag = doc
                wv.loadDataWithBaseURL(VENDOR, doc, "text/html", "utf-8", null)
            }
        },
        modifier = Modifier.fillMaxWidth().then(
            if (heightDp > 0.dp) Modifier.height(heightDp) else Modifier.heightIn(min = minHeight),
        ),
    )
}

internal fun escapeHtml(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")
