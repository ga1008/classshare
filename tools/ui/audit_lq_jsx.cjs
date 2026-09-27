'use strict';
// TypeScript's own parser distinguishes JSX from type arguments. No app import,
// compilation, network, filesystem output or execution of inspected source.
const fs = require('node:fs');
const ts = require('typescript');
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = {};
for (const [name, text] of Object.entries(inputs)) {
  // TypeScript offsets are UTF-16 code units; Python/source reports use Unicode
  // characters. Emoji before JSX must not shift a codemod insertion point.
  const codePoints = [];
  let index = 0, point = 0;
  while (index < text.length) {
    codePoints[index] = point;
    const width = text.codePointAt(index) > 0xffff ? 2 : 1;
    if (width === 2) codePoints[index + 1] = point;
    index += width; point++;
  }
  codePoints[text.length] = point;
  const file = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true,
    name.endsWith('.tsx') ? ts.ScriptKind.TSX : name.endsWith('.jsx') ? ts.ScriptKind.JSX : ts.ScriptKind.TS);
  const ranges = [];
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      ranges.push([codePoints[node.getStart(file)], codePoints[node.getEnd()], 'jsx']);
    } else if (ts.isTemplateExpression(node)) {
      ranges.push([codePoints[node.getStart(file)], codePoints[node.getEnd()], 'string']);
      return;
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      if (!ts.isJsxAttribute(node.parent) && !ts.isImportDeclaration(node.parent)) {
        ranges.push([codePoints[node.getStart(file)], codePoints[node.getEnd()], 'string']);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  output[name] = { ranges, parseDiagnostics: file.parseDiagnostics.map(item => ({
    start: item.start, message: ts.flattenDiagnosticMessageText(item.messageText, ' '),
  })) };
}
process.stdout.write(JSON.stringify(output));
