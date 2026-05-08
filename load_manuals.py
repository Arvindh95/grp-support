#!/usr/bin/env python3
"""
GRP Manual Loader
Reads MD files -> chunks by heading -> embeds via Ollama -> pushes to ES grp-manuals index
Run on server: python3 load_manuals.py
"""

import os, re, json, time, sys
import requests
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
ES_URL      = "https://localhost:9200"
ES_AUTH     = ("elastic", "W1iUd3PBH2qvhEcTc9mR")
ES_VERIFY   = False
OLLAMA_URL  = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
INDEX       = "grp-manuals"

MD_DIR  = Path("/opt/grp-manuals/Converted_MD")
IMG_DIR = Path("/opt/grp-manuals/Doc-Images")

# ── ES Mapping ────────────────────────────────────────────────────────────────
MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0
    },
    "mappings": {
        "properties": {
            "module":          {"type": "keyword"},
            "section":         {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "subsection":      {"type": "text"},
            "content":         {"type": "text", "analyzer": "standard"},
            "screen_codes":    {"type": "keyword"},
            "images":          {"type": "keyword"},
            "image_captions":  {"type": "text"},
            "chunk_index":     {"type": "integer"},
            "total_chunks":    {"type": "integer"},
            "prev_section":    {"type": "keyword"},
            "next_section":    {"type": "keyword"},
            "prev_tail":       {"type": "text"},
            "source_file":     {"type": "keyword"},
            "embedding": {
                "type":       "dense_vector",
                "dims":       1024,
                "index":      True,
                "similarity": "cosine"
            }
        }
    }
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_module_name(filename: str) -> str:
    """GRP9 - Manual Pengguna Account Payable v1.0.md -> Account Payable"""
    m = re.search(r'Manual Pengguna\s+(.+?)\s+v\d', filename)
    if m:
        return m.group(1).strip()
    return filename.replace('.md', '')


def extract_screen_codes(text: str) -> list[str]:
    """Find screen codes like AP301000, PR201000, GL102000"""
    return list(set(re.findall(r'\b[A-Z]{2,3}\d{6}\b', text)))


def extract_images_and_captions(text: str) -> tuple[list[str], list[str]]:
    """
    Find all ![Image](path) refs and italic captions after them.
    Returns (image_filenames, captions)
    """
    images = []
    captions = []
    lines = text.split('\n')
    seen = set()

    for i, line in enumerate(lines):
        img_match = re.search(r'!\[.*?\]\(.*?/(.*?)\)', line)
        if img_match:
            fname = img_match.group(1)
            if fname not in seen:
                seen.add(fname)
                images.append(fname)
                # Check next non-empty line for caption (italic = *text*)
                for j in range(i+1, min(i+4, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue
                    cap_match = re.match(r'^\*(.+)\*$', next_line)
                    if cap_match:
                        captions.append(cap_match.group(1))
                    break

    return images, captions


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Strip YAML frontmatter, return (meta, body)"""
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            meta_text = text[3:end]
            body = text[end+4:].strip()
            meta = {}
            for line in meta_text.strip().split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip()
            return meta, body
    return {}, text


def chunk_by_headings(body: str, module: str, source_file: str) -> list[dict]:
    """
    Split markdown by # / ## headings.
    Each chunk = one section with its content, images, captions, screen codes.
    """
    chunks = []
    # Split on heading lines
    heading_pattern = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)
    matches = list(heading_pattern.finditer(body))

    # Add sentinel at end
    spans = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i+1].start() if i+1 < len(matches) else len(body)
        spans.append((m.group(1), m.group(2).strip(), body[m.end():end]))

    # Also capture content before first heading
    if matches:
        pre = body[:matches[0].start()].strip()
        if pre and len(pre) > 100:
            spans.insert(0, ('#', 'Introduction', pre))

    for chunk_idx, (level, heading, content) in enumerate(spans):
        content = content.strip()
        if not content or len(content) < 30:
            continue

        images, captions = extract_images_and_captions(content)
        screen_codes = extract_screen_codes(heading + ' ' + content)

        # Clean content: remove image markdown lines, keep text
        clean_lines = []
        for line in content.split('\n'):
            if re.match(r'!\[.*?\]\(', line):
                continue  # skip raw image lines (keep captions)
            clean_lines.append(line)
        clean_content = '\n'.join(clean_lines).strip()

        # Determine section vs subsection
        section = heading if level in ('#', '##') else ''
        subsection = heading if level == '###' else ''

        # Build embedding text: module + section + first meaningful paragraph
        # Model max ~300 chars — use heading + first non-empty paragraph for best semantic signal
        first_para = ''
        for para in clean_content.split('\n\n'):
            para = para.strip()
            # Skip table rows, very short lines
            if para and len(para) > 30 and not para.startswith('|'):
                first_para = para[:150]
                break
        embed_text = f"{module}: {heading}. {clean_content}"[:4000]

        chunks.append({
            'module':         module,
            'section':        heading,
            'subsection':     subsection,
            'content':        clean_content,
            'screen_codes':   screen_codes,
            'images':         images,
            'image_captions': captions,
            'chunk_index':    chunk_idx,
            'total_chunks':   0,       # filled in after all chunks built
            'prev_section':   '',      # filled in after all chunks built
            'next_section':   '',      # filled in after all chunks built
            'prev_tail':      '',      # filled in after all chunks built
            'source_file':    source_file,
            '_embed_text':    embed_text,
        })

    # ── Continuity pass: fill prev/next/tail after all chunks known ───────────
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        chunk['total_chunks'] = total
        if i > 0:
            chunk['prev_section'] = chunks[i-1]['section']
            # last 150 chars of prev chunk content for overlap context
            chunk['prev_tail'] = chunks[i-1]['content'][-150:].strip()
        if i < total - 1:
            chunk['next_section'] = chunks[i+1]['section']

    return chunks


# ── ES ops ────────────────────────────────────────────────────────────────────

def create_index():
    r = requests.put(
        f"{ES_URL}/{INDEX}",
        json=MAPPING,
        auth=ES_AUTH,
        verify=ES_VERIFY
    )
    if r.status_code in (200, 400):
        data = r.json()
        if r.status_code == 400 and 'already exists' in str(data):
            print(f"Index '{INDEX}' already exists — skipping create.")
        else:
            print(f"Index '{INDEX}' created.")
    else:
        print(f"ERROR creating index: {r.text}")
        sys.exit(1)


def get_embedding(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    r.raise_for_status()
    return r.json()['embedding']


def index_chunk(chunk: dict, embedding: list[float]):
    doc = {k: v for k, v in chunk.items() if not k.startswith('_')}
    doc['embedding'] = embedding
    r = requests.post(
        f"{ES_URL}/{INDEX}/_doc",
        json=doc,
        auth=ES_AUTH,
        verify=ES_VERIFY
    )
    if r.status_code not in (200, 201):
        print(f"  ERROR indexing chunk: {r.text[:200]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import urllib3
    urllib3.disable_warnings()

    print("=== GRP Manual Loader ===\n")

    # 1. Create index
    create_index()

    # 2. Find all MD files
    md_files = list(MD_DIR.glob("*.md"))
    print(f"Found {len(md_files)} markdown files.\n")

    total_chunks = 0
    total_errors = 0

    for md_file in sorted(md_files):
        module = extract_module_name(md_file.name)
        print(f"[{module}] Processing {md_file.name} ...")

        text = md_file.read_text(encoding='utf-8', errors='replace')
        _, body = parse_frontmatter(text)
        chunks = chunk_by_headings(body, module, md_file.name)

        print(f"  → {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            try:
                embedding = get_embedding(chunk['_embed_text'])
                index_chunk(chunk, embedding)
                total_chunks += 1
                print(f"  [{i+1}/{len(chunks)}] ✓ {chunk['section'][:60]}")
            except Exception as e:
                total_errors += 1
                print(f"  [{i+1}/{len(chunks)}] ✗ ERROR: {e}")
                time.sleep(1)

        print()

    print(f"=== Done. {total_chunks} chunks indexed, {total_errors} errors. ===")

    # 3. Verify
    time.sleep(2)
    r = requests.get(f"{ES_URL}/{INDEX}/_count", auth=ES_AUTH, verify=False)
    print(f"Total docs in ES: {r.json().get('count', '?')}")


if __name__ == '__main__':
    main()
