import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";
import {
  ROBOT_AVATAR_COLOR_FILTER,
  ROBOT_AVATAR_CSS_FILTER,
  ROBOT_AVATAR_WEBM_SRC,
  isRobotAvatarVideoFilterUnreliable,
  resolveRobotAvatarPipeline,
} from "./robotAvatarVisual.ts";

const here = dirname(fileURLToPath(import.meta.url));

describe("robotAvatarVisual — constantes oficiais", () => {
  it("mantém o WebM lima e o filtro hue-rotate do Windows", () => {
    assert.equal(ROBOT_AVATAR_WEBM_SRC, "/robo-wink-orig.webm");
    assert.match(ROBOT_AVATAR_COLOR_FILTER, /hue-rotate\(175deg\)/);
    assert.match(ROBOT_AVATAR_COLOR_FILTER, /saturate\(1\.35\)/);
    assert.match(ROBOT_AVATAR_CSS_FILTER, /37,\s*219,\s*224/);
  });

  it("detecta Safari/iOS como motor sem filtro confiável em <video>", () => {
    assert.equal(
      isRobotAvatarVideoFilterUnreliable(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
      ),
      true,
    );
    assert.equal(
      isRobotAvatarVideoFilterUnreliable(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
      ),
      true,
    );
    assert.equal(
      isRobotAvatarVideoFilterUnreliable(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      ),
      false,
    );
  });

  it("escolhe video no Windows/Chrome e canvas no Safari/iOS", () => {
    assert.equal(
      resolveRobotAvatarPipeline(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      ),
      "video",
    );
    assert.equal(
      resolveRobotAvatarPipeline(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
      ),
      "canvas",
    );
    assert.equal(
      resolveRobotAvatarPipeline(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
      ),
      "canvas",
    );
  });
});

describe("visual do overlay do robô (cross-platform)", () => {
  it("usa vídeo no Windows e canvas só no Safari; filtro no CSS", () => {
    const overlay = readFileSync(join(here, "../components/RobotOverlay.tsx"), "utf8");
    const avatar = readFileSync(join(here, "../components/RobotAvatarVideo.tsx"), "utf8");
    const styles = readFileSync(join(here, "../styles.css"), "utf8");

    assert.match(overlay, /RobotAvatarVideo/);
    assert.doesNotMatch(overlay, /<video[\s\S]*robo-wink/);
    assert.doesNotMatch(overlay, /hue-rotate/);
    assert.doesNotMatch(overlay, /robo-wink-classic/);

    assert.match(avatar, /robo-wink-orig\.webm|ROBOT_AVATAR_WEBM_SRC/);
    assert.match(avatar, /resolveRobotAvatarPipeline/);
    assert.match(avatar, /<canvas/);
    assert.match(avatar, /drawImage/);
    assert.match(avatar, /robot-avatar-video/);
    assert.doesNotMatch(avatar, /robo-wink-classic/);

    assert.match(styles, /\.robot-avatar-video/);
    assert.match(styles, /\.robot-avatar-source/);
    assert.match(styles, /hue-rotate\(175deg\)/);
    assert.match(styles, /37,\s*219,\s*224/);
  });
});
