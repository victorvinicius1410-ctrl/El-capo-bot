import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { scheduleDialogOpen } from "./scheduleDialogOpen.ts";

describe("scheduleDialogOpen", () => {
  it("não chama open de forma síncrona", () => {
    let called = false;
    scheduleDialogOpen(() => {
      called = true;
    });
    assert.equal(called, false);
  });

  it("chama open no próximo tick do event loop", async () => {
    let called = false;
    scheduleDialogOpen(() => {
      called = true;
    });
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });
    assert.equal(called, true);
  });
});
