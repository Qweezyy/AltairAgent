package com.localaiagent.core.tools

import com.localaiagent.core.AgentEvent
import com.localaiagent.core.Tool
import com.localaiagent.core.ToolCategory
import com.localaiagent.core.ToolContext
import com.localaiagent.core.ToolResult
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import java.io.File

private fun cvStr(desc: String): JsonObject = buildJsonObject { put("type", "string"); put("description", desc) }

private fun cvSchema(props: Map<String, JsonObject>, required: List<String>): JsonObject = buildJsonObject {
    put("type", "object")
    putJsonObject("properties") { props.forEach { (k, v) -> put(k, v) } }
    putJsonArray("required") { required.forEach { add(it) } }
}

private fun JsonObject.c(key: String): String = this[key]?.jsonPrimitive?.contentOrNull.orEmpty()

/** Рисует графику: модель даёт SVG, он показывается в ответе. */
class ShowGraphicTool : Tool {
    override val name = "show_graphic"
    override val description =
        "Рисует векторную графику в ответе: диаграмму, схему, график, иллюстрацию. " +
            "Передай ПОЛНЫЙ валидный <svg>…</svg> (используй viewBox для масштабирования). " +
            "Для тем-совместимости используй цвета var(--accent)/var(--fg)/var(--muted)/var(--border) " +
            "или currentColor вместо жёстких hex (тема приложения подключается автоматически). " +
            "Для интерактива (кнопки, анимация, ввод) используй show_interactive."
    override val category = ToolCategory.EDIT
    override fun schema() = cvSchema(
        mapOf("svg" to cvStr("Полный код <svg …>…</svg>"), "caption" to cvStr("Короткая подпись")),
        listOf("svg"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val svg = args.c("svg").trim()
        if (!svg.contains("<svg")) return ToolResult.fail("нужен полный <svg>…</svg>")
        val html = """<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;padding:0;background:transparent}svg{max-width:100%;height:auto;display:block}</style>
</head><body>$svg</body></html>"""
        ctx.emit(AgentEvent.ShowHtml(html, args.c("caption")))
        return ToolResult("Показал графику пользователю.")
    }
}

/** Интерактивная графика/виджет: модель даёт HTML+JS, он рендерится в WebView. */
class ShowInteractiveTool : Tool {
    override val name = "show_interactive"
    override val description =
        "Показывает интерактивный виджет в ответе: HTML+CSS+JS. Годится для калькуляторов " +
            "(проценты, кредит, ИМТ, конвертер единиц/валют по заданному курсу), ползунков «что-если», " +
            "пошаговых решений с вводом, мини-квизов и тренажёров, интерактивных графиков. " +
            "Передай самодостаточный HTML (можно фрагмент) — БЕЗ внешних сетевых ресурсов, всё инлайн. " +
            "ТЕМА приложения уже подключена: используй CSS-переменные var(--accent), var(--fg), " +
            "var(--surface), var(--border), var(--muted), var(--radius) и классы .card/.result вместо " +
            "своих цветов; ширину делай 100%, высоту НЕ фиксируй (подгонится автоматически). " +
            "Подсказка: если у ответа есть числовой параметр — добавь под ним маленький калькулятор."
    override val category = ToolCategory.EDIT
    override fun schema() = cvSchema(
        mapOf("html" to cvStr("Самодостаточный HTML-документ (можно с <style>/<script>)"), "caption" to cvStr("Подпись")),
        listOf("html"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val html = args.c("html").trim()
        if (html.isEmpty()) return ToolResult.fail("пустой html")
        ctx.emit(AgentEvent.ShowHtml(html, args.c("caption")))
        return ToolResult("Показал интерактив пользователю.")
    }
}

/** Прикрепляет файл из песочницы/ПК прямо в ответ (видео/аудио/документ/таблица/картинка). */
class AttachFileTool : Tool {
    override val name = "attach_file"
    override val description =
        "Прикрепляет ГОТОВЫЙ файл в свой ответ (пользователь увидит превью/сможет открыть): " +
            "картинку, видео, аудио, документ, таблицу — напр. результат работы или файл, полученный с ПК. " +
            "path — путь в рабочей папке чата (или абсолютный). Сам решай, что и куда прикрепить."
    override val category = ToolCategory.READ
    override fun schema() = cvSchema(
        mapOf("path" to cvStr("Путь к файлу (в рабочей папке или абсолютный)"), "caption" to cvStr("Подпись")),
        listOf("path"),
    )

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val raw = args.c("path").trim()
        if (raw.isEmpty()) return ToolResult.fail("нужен path")
        val file = runCatching { resolveInWorkspace(ctx, raw) }.getOrNull()?.takeIf { it.isFile }
            ?: File(raw).takeIf { it.isFile }
            ?: return ToolResult.fail("файл '$raw' не найден")
        ctx.emit(AgentEvent.ShowFile(file.absolutePath, args.c("caption")))
        return ToolResult("Прикрепил файл ${file.name} к ответу.")
    }
}
