import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { createOpalineScene } from "./createOpalineScene";

type SceneState = "animated" | "fallback" | "loading" | "static";

export function OpalineScene({ paused = false }: { paused?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<
    ReturnType<typeof createOpalineScene> | undefined
  >(undefined);
  const initialPausedRef = useRef(paused);
  const [sceneState, setSceneState] = useState<SceneState>("loading");

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let active = true;
    try {
      const controller = createOpalineScene(canvas, {
        host,
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

  useLayoutEffect(() => {
    controllerRef.current?.setPaused(paused);
  }, [paused]);

  return (
    <div
      ref={hostRef}
      aria-hidden="true"
      className="jx-opaline-scene"
      data-opaline-paused={paused || undefined}
      data-opaline-state={sceneState}
    >
      <canvas id="scene" ref={canvasRef} />
    </div>
  );
}
