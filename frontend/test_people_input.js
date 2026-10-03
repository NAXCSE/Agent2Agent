// Headless test of frontend/people.js - the real file the browser loads.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "people.js"), "utf8");
const context = { module: { exports: {} } };
vm.createContext(context);
vm.runInContext(source, context);
const { MIN_PEOPLE, collectPeople } = context.module.exports;

console.log("MIN_PEOPLE =", MIN_PEOPLE);
console.log();

const person = (linkedin, instagram) => ({ linkedin, instagram });

const cases = [
  ["two complete people", [person("li1", "ig1"), person("li2", "ig2")], true, 2],
  ["only one filled", [person("li1", "ig1"), person("", "")], false, 1],
  ["second half filled", [person("li1", "ig1"), person("li2", "")], false, 1],
  ["first half filled", [person("li1", ""), person("li2", "ig2")], false, 1],
  ["three people", [person("li1", "ig1"), person("li2", "ig2"), person("li3", "ig3")], true, 3],
  ["extra blank row ignored", [person("li1", "ig1"), person("li2", "ig2"), person("", "")], true, 2],
  ["nothing filled", [person("", ""), person("", "")], false, 0],
  ["whitespace only", [person("   ", "  "), person("", "")], false, 0],
  ["values are trimmed", [person("  li1  ", "  ig1  "), person("li2", "ig2")], true, 2],
];

let ok = true;

for (const [label, entries, expectedComplete, expectedCount] of cases) {
  const result = collectPeople(entries);
  const passed =
    result.complete === expectedComplete && result.people.length === expectedCount;
  ok = ok && passed;
  console.log(
    `[${passed ? "PASS" : "FAIL"}] ${label.padEnd(24)} complete=${String(result.complete).padEnd(5)} ` +
      `people=${result.people.length}` +
      (result.missing.length ? ` | ${result.missing[0]}` : "")
  );
}

const shape = collectPeople([person("https://li/1", "https://ig/1"), person("https://li/2", "https://ig/2")]);
const shapeOk =
  shape.people[0].linkedin_url === "https://li/1" &&
  shape.people[0].instagram_url === "https://ig/1";
ok = ok && shapeOk;
console.log(`[${shapeOk ? "PASS" : "FAIL"}] payload uses linkedin_url / instagram_url`);

console.log();
console.log(`RESULT: ${ok ? "PASS" : "FAIL"}`);
process.exit(ok ? 0 : 1);