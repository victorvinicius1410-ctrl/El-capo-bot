import { useEffect, useRef, useState } from "react";
import {
  ROBOT_AVATAR_ALPHA_STACK_SRC,
  ROBOT_AVATAR_STILL_SRC,
  ROBOT_AVATAR_WEBM_SRC,
  resolveRobotAvatarPipeline,
} from "@/lib/robotAvatarVisual";

export interface RobotAvatarVideoProps {
  className?: string;
  /** Rótulo acessível do avatar. */
  "aria-label"?: string;
}

/** Redesenha no máximo ~15x por segundo (o vídeo original tem 12 fps). */
const FRAME_INTERVAL_MS = 66;

/**
 * Avatar animado do El Capo.
 *
 * - Windows/Chrome/Firefox/Android: `<video>` + filtro CSS, com o WebM alfa
 *   original (visual de referência).
 * - Safari/iOS: o WebKit ignora `filter` em `<video>` **e** joga fora o canal
 *   alfa do WebM — era daí que vinha o quadrado verde-oliva atrás do robô no
 *   Mac. Aqui usamos o vídeo empilhado (cor em cima, máscara embaixo) e o
 *   canvas remonta a transparência pixel a pixel.
 * - Navegador que não toca WebM (iOS anterior ao 17.4): frame estático PNG.
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
  const [videoBroken, setVideoBroken] = useState(false);

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

    // Canvas auxiliar recebe o quadro inteiro (cor + máscara) para leitura.
    const stack = document.createElement("canvas");
    const stackCtx = stack.getContext("2d", { willReadFrequently: true });
    if (!stackCtx) {
      setPipeline("video");
      return () => {
        video.removeEventListener("loadeddata", tryPlay);
        video.removeEventListener("canplay", tryPlay);
      };
    }

    let raf = 0;
    let running = true;
    let lastDraw = 0;

    const syncSize = (): { width: number; height: number } => {
      // O vídeo empilhado tem o dobro da altura: cor em cima, máscara embaixo.
      const width = video.videoWidth || 512;
      const height = Math.round((video.videoHeight || 1024) / 2);
      if (stack.width !== width || stack.height !== height * 2) {
        stack.width = width;
        stack.height = height * 2;
      }
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      return { width, height };
    };

    const draw = (now: number) => {
      if (!running) return;
      raf = window.requestAnimationFrame(draw);
      if (video.readyState < 2) return;
      if (now - lastDraw < FRAME_INTERVAL_MS) return;
      lastDraw = now;

      const { width, height } = syncSize();
      stackCtx.drawImage(video, 0, 0, width, height * 2);

      const frame = stackCtx.getImageData(0, 0, width, height);
      const matte = stackCtx.getImageData(0, height, width, height);
      const pixels = frame.data;
      const mask = matte.data;
      for (let i = 3; i < pixels.length; i += 4) {
        // Máscara em escala de cinza: qualquer canal serve como opacidade.
        pixels[i] = mask[i - 3];
      }
      ctx.putImageData(frame, 0, 0);
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

  // Navegador sem WebM nenhum: mostra o frame estático com alfa de verdade.
  if (videoBroken) {
    return (
      <span className="robot-avatar-root relative inline-flex justify-center">
        <img
          src={ROBOT_AVATAR_STILL_SRC}
          alt={ariaLabel}
          className={`robot-avatar-video pointer-events-none ${className}`}
        />
      </span>
    );
  }

  const videoVisible = pipeline === "video";

  return (
    <span className="robot-avatar-root relative inline-flex justify-center">
      <video
        ref={videoRef}
        src={videoVisible ? ROBOT_AVATAR_WEBM_SRC : ROBOT_AVATAR_ALPHA_STACK_SRC}
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
        onError={() => setVideoBroken(true)}
      />
      <canvas
        ref={canvasRef}
        aria-label={ariaLabel}
        role="img"
        className={
          pipeline === "canvas" ? `robot-avatar-video pointer-events-none ${className}` : "hidden"
        }
      />
    </span>
  );
}
