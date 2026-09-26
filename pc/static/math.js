"use strict";

/* Формулы: маскировка перед разбором markdown и отрисовка через KaTeX.
 *
 * Вынесено из app.js отдельным файлом: это законченный кусок со своей
 * логикой, и его удобно проверять отдельно от интерфейса.
 */

//: Пары разделителей формул. Порядок важен: «$$» обязан проверяться раньше
//: «$», иначе «$$x$$» распадётся на два пустых куска.
const MATH_PATTERNS = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
  { open: "$", close: "$", display: false },
];

//: Метка вокруг номера формулы. Символы управляющие намеренно: строка вида
//: «MATH0» в ответе вполне может встретиться, и обратная замена испортила бы
//: текст. Управляющие символы в осмысленном тексте не встречаются никогда.
const MATH_OPEN = "";
const MATH_CLOSE = "";

/** Прячет формулы перед разбором markdown и возвращает их список.
 *
 *  Без этого шага «\[ ... \]» не доживает до KaTeX: markdown считает «\[»
 *  экранированной скобкой и убирает слэш, а «_» внутри формулы превращает в
 *  курсив. Внутри блоков кода ничего не трогаем — там доллары это доллары.
 */
function maskMath(text, store) {
  const parts = text.split(/(```[\s\S]*?```|`[^`\n]*`)/g);

  return parts
    .map((part, index) => {
      if (index % 2 === 1) return part; // код — как есть
      let result = "";
      let rest = part;

      outer: while (rest.length) {
        for (const pattern of MATH_PATTERNS) {
          if (!rest.startsWith(pattern.open)) continue;
          const end = rest.indexOf(pattern.close, pattern.open.length);
          if (end === -1) continue;

          const body = rest.slice(pattern.open.length, end);
          if (!body.trim()) continue;

          // «Цена выросла с $100 до $200» — не формула. Признаки настоящей:
          // тело не начинается и не кончается пробелом, за закрывающим
          // долларом не идёт цифра, и внутри нет пустой строки.
          if (pattern.open === "$") {
            const after = rest[end + pattern.close.length] || "";
            const looksLikeMoney =
              /^\s|\s$/.test(body) || /\d/.test(after) || /\n\s*\n/.test(body);
            if (looksLikeMoney) continue;
          }

          store.push({ body, display: pattern.display });
          result += `${MATH_OPEN}${store.length - 1}${MATH_CLOSE}`;
          rest = rest.slice(end + pattern.close.length);
          continue outer;
        }
        result += rest[0];
        rest = rest.slice(1);
      }
      return result;
    })
    .join("");
}

/** Экранирование HTML. Модуль самодостаточен: не зависит от глобалей UI
 *  (в старом app.js была escapeHTML, в новом redesign.js — esc). */
function mathEscHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Возвращает формулы в разметку — уже отдельными узлами для KaTeX. */
function unmaskMath(html, store) {
  const pattern = new RegExp(`${MATH_OPEN}(\\d+)${MATH_CLOSE}`, "g");
  return html.replace(pattern, (match, index) => {
    const item = store[Number(index)];
    if (!item) return match;
    const tag = item.display ? "div" : "span";
    return `<${tag} class="math" data-display="${item.display}">${mathEscHtml(item.body)}</${tag}>`;
  });
}

/** Отрисовывает спрятанные формулы: «\frac{x}{2}» посреди текста нечитаемо. */
function renderMath(container) {
  if (typeof katex === "undefined") return;

  container.querySelectorAll(".math:not([data-rendered])").forEach((node) => {
    const source = node.textContent;
    try {
      katex.render(source, node, {
        displayMode: node.dataset.display === "true",
        // Кривую формулу показываем как есть: пустое место хуже сырого LaTeX.
        throwOnError: false,
        errorColor: "#b4593a",
      });
    } catch (error) {
      node.textContent = source;
      if (typeof logLine === "function") logLine(`Формула не отрисовалась: ${error}`, "debug");
    }
    node.dataset.rendered = "yes";
  });
}
