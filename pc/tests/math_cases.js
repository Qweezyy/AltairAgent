// Проверка маскировки формул. Запускается из tests/test_math_js.py через node:
// логика чисто строковая, и её дешевле проверить там же, где она живёт.
"use strict";

const fs = require("fs");
const path = require("path");

// НЕ определяем escapeHTML нарочно: math.js должен быть самодостаточным (в окне
// redesign.js глобали escapeHTML нет, и раньше unmaskMath из-за этого падал).

const source = fs.readFileSync(
  path.join(__dirname, "..", "static", "math.js"),
  "utf8",
);
// indirect eval кладёт объявления в глобальную область, обычный eval —
// в локальную область модуля, где их не видно остальному файлу.
(0, eval)(source.replace('"use strict";', ""));

const cases = [
  {
    name: "строчная формула в долларах",
    input: "Для $f(x) = x\\sin x$ получаем",
    formulas: ["f(x) = x\\sin x"],
    display: [false],
  },
  {
    name: "выключная формула в двойных долларах",
    input: "Итог: $$a^2 + b^2 = c^2$$ вот так",
    formulas: ["a^2 + b^2 = c^2"],
    display: [true],
  },
  {
    name: "скобки \\[ \\] переживают markdown",
    input: "Интеграл \\[ \\int_0^3 x^2\\,dx = 9 \\] готов",
    formulas: [" \\int_0^3 x^2\\,dx = 9 "],
    display: [true],
  },
  {
    name: "скобки \\( \\) переживают markdown",
    input: "Матрица \\( A = B \\) тут",
    formulas: [" A = B "],
    display: [false],
  },
  {
    name: "цены формулой не считаются",
    input: "Цена выросла с $100 до $200 за штуку",
    formulas: [],
    display: [],
  },
  {
    name: "доллары в коде не трогаем",
    input: "Смотри:\n\n```bash\necho \"$HOME стоит $5\"\n```\n",
    formulas: [],
    display: [],
  },
  {
    name: "доллары в строчном коде не трогаем",
    input: "Переменная `$PATH` и `$HOME` рядом",
    formulas: [],
    display: [],
  },
  {
    name: "подчёркивание внутри формулы не станет курсивом",
    input: "Индексы $x_1 + x_2$ здесь",
    formulas: ["x_1 + x_2"],
    display: [false],
  },
  {
    name: "несколько формул подряд нумеруются по порядку",
    input: "Сначала $a$, потом $b$, затем $$c$$",
    formulas: ["a", "b", "c"],
    display: [false, false, true],
  },
  {
    name: "незакрытая формула остаётся текстом",
    input: "Оборванная $x + y без конца",
    formulas: [],
    display: [],
  },
];

let failed = 0;
for (const item of cases) {
  const store = [];
  const masked = maskMath(item.input, store);
  const bodies = store.map((entry) => entry.body);
  const flags = store.map((entry) => entry.display);

  const ok =
    JSON.stringify(bodies) === JSON.stringify(item.formulas) &&
    JSON.stringify(flags) === JSON.stringify(item.display);

  if (!ok) {
    failed += 1;
    console.log(`ПРОВАЛ: ${item.name}`);
    console.log(`  вход:    ${JSON.stringify(item.input)}`);
    console.log(`  ждали:   ${JSON.stringify(item.formulas)} ${JSON.stringify(item.display)}`);
    console.log(`  вышло:   ${JSON.stringify(bodies)} ${JSON.stringify(flags)}`);
    console.log(`  маска:   ${JSON.stringify(masked)}`);
  }

  // Обратная замена обязана восстановить ровно столько узлов, сколько спрятали.
  const html = unmaskMath(masked, store);
  const nodes = (html.match(/class="math"/g) || []).length;
  if (nodes !== item.formulas.length) {
    failed += 1;
    console.log(`ПРОВАЛ (обратная замена): ${item.name}: узлов ${nodes}, формул ${item.formulas.length}`);
  }
  if (/[]/.test(html)) {
    failed += 1;
    console.log(`ПРОВАЛ: в результате остались служебные метки — ${item.name}`);
  }
}

// Отдельно: текст, похожий на метку, не должен ломать обратную замену.
const store = [];
const tricky = maskMath("Модель MATH0 и число 0 рядом с $x$", store);
const restored = unmaskMath(tricky, store);
if (!restored.includes("MATH0")) {
  failed += 1;
  console.log("ПРОВАЛ: обычный текст «MATH0» пострадал от маскировки");
}

// unmaskMath обязан экранировать HTML в теле формулы (защита от инъекции и
// проверка, что модуль самодостаточен — без внешней escapeHTML он не падает).
{
  const st = [];
  const masked = maskMath("Неравенство $a < b & c > 0$ тут", st);
  const html = unmaskMath(masked, st);
  if (!html.includes("&lt;") || !html.includes("&amp;") || html.includes("<b ")) {
    failed += 1;
    console.log(`ПРОВАЛ: unmaskMath не экранировал HTML — ${JSON.stringify(html)}`);
  }
}

console.log(failed ? `ПРОВАЛОВ: ${failed}` : `ВСЕ ${cases.length + 2} ПРОВЕРОК ПРОШЛИ`);
process.exit(failed ? 1 : 0);
