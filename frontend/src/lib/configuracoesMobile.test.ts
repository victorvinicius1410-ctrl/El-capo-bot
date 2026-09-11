import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

describe("configurações mobile (Safari/iOS)", () => {
  it("usa atmosfera leve no mobile e guarda CSS sem blur/animações pesadas", () => {
    const page = readFileSync(
      join(here, "../routes/_authenticated/configuracoes.tsx"),
      "utf8",
    );
    const css = readFileSync(join(here, "../styles.css"), "utf8");

    assert.match(page, /useLiteConfigAtmosphere/);
    assert.match(page, /config-atmosphere-lite/);
    assert.match(page, /max-width: 767px/);
    assert.match(css, /Um problema ocorreu repetidamente/);
    assert.match(css, /fora de @layer/);
    assert.match(
      css,
      /@media \(max-width: 767px\)[\s\S]*\.config-aurora[\s\S]*display: none !important/,
    );
    assert.match(
      css,
      /@media \(max-width: 767px\)[\s\S]*\.config-tabs[\s\S]*backdrop-filter: none/,
    );
  });
});
