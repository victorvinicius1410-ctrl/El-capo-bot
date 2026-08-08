import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  isEditableKeyboardTarget,
  isMarketingHotkey,
  shouldToggleMarketingPanel,
} from "./marketingHotkey.ts";

describe("marketingHotkey", () => {
  it("reconhece Shift+O sem outros modificadores", () => {
    assert.equal(isMarketingHotkey({ key: "O", code: "KeyO", shiftKey: true }), true);
    assert.equal(isMarketingHotkey({ key: "o", code: "KeyO", shiftKey: true }), true);
    assert.equal(isMarketingHotkey({ code: "KeyO", shiftKey: true }), true);
  });

  it("ignora atalho com Ctrl/Meta/Alt ou sem Shift", () => {
    assert.equal(isMarketingHotkey({ key: "O", shiftKey: true, ctrlKey: true }), false);
    assert.equal(isMarketingHotkey({ key: "O", shiftKey: true, metaKey: true }), false);
    assert.equal(isMarketingHotkey({ key: "O", shiftKey: true, altKey: true }), false);
    assert.equal(isMarketingHotkey({ key: "O", shiftKey: false }), false);
  });

  it("não dispara com foco em input", () => {
    const input = {
      closest: (selector: string) => (selector.includes("input") ? {} : null),
    };
    assert.equal(
      shouldToggleMarketingPanel(
        { key: "O", code: "KeyO", shiftKey: true, target: input as unknown as EventTarget },
        true,
      ),
      false,
    );
  });

  it("só habilita para conta marketing", () => {
    assert.equal(
      shouldToggleMarketingPanel({ key: "O", code: "KeyO", shiftKey: true, target: null }, false),
      false,
    );
    assert.equal(
      shouldToggleMarketingPanel({ key: "O", code: "KeyO", shiftKey: true, target: null }, true),
      true,
    );
  });

  it("detecta contentEditable como alvo editável", () => {
    assert.equal(
      isEditableKeyboardTarget({ isContentEditable: true } as unknown as EventTarget),
      true,
    );
  });
});
