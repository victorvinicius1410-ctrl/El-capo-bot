import { useEffect, useRef, useState } from "react";
import {
  ROBOT_AVATAR_WEBM_SRC,
  resolveRobotAvatarPipeline,
} from "@/lib/robotAvatarVisual";

export interface RobotAvatarVideoProps {
  className?: string;
  /** Rótulo acessível do avatar. */
  "aria-label"?: string;
}

/**
 * Avatar animado do El Capo.
 *
 * - Windows/Chrome/Firefox/Android: `<video>` + filtro CSS (visual original,
 *   sem a “borda”/quadro que o canvas introduzia).
 * - Safari/iOS: vídeo oculto → `<canvas>` + mesmo filtro CSS (WebKit ignora
 *   filter em `<video>`).
 */
export function RobotAvatarVideo({
  className = "",
  "aria-label": ariaLabel = "Robô analisando o mercado",
}: RobotAvatarVideoProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [pipeline, setPipeline] = useState<"canvas" | "video">(() =>
    typeof navigator !== "undefined" ? resolveRobotAvatarPipeline() : "video",
  );

  useEffect(() => {
    const preferred = resolveRobotAvatarPipeline();
    setPipeline(preferred);

    const video = videoRef.current;
    if (!video) return;

    const tryPlay = () => {
      void video.play().catch((error) => {
        console.warn("[ROBOT AVATAR] autoplay bloqueado; tentando de novo no gesto", error);
      });
    };

    video.addEventListener("loadeddata", tryPlay);
    video.addEventListener("canplay", tryPlay);
    tryPlay();

    if (preferred !== "canvas") {
      return () => {
        video.removeEventListener("loadeddata", tryPlay);
        video.removeEventListener("canplay", tryPlay);
      };
    }

    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) {
      setPipeline("video");
      return () => {
        video.removeEventListener("loadeddata", tryPlay);
        video.removeEventListener("canplay", tryPlay);
      };
    }

    let raf = 0;
    let running = true;

    const syncSize = () => {
      const width = video.videoWidth || 512;
      const height = video.videoHeight || 512;
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
    };

    const draw = () => {
      if (!running) return;
      if (video.readyState >= 2) {
        syncSize();
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      }
      raf = window.requestAnimationFrame(draw);
    };

    const onLoaded = () => {
      syncSize();
      tryPlay();
    };

    video.addEventListener("loadeddata", onLoaded);
    raf = window.requestAnimationFrame(draw);

    return () => {
      running = false;
      window.cancelAnimationFrame(raf);
      video.removeEventListener("loadeddata", tryPlay);
      video.removeEventListener("loadeddata", onLoaded);
      video.removeEventListener("canplay", tryPlay);
    };
  }, []);

  const videoVisible = pipeline === "video";

  return (
    <span className="robot-avatar-root relative inline-flex justify-center">
      <video
        ref={videoRef}
        src={ROBOT_AVATAR_WEBM_SRC}
        className={
          videoVisible
            ? `robot-avatar-video pointer-events-none ${className}`
            : "robot-avatar-source"
        }
        aria-hidden={!videoVisible}
        aria-label={videoVisible ? ariaLabel : undefined}
        autoPlay
        loop
        muted
        playsInline
        disablePictureInPicture
      />
      <canvas
        ref={canvasRef}
        aria-label={ariaLabel}
        role="img"
        className={
          pipeline === "canvas"
            ? `robot-avatar-video pointer-events-none ${className}`
            : "hidden"
        }
      />
    </span>
  );
}
