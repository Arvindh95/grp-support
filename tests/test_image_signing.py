import os


def test_image_endpoint_rejects_unsigned(client):
    r = client.get("/images/foo.png")
    assert r.status_code == 403


def test_image_endpoint_rejects_bad_sig(client):
    r = client.get("/images/foo.png?sig=deadbeef&exp=9999999999")
    assert r.status_code == 403


def test_signed_url_accepted(client, tmp_path):
    import api_server as A

    # Place a real file in IMG_DIR
    os.makedirs(A.IMG_DIR, exist_ok=True)
    path = "test.png"
    full = os.path.join(A.IMG_DIR, path)
    with open(full, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
    try:
        signed = A._sign_image(path)
        # signed = "{IMG_PUBLIC_BASE}/{path}?sig=...&exp=..."
        rel = signed.split(A.IMG_PUBLIC_BASE)[-1]
        r = client.get(f"/images{rel}")
        assert r.status_code == 200
    finally:
        os.remove(full)


def test_signed_url_path_traversal_blocked(client):
    import api_server as A
    signed = A._sign_image("../../etc/passwd")
    rel = signed.split(A.IMG_PUBLIC_BASE)[-1]
    r = client.get(f"/images{rel}")
    # Either rejected as bad path (400) or not-found (404), but never 200
    assert r.status_code in (400, 404)


def test_sign_text_images_rewrites_legacy_urls():
    import api_server as A
    raw = f"See ![]({A.IMG_BASE}/x/y.png) and ({A.IMG_BASE}/z.png)."
    signed = A._sign_text_images(raw)
    assert "sig=" in signed and "exp=" in signed
    assert A.IMG_BASE not in signed.split("?")[0].rsplit("/", 1)[0] or "sig=" in signed


def test_expired_signature_rejected(client):
    import api_server as A
    import hmac, hashlib, time
    path = "foo.png"
    exp = int(time.time()) - 10
    msg = f"{path}|{exp}".encode()
    sig = hmac.new(A.JWT_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]
    r = client.get(f"/images/{path}?sig={sig}&exp={exp}")
    assert r.status_code == 403
