import { useEffect, useRef, useState } from "react";

export function GenerationIndicator({ label }: { label: string }) {
  const indicator = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const element = indicator.current;
    if (!element) return;

    let intersects = typeof IntersectionObserver === "undefined";
    const syncPlayback = () => setPlaying(intersects && !document.hidden);
    const observer = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(([entry]) => {
      intersects = Boolean(entry?.isIntersecting);
      syncPlayback();
    });

    observer?.observe(element);
    document.addEventListener("visibilitychange", syncPlayback);
    syncPlayback();
    return () => {
      observer?.disconnect();
      document.removeEventListener("visibilitychange", syncPlayback);
    };
  }, []);

  return (
    <div className="chat-generation" ref={indicator} role="status" data-playing={playing}>
      <span className="chat-liquid" aria-hidden="true">
        <span className="chat-liquid-ripple" />
        <span className="chat-liquid-body" />
        <span className="chat-liquid-drop" />
      </span>
      <span>{label}</span>
    </div>
  );
}
