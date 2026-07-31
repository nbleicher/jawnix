import { useEffect, useRef, useState } from "react";

import { createOpalineScene } from "./createOpalineScene";

type SceneState = "animated" | "fallback" | "loading" | "static";

export function OpalineScene() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [sceneState, setSceneState] = useState<SceneState>("loading");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let active = true;
    let controller: ReturnType<typeof createOpalineScene> | undefined;

    try {
      controller = createOpalineScene(canvas, {
        reducedMotion,
        onFailure: () => {
          if (active) setSceneState("fallback");
        },
        onReady: () => {
          if (active) setSceneState(reducedMotion ? "static" : "animated");
        },
      });
    } catch {
      setSceneState("fallback");
    }

    return () => {
      active = false;
      controller?.dispose();
    };
  }, []);

  return (
    <div
      aria-hidden="true"
      className="jx-opaline-scene"
      data-opaline-state={sceneState}
    >
      <canvas id="scene" ref={canvasRef} />
    </div>
  );
}
