"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Loader2, Trash2, Upload as UploadIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { api, API_URL, getToken } from "@/lib/api";

type SectionImage = {
  filename: string;
  url: string;
  caption: string;
};

export default function ImagesPage() {
  const [modules, setModules] = useState<string[]>([]);
  const [module, setModule] = useState<string>("");
  const [sections, setSections] = useState<string[]>([]);
  const [section, setSection] = useState<string>("");
  const [images, setImages] = useState<SectionImage[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  // Upload form state
  const [file, setFile] = useState<File | null>(null);
  const [caption, setCaption] = useState("");

  // Load modules on mount
  useEffect(() => {
    (async () => {
      try {
        setModules(await api<string[]>("/modules"));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load modules");
      }
    })();
  }, []);

  // Load sections when module changes
  useEffect(() => {
    if (!module) {
      setSections([]);
      setSection("");
      return;
    }
    (async () => {
      try {
        const list = await api<string[]>(`/sections?module=${encodeURIComponent(module)}`);
        setSections(list);
        if (list.length > 0 && !list.includes(section)) setSection(list[0]);
      } catch {
        setSections([]);
      }
    })();
  }, [module]);

  // Load images when section changes
  useEffect(() => {
    if (!module || !section) {
      setImages(null);
      return;
    }
    loadImages();
  }, [module, section]);

  async function loadImages() {
    try {
      const r = await api<{ doc_id: string | null; images: SectionImage[] }>(
        `/section-images?module=${encodeURIComponent(module)}&section=${encodeURIComponent(section)}`,
      );
      setImages(r.images);
    } catch {
      setImages([]);
    }
  }

  async function onUpload(e: FormEvent) {
    e.preventDefault();
    if (!file || !module || !section) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("module", module);
      fd.append("section", section);
      fd.append("caption", caption);
      const tok = getToken();
      const res = await fetch(`${API_URL}/upload-image`, {
        method: "POST",
        headers: tok ? { Authorization: `Bearer ${tok}` } : {},
        body: fd,
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error((j as { detail?: string }).detail || res.statusText);
      }
      setOkMsg(`Uploaded.`);
      setFile(null);
      setCaption("");
      await loadImages();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(filename: string) {
    if (!confirm(`Delete ${filename}? This removes the file and the chunk reference.`)) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await api(
        `/delete-image?module=${encodeURIComponent(module)}&section=${encodeURIComponent(section)}&filename=${encodeURIComponent(filename)}`,
        { method: "DELETE" },
      );
      setOkMsg(`Deleted ${filename}.`);
      await loadImages();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Image Manager</h1>
        <p className="text-sm text-muted-foreground">
          Browse and manage the screenshots attached to each manual section.
        </p>
      </div>

      {error && <Alert variant="destructive">{error}</Alert>}
      {okMsg && <Alert variant="success">{okMsg}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Pick a section</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Module</Label>
            <select
              value={module}
              onChange={(e) => setModule(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value="">— pick a module —</option>
              {modules.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-2">
            <Label>Section</Label>
            <select
              value={section}
              onChange={(e) => setSection(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              disabled={!module}
            >
              <option value="">— pick a section —</option>
              {sections.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      {module && section && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">📤 Add screenshot</CardTitle>
            </CardHeader>
            <CardContent>
              <form onSubmit={onUpload} className="space-y-4">
                <div className="space-y-2">
                  <Label>Image file (.png/.jpg/.jpeg/.gif/.webp)</Label>
                  <Input
                    type="file"
                    accept=".png,.jpg,.jpeg,.gif,.webp"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Caption (optional)</Label>
                  <Input value={caption} onChange={(e) => setCaption(e.target.value)} />
                </div>
                <Button type="submit" disabled={busy || !file}>
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadIcon className="h-4 w-4" />}
                  Upload
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">
                📸 {images?.length ?? "—"} image(s) on this section
              </CardTitle>
            </CardHeader>
            <CardContent>
              {images === null ? (
                <p className="text-sm text-muted-foreground">Loading…</p>
              ) : images.length === 0 ? (
                <p className="text-sm text-muted-foreground">No images yet.</p>
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {images.map((img) => (
                    <figure key={img.filename} className="border rounded overflow-hidden">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={img.url} alt={img.caption || ""} className="w-full h-auto block" />
                      <figcaption className="px-2 py-2 bg-muted/30 flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="text-xs font-medium truncate">{img.filename}</div>
                          {img.caption && (
                            <div className="text-xs text-muted-foreground truncate">{img.caption}</div>
                          )}
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => onDelete(img.filename)}
                          disabled={busy}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </figcaption>
                    </figure>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
