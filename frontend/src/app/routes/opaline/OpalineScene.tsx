import { useEffect, useRef, useState } from "react";

import { createOpalineScene } from "./createOpalineScene";

type SceneState = "animated" | "fallback" | "loading" | "static";

export function OpalineScene({ paused = false }: { paused?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const controllerRef = useRef<
    ReturnType<typeof createOpalineScene> | undefined
  >(undefined);
  const initialPausedRef = useRef(paused);
  const [sceneState, setSceneState] = useState<SceneState>("loading");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let active = true;
    try {
      const controller = createOpalineScene(canvas, {
        reducedMotion,
        onFailure: () => {
          if (active) setSceneState("fallback");
        },
        onReady: () => {
          if (active) setSceneState(reducedMotion ? "static" : "animated");
        },
      });
      controller.setPaused(initialPausedRef.current);
      controllerRef.current = controller;
    } catch {
      setSceneState("fallback");
    }

    return () => {
      active = false;
      controllerRef.current?.dispose();
      controllerRef.current = undefined;
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.setPaused(paused);
  }, [paused]);

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
