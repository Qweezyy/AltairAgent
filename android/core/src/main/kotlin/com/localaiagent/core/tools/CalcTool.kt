package com.localaiagent.core.tools

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
import java.math.BigDecimal
import java.math.BigInteger
import java.math.MathContext
import java.math.RoundingMode

/**
 * Точный калькулятор на чистом Kotlin (офлайн, без Python-рантайма на телефоне).
 * Большие числа/степени/факториал — ТОЧНО (BigDecimal/BigInteger); тригонометрия/
 * логарифмы — с double-точностью. Нужен, чтобы бот «считал точно», а не в уме.
 */
class CalcTool : Tool {
    override val name = "calc"
    override val description =
        "Точно вычисляет математическое выражение — используй ВСЕГДА, когда нужна точность " +
            "(арифметика, большие числа, проценты, дроби), не считай в уме. Поддержка: + - * / % " +
            "^ (степень), ! (факториал), скобки; функции sqrt, cbrt, abs, exp, ln, log, log10, " +
            "sin, cos, tan, floor, ceil, round, gcd, min, max, factorial; константы pi, e. " +
            "Примеры: factorial(20), 2^100, (1/3+1/6), sqrt(2)*3, 15%*200 → пиши 0.15*200."
    override val category = ToolCategory.READ

    override fun schema(): JsonObject = buildJsonObject {
        put("type", "object")
        putJsonObject("properties") {
            putJsonObject("expression") { put("type", "string"); put("description", "Выражение, напр. factorial(20) или 2^100") }
        }
        putJsonArray("required") { add("expression") }
    }

    override suspend fun run(args: JsonObject, ctx: ToolContext): ToolResult {
        val expr = args["expression"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
        if (expr.isEmpty()) return ToolResult.fail("пустое выражение")
        return try {
            val result = Calc(expr).evaluate()
            ToolResult("$expr = ${format(result)}")
        } catch (e: Exception) {
            ToolResult.fail("не удалось вычислить «$expr»: ${e.message}")
        }
    }

    private fun format(v: BigDecimal): String {
        val s = v.stripTrailingZeros()
        // toPlainString — без научной записи (чтобы факториалы/степени были полными).
        return if (s.scale() <= 0) s.toBigInteger().toString() else s.toPlainString()
    }
}

/**
 * Рекурсивный парсер выражений. Приоритеты: + - < * / % < унарный минус < ^ (правая
 * ассоц.) < постфикс ! < атом. Числа — BigDecimal; ^ и ! с целыми — точные.
 */
private class Calc(private val src: String) {
    private var pos = 0
    private val mc = MathContext(50)

    fun evaluate(): BigDecimal {
        val v = parseExpr()
        skipWs()
        if (pos < src.length) error("лишние символы у позиции $pos")
        return v
    }

    private fun parseExpr(): BigDecimal {
        var left = parseTerm()
        while (true) {
            skipWs()
            when (peek()) {
                '+' -> { pos++; left = left.add(parseTerm(), mc) }
                '-' -> { pos++; left = left.subtract(parseTerm(), mc) }
                else -> return left
            }
        }
    }

    private fun parseTerm(): BigDecimal {
        var left = parseUnary()
        while (true) {
            skipWs()
            when (peek()) {
                '*' -> { pos++; left = left.multiply(parseUnary(), mc) }
                '/' -> { pos++; left = divide(left, parseUnary()) }
                '%' -> { pos++; left = left.remainder(parseUnary(), mc) }
                else -> return left
            }
        }
    }

    private fun parseUnary(): BigDecimal {
        skipWs()
        return when (peek()) {
            '-' -> { pos++; parseUnary().negate() }
            '+' -> { pos++; parseUnary() }
            else -> parsePower()
        }
    }

    private fun parsePower(): BigDecimal {
        val base = parsePostfix()
        skipWs()
        // Степень: ^ или Python-стиль ** (модели часто пишут 2**10).
        if (peek() == '^' || (peek() == '*' && src.getOrNull(pos + 1) == '*')) {
            pos += if (peek() == '^') 1 else 2
            val exp = parseUnary() // правая ассоциативность + унарный минус в показателе
            return power(base, exp)
        }
        return base
    }

    private fun parsePostfix(): BigDecimal {
        var v = parseAtom()
        while (true) {
            skipWs()
            if (peek() == '!') { pos++; v = factorial(v) } else return v
        }
    }

    private fun parseAtom(): BigDecimal {
        skipWs()
        val c = peek() ?: error("неожиданный конец выражения")
        if (c == '(') {
            pos++
            val v = parseExpr()
            skipWs()
            if (peek() != ')') error("ожидалась ')'")
            pos++
            return v
        }
        if (c.isDigit() || c == '.') return parseNumber()
        if (c.isLetter()) return parseNameOrCall()
        error("непонятный символ '$c'")
    }

    private fun parseNumber(): BigDecimal {
        val start = pos
        while (peek()?.let { it.isDigit() || it == '.' } == true) pos++
        // экспоненциальная запись: 1e9, 2.5E-3
        if (peek()?.lowercaseChar() == 'e' && src.getOrNull(pos + 1)?.let { it.isDigit() || it == '+' || it == '-' } == true) {
            pos++
            if (peek() == '+' || peek() == '-') pos++
            while (peek()?.isDigit() == true) pos++
        }
        return BigDecimal(src.substring(start, pos))
    }

    private fun parseNameOrCall(): BigDecimal {
        val start = pos
        while (peek()?.let { it.isLetterOrDigit() || it == '_' } == true) pos++
        val name = src.substring(start, pos).lowercase()
        skipWs()
        if (peek() == '(') {
            pos++
            val a = parseExpr()
            var b: BigDecimal? = null
            skipWs()
            if (peek() == ',') { pos++; b = parseExpr(); skipWs() }
            if (peek() != ')') error("ожидалась ')' после $name(")
            pos++
            return callFunc(name, a, b)
        }
        return constant(name)
    }

    // ------------------------------------------------------------- операции

    private fun divide(a: BigDecimal, b: BigDecimal): BigDecimal {
        if (b.signum() == 0) error("деление на ноль")
        return try {
            a.divide(b) // точно, если делится нацело/конечно
        } catch (_: ArithmeticException) {
            a.divide(b, mc) // иначе — с округлением до 50 значащих
        }
    }

    private fun power(base: BigDecimal, exp: BigDecimal): BigDecimal {
        // Целая степень — точно (в т.ч. большие: 2^100).
        if (exp.stripTrailingZeros().scale() <= 0) {
            val n = exp.toBigIntegerExact()
            if (n.signum() >= 0 && n.bitLength() < 31) return base.pow(n.toInt())
            if (n.signum() < 0 && n.negate().bitLength() < 31) return BigDecimal.ONE.divide(base.pow(n.negate().toInt()), mc)
        }
        return BigDecimal(Math.pow(base.toDouble(), exp.toDouble()), mc)
    }

    private fun factorial(v: BigDecimal): BigDecimal {
        if (v.stripTrailingZeros().scale() > 0 || v.signum() < 0) error("факториал только для целых ≥ 0")
        val n = v.toBigIntegerExact()
        // Ограничение, чтобы гигантский факториал не подвесил телефон (n=20000 —
        // это уже ~78k цифр, считается быстро; больше — редко нужно точно).
        if (n > BigInteger.valueOf(20_000)) error("слишком большой факториал (макс 20000)")
        var acc = BigInteger.ONE
        var i = BigInteger.TWO
        while (i <= n) { acc = acc.multiply(i); i = i.add(BigInteger.ONE) }
        return BigDecimal(acc)
    }

    private fun constant(name: String): BigDecimal = when (name) {
        "pi" -> BigDecimal(Math.PI)
        "e" -> BigDecimal(Math.E)
        else -> error("неизвестная константа '$name'")
    }

    private fun callFunc(name: String, a: BigDecimal, b: BigDecimal?): BigDecimal {
        fun d(x: Double) = BigDecimal(x, mc)
        return when (name) {
            "sqrt" -> { if (a.signum() < 0) error("sqrt от отрицательного"); d(Math.sqrt(a.toDouble())) }
            "cbrt" -> d(Math.cbrt(a.toDouble()))
            "abs" -> a.abs()
            "exp" -> d(Math.exp(a.toDouble()))
            "ln" -> d(Math.log(a.toDouble()))
            "log10" -> d(Math.log10(a.toDouble()))
            "log" -> if (b != null) d(Math.log(b.toDouble()) / Math.log(a.toDouble())) else d(Math.log(a.toDouble()))
            "sin" -> d(Math.sin(a.toDouble()))
            "cos" -> d(Math.cos(a.toDouble()))
            "tan" -> d(Math.tan(a.toDouble()))
            "floor" -> a.setScale(0, RoundingMode.FLOOR)
            "ceil" -> a.setScale(0, RoundingMode.CEILING)
            "round" -> a.setScale(0, RoundingMode.HALF_UP)
            "factorial" -> factorial(a)
            "gcd" -> { requireNotNull(b) { "gcd(a,b)" }; BigDecimal(a.toBigIntegerExact().gcd(b.toBigIntegerExact())) }
            "min" -> { requireNotNull(b) { "min(a,b)" }; a.min(b) }
            "max" -> { requireNotNull(b) { "max(a,b)" }; a.max(b) }
            else -> error("неизвестная функция '$name'")
        }
    }

    // ------------------------------------------------------------- утилиты

    private fun peek(): Char? = src.getOrNull(pos)
    private fun skipWs() { while (peek() == ' ' || peek() == '\t' || peek() == '\n') pos++ }
    private fun error(msg: String): Nothing = throw IllegalArgumentException(msg)
}
