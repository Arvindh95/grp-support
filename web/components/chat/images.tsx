"use client";

import type { ImageItem } from "@/lib/chat-types";

export function ImageGallery({ images }: { images: ImageItem[] }) {
  if (!images || images.length === 0) return null;

  // Group by section so each cluster has a header.
  const grouped = images.reduce<Record<string, ImageItem[]>>((acc, img) => {
    const key = `${img.module || "?"} · ${img.section || "?"}`;
    (acc[key] ||= []).push(img);
    return acc;
  }, {});

  return (
    <div className="mt-4 border-t pt-3 space-y-3">
      <p className="text-xs text-muted-foreground">📸 Related screenshots</p>
      {Object.entries(grouped).map(([label, imgs]) => (
        <div key={label} className="space-y-2">
          <p className="text-xs font-medium">{label}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {imgs.map((img, i) => (
              <figure key={i} className="border rounded overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={img.url} alt={img.caption || ""} className="w-full h-auto block" loading="lazy" />
                {img.caption && (
                  <figcaption className="text-xs px-2 py-1 bg-muted/40 text-muted-foreground">
                    {img.caption}
                  </figcaption>
                )}
              </figure>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
